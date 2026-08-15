"""Asking a question over HTTP (architecture Part 10.2).

The contract WP7.3's chat UI and WP7.2's smoke script are both written against:
**202 and a run id, not an answer**. Nothing executes a run yet — the planner
arrives in WP7.2 — so what these tests hold is the shape of the exchange and the
two rules that are easy to get wrong once something *is* executing: a retry with
the same idempotency key must not start a second run, and a colleague must not be
able to read somebody else's conversation by knowing its id.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dataagent.auth.jwt_validator import TokenValidator
from dataagent.auth.principal import Principal, TokenError
from dataagent.config import Settings
from dataagent.db import engine as engine_module
from dataagent.main import create_app
from dataagent.runs import service
from dataagent.tenancy import session as session_module


class _SubjectAsToken(TokenValidator):
    def __init__(self) -> None:
        pass

    async def validate(self, token: str) -> Principal:
        if not token:
            raise TokenError("malformed", "nope")
        return Principal(subject=token, email=f"{token}@example.com", name=token.title())


class Api:
    def __init__(self, app: FastAPI) -> None:
        self._app = app

    async def call(
        self, method: str, path: str, who: str, body: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        async with AsyncClient(
            transport=ASGITransport(app=self._app), base_url="http://testserver"
        ) as client:
            response = await client.request(
                method, path, headers={"Authorization": f"Bearer {who}"}, json=body
            )
        return response.status_code, (None if response.status_code == 204 else response.json())


@pytest.fixture
async def api(
    app_database: URL, migrated_database: URL, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Api]:
    owner = create_async_engine(migrated_database)
    app_engine = create_async_engine(app_database)
    monkeypatch.setattr(engine_module, "get_engine", lambda: owner)
    monkeypatch.setattr(
        session_module,
        "_session_factory",
        lambda: async_sessionmaker(app_engine, expire_on_commit=False, autoflush=False),
    )
    app = create_app(settings=Settings(auth_mode="dev", env="ci", build_env="dev"))
    app.state.token_validator = _SubjectAsToken()
    try:
        yield Api(app)
    finally:
        await owner.dispose()
        await app_engine.dispose()


async def _org(api: Api) -> str:
    _, org = await api.call("POST", "/v1/orgs", "alice", {"name": "Acme"})
    return str(org["org_id"])


async def _org_with_a_reader(api: Api) -> str:
    """Alice's organization, with Bob in it as a Reader.

    A Reader, deliberately: the role matrix grants every role "ask questions and
    view own conversations", so Bob has to be able to ask *and* be unable to read
    Alice's, and only a real second member proves both.
    """
    org_id = await _org(api)
    _, invitation = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/invitations",
        "alice",
        {"email": "bob@example.com", "role": "reader"},
    )
    await api.call("POST", "/v1/invitations/accept", "bob", {"token": invitation["token"]})
    return org_id


async def _user_id(org_id: uuid.UUID) -> uuid.UUID:
    """The one member of a freshly created organization."""
    from sqlalchemy import text as sql_text

    from dataagent.tenancy.session import org_session

    async with org_session(org_id) as session:
        return (
            await session.execute(
                sql_text("SELECT user_id FROM org_memberships WHERE org_id = :org LIMIT 1"),
                {"org": org_id},
            )
        ).scalar_one()


async def _ask(
    api: Api,
    org_id: str,
    who: str = "alice",
    key: str = "send-1",
    content: str = "How many orders?",
) -> tuple[str, str]:
    _, conversation = await api.call("POST", f"/v1/orgs/{org_id}/conversations", who, {})
    status, accepted = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/conversations/{conversation['id']}/messages",
        who,
        {"content": content, "idempotency_key": key},
    )
    assert status == 202, accepted
    return str(conversation["id"]), str(accepted["run_id"])


# ---------------------------------------------------------------------------
# The exchange
# ---------------------------------------------------------------------------


async def test_asking_a_question_is_accepted_not_answered(api: Api) -> None:
    org_id = await _org(api)

    status, conversation = await api.call(
        "POST", f"/v1/orgs/{org_id}/conversations", "alice", {"title": "July"}
    )
    assert status == 201
    assert conversation["title"] == "July"

    status, accepted = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/conversations/{conversation['id']}/messages",
        "alice",
        {"content": "How many orders were placed in July 2026?", "idempotency_key": "send-1"},
    )

    assert status == 202
    assert accepted["status"] == "queued"
    assert accepted["created"] is True

    status, run = await api.call("GET", f"/v1/orgs/{org_id}/runs/{accepted['run_id']}", "alice")
    assert status == 200
    assert run["status"] == "queued"
    assert run["question"] == "How many orders were placed in July 2026?"
    assert run["answer"] is None
    assert run["findings"] == []


async def test_a_retried_send_returns_the_run_that_already_exists(api: Api) -> None:
    """One tap or five, one run — which is one bill under D-019's spend ceiling."""
    org_id = await _org(api)
    conversation_id, run_id = await _ask(api, org_id)

    status, again = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/conversations/{conversation_id}/messages",
        "alice",
        {"content": "How many orders?", "idempotency_key": "send-1"},
    )

    assert status == 202
    assert again["run_id"] == run_id
    assert again["created"] is False

    _, messages = await api.call(
        "GET", f"/v1/orgs/{org_id}/conversations/{conversation_id}/messages", "alice"
    )
    assert len(messages) == 1


async def test_a_send_without_an_idempotency_key_is_refused(api: Api) -> None:
    """Required, not optional: the field only protects anyone if clients send it."""
    org_id = await _org(api)
    _, conversation = await api.call("POST", f"/v1/orgs/{org_id}/conversations", "alice", {})

    status, _ = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/conversations/{conversation['id']}/messages",
        "alice",
        {"content": "How many orders?"},
    )

    assert status == 422


async def test_the_conversation_list_carries_what_a_sidebar_needs(api: Api) -> None:
    org_id = await _org(api)
    conversation_id, run_id = await _ask(api, org_id, content="Which store sold most?")

    status, conversations = await api.call("GET", f"/v1/orgs/{org_id}/conversations", "alice")

    assert status == 200
    assert len(conversations) == 1
    assert conversations[0]["title"] == "Which store sold most?"
    assert conversations[0]["message_count"] == 1

    _, one = await api.call("GET", f"/v1/orgs/{org_id}/conversations/{conversation_id}", "alice")
    assert one["last_run_id"] == run_id


# ---------------------------------------------------------------------------
# The trace
# ---------------------------------------------------------------------------


async def test_the_trace_polls_from_where_the_client_left_off(api: Api) -> None:
    """``?after=`` is the whole replay contract, and Phase 8's SSE reuses it.

    The run is created through the service rather than the route, so that nothing
    schedules it: from WP7.2c a posted message starts a background run, and this
    test is about the polling contract rather than about what a run does. Driving
    the transitions by hand is what keeps the sequence under the test's control.
    """
    org_id = await _org(api)
    _, conversation = await api.call("POST", f"/v1/orgs/{org_id}/conversations", "alice", {})
    asked = await service.post_message(
        org_id=uuid.UUID(org_id),
        user_id=await _user_id(uuid.UUID(org_id)),
        conversation_id=uuid.UUID(str(conversation["id"])),
        content="How many orders?",
        idempotency_key="poll-1",
    )
    run_id = str(asked.run_id)

    status, empty = await api.call("GET", f"/v1/orgs/{org_id}/runs/{run_id}/events", "alice")
    assert status == 200
    assert empty["events"] == []
    # Nothing has happened, so a poll loop passing `last_seq` back must not
    # rewind to the start of a trace it has already read.
    assert empty["last_seq"] == 0

    # Driven through the service, because nothing executes a run yet: WP7.2's
    # planner is what will make these transitions on its own.
    await service.transition(org_id=uuid.UUID(org_id), run_id=uuid.UUID(run_id), status="running")
    await service.transition(org_id=uuid.UUID(org_id), run_id=uuid.UUID(run_id), status="completed")

    _, whole = await api.call("GET", f"/v1/orgs/{org_id}/runs/{run_id}/events", "alice")
    assert [event["type"] for event in whole["events"]] == ["run_started", "run_finished"]
    assert whole["last_seq"] == 2

    _, since = await api.call("GET", f"/v1/orgs/{org_id}/runs/{run_id}/events?after=1", "alice")
    assert [event["type"] for event in since["events"]] == ["run_finished"]


# ---------------------------------------------------------------------------
# Whose it is
# ---------------------------------------------------------------------------


async def test_a_reader_may_ask_their_own_questions(api: Api) -> None:
    """Every role gets this route; a Reader who cannot ask is not a Reader."""
    org_id = await _org_with_a_reader(api)

    _, run_id = await _ask(api, org_id, who="bob", key="bobs-send")

    status, run = await api.call("GET", f"/v1/orgs/{org_id}/runs/{run_id}", "bob")
    assert status == 200
    assert run["status"] == "queued"


async def test_a_colleague_cannot_read_your_conversation_even_knowing_its_id(api: Api) -> None:
    """404 rather than 403: "forbidden" would confirm the conversation exists."""
    org_id = await _org_with_a_reader(api)
    conversation_id, run_id = await _ask(api, org_id)

    for path in (
        f"/v1/orgs/{org_id}/conversations/{conversation_id}",
        f"/v1/orgs/{org_id}/conversations/{conversation_id}/messages",
        f"/v1/orgs/{org_id}/runs/{run_id}",
        f"/v1/orgs/{org_id}/runs/{run_id}/events",
    ):
        status, _ = await api.call("GET", path, "bob")
        assert status == 404, path

    status, _ = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/conversations/{conversation_id}/messages",
        "bob",
        {"content": "let me in", "idempotency_key": "intruder"},
    )
    assert status == 404

    _, bobs = await api.call("GET", f"/v1/orgs/{org_id}/conversations", "bob")
    assert bobs == []


# ---------------------------------------------------------------------------
# The database a conversation is about (D-022)
# ---------------------------------------------------------------------------


async def _register_source(org_id: str, name: str) -> uuid.UUID:
    """A data source row, written directly.

    Directly rather than through ``POST …/data-sources``, because that route
    reaches a secrets provider and a live socket to verify credentials, and none
    of what is being tested here is about either. What matters is that the row
    exists in this organization and can be named.
    """
    from sqlalchemy import text as sql_text

    from dataagent.tenancy.session import org_session

    data_source_id = uuid.uuid4()
    async with org_session(uuid.UUID(org_id)) as session:
        await session.execute(
            sql_text(
                "INSERT INTO data_sources (id, org_id, name, engine, host_display, "
                "settings, secret_ref) VALUES (:i, :o, :n, 'pg', '127.0.0.1:1/probe', "
                "'{}'::jsonb, :r)"
            ),
            {"i": data_source_id, "o": org_id, "n": name, "r": f"ds/{org_id}/{data_source_id}/c"},
        )
    return data_source_id


async def test_a_conversation_remembers_the_database_it_is_about(api: Api) -> None:
    """Named once, when the thread starts, and readable on every view of it.

    The alternative — naming a source per message — would let one thread's two
    answers come from two databases with nothing saying so.
    """
    org_id = await _org(api)
    data_source_id = await _register_source(org_id, "Pizza (PostgreSQL)")

    status, conversation = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/conversations",
        "alice",
        {"data_source_id": str(data_source_id)},
    )

    assert status == 201
    assert conversation["data_source_id"] == str(data_source_id)
    assert conversation["data_source_name"] == "Pizza (PostgreSQL)"

    # Both read routes, because the sidebar uses one and the thread uses the
    # other, and a choice visible in only one of them is half a feature.
    _, detail = await api.call(
        "GET", f"/v1/orgs/{org_id}/conversations/{conversation['id']}", "alice"
    )
    assert detail["data_source_id"] == str(data_source_id)
    assert detail["data_source_name"] == "Pizza (PostgreSQL)"

    _, listed = await api.call("GET", f"/v1/orgs/{org_id}/conversations", "alice")
    assert listed[0]["data_source_id"] == str(data_source_id)
    assert listed[0]["data_source_name"] == "Pizza (PostgreSQL)"


async def test_a_conversation_may_name_no_database_at_all(api: Api) -> None:
    """Null is the shape every conversation written before revision 0014 has.

    It is not an error and must not become one: the run still resolves an
    organization's single source, and still refuses when there is a choice.
    """
    org_id = await _org(api)

    status, conversation = await api.call("POST", f"/v1/orgs/{org_id}/conversations", "alice", {})

    assert status == 201
    assert conversation["data_source_id"] is None
    assert conversation["data_source_name"] is None


async def test_a_conversation_cannot_name_a_database_that_does_not_exist(api: Api) -> None:
    org_id = await _org(api)

    status, _ = await api.call(
        "POST", f"/v1/orgs/{org_id}/conversations", "alice", {"data_source_id": str(uuid.uuid4())}
    )

    assert status == 404


async def test_a_conversation_cannot_name_another_organizations_database(api: Api) -> None:
    """The check the foreign key cannot make.

    A constraint check does not consult row-level security, so Globex's data
    source id satisfies the database perfectly well from inside Acme. Only a read
    through the org session can tell, which is why there is an explicit lookup
    rather than a reliance on the FK. Alice is a member of both organizations,
    which is the sharper case: she is not guessing at an id, she has it.
    """
    acme = await _org(api)
    _, globex = await api.call("POST", "/v1/orgs", "alice", {"name": "Globex"})
    theirs = await _register_source(str(globex["org_id"]), "Globex warehouse")

    status, _ = await api.call(
        "POST", f"/v1/orgs/{acme}/conversations", "alice", {"data_source_id": str(theirs)}
    )

    assert status == 404

    _, conversations = await api.call("GET", f"/v1/orgs/{acme}/conversations", "alice")
    assert conversations == [], "a refused conversation must not have been written"


async def test_a_run_from_another_organization_is_not_found(api: Api) -> None:
    """The tenant boundary, over HTTP. Alice is in both, which is the sharper case."""
    acme = await _org(api)
    _, other = await api.call("POST", "/v1/orgs", "alice", {"name": "Globex"})
    _, run_id = await _ask(api, acme)

    status, _ = await api.call("GET", f"/v1/orgs/{other['org_id']}/runs/{run_id}", "alice")

    assert status == 404


async def test_a_run_that_does_not_exist_is_not_found(api: Api) -> None:
    org_id = await _org(api)

    status, _ = await api.call("GET", f"/v1/orgs/{org_id}/runs/{uuid.uuid4()}", "alice")

    assert status == 404


async def test_the_routes_need_a_token(api: Api) -> None:
    org_id = await _org(api)

    status, _ = await api.call("GET", f"/v1/orgs/{org_id}/conversations", "")

    assert status == 401
