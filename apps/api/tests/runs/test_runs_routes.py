"""Asking a question over HTTP (architecture Part 10.2).

The contract WP7.3's chat UI and WP7.2's smoke script are both written against:
**202 and a run id, not an answer**. Nothing executes a run yet — the planner
arrives in WP7.2 — so what these tests hold is the shape of the exchange and the
two rules that are easy to get wrong once something *is* executing: a retry with
the same idempotency key must not start a second run, and a colleague must not be
able to read somebody else's conversation by knowing its id.
"""

from __future__ import annotations

import json
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
    # The *user's* messages, not every message. A total counts the assistant's
    # reply too, and whether that has been written yet is a race against the run
    # this test just started — which made this fail intermittently in a
    # randomly-ordered full suite while passing alone (B-063). Idempotency is a
    # claim about what the sender created, so that is what it asserts.
    asked = [message for message in messages if message["role"] == "user"]
    assert len(asked) == 1


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
        # **B-106's route belongs in this list, not beside it.** A thread's runs
        # are the answers in it: a new way to read a conversation is a new way to
        # read somebody's questions and every number they were given.
        f"/v1/orgs/{org_id}/conversations/{conversation_id}/runs",
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


# ---------------------------------------------------------------------------
# Every run in a thread (B-106)
# ---------------------------------------------------------------------------


async def test_a_thread_lists_its_runs_oldest_first(api: Api) -> None:
    """The route the screen reads to render a card per answer (**B-106**).

    Oldest first, and the thread's own order: a screen that has to sort answers
    by guessing is one that will eventually sort them wrong.
    """
    org_id = await _org(api)
    conversation_id, first = await _ask(api, org_id)

    _, second = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/conversations/{conversation_id}/messages",
        "alice",
        {"content": "and in August?", "idempotency_key": "send-2"},
    )

    status, runs = await api.call(
        "GET", f"/v1/orgs/{org_id}/conversations/{conversation_id}/runs", "alice"
    )

    assert status == 200
    assert [run["id"] for run in runs] == [first, second["run_id"]]


async def test_a_listed_run_is_the_same_shape_the_single_route_returns(api: Api) -> None:
    """**One field added to one route and not the other** would make an answer
    look different depending on which request fetched it — and the thread is
    exactly where a reader compares two answers side by side. Both routes build
    their payload from one function, and this is what holds that true."""
    org_id = await _org(api)
    conversation_id, run_id = await _ask(api, org_id)

    _, listed = await api.call(
        "GET", f"/v1/orgs/{org_id}/conversations/{conversation_id}/runs", "alice"
    )
    _, single = await api.call("GET", f"/v1/orgs/{org_id}/runs/{run_id}", "alice")

    # Fields, not values. A run is being executed while this test reads it, so
    # `status` legitimately differs between two requests a moment apart — and
    # asserting on values here would be a flake that says nothing about shape.
    assert len(listed) == 1
    assert sorted(listed[0]) == sorted(single)
    assert listed[0]["id"] == single["id"]


async def test_a_thread_does_not_list_another_thread_s_runs(api: Api) -> None:
    org_id = await _org(api)
    mine, my_run = await _ask(api, org_id)

    status, other = await api.call(
        "POST", f"/v1/orgs/{org_id}/conversations", "alice", {"title": "Something else"}
    )
    assert status == 201
    await api.call(
        "POST",
        f"/v1/orgs/{org_id}/conversations/{other['id']}/messages",
        "alice",
        {"content": "unrelated", "idempotency_key": "send-other"},
    )

    _, runs = await api.call("GET", f"/v1/orgs/{org_id}/conversations/{mine}/runs", "alice")

    assert [run["id"] for run in runs] == [my_run]


async def test_the_runs_of_a_conversation_that_does_not_exist_are_not_found(api: Api) -> None:
    org_id = await _org(api)

    status, _ = await api.call(
        "GET", f"/v1/orgs/{org_id}/conversations/{uuid.uuid4()}/runs", "alice"
    )

    assert status == 404


# ---------------------------------------------------------------------------
# The database the *organization* asks questions of (D-045)
# ---------------------------------------------------------------------------


async def test_a_conversation_is_stamped_with_the_organizations_choice(api: Api) -> None:
    """The reachability proof: a member names nothing and still gets the database.

    **This drives the live path and asserts at the far end of it.** An Admin sets
    the choice through the route; a member POSTs a conversation with an empty
    body; and what is asserted is `data_source_id` on the **conversation the API
    returns**, then on both read routes — not on a service return value and not
    on the organization row. B-133 is why: `answered` had a passing test on the
    outcome object, which is the one thing the product cannot look at, and it
    stayed green for as long as the screen contradicted it.

    So this goes red if the column stops being written, if `create_conversation`
    stops reading it, or if `ConversationOut` stops carrying it — any one of
    which breaks the feature, and none of which a test on an intermediate value
    would notice.
    """
    org_id = await _org_with_a_reader(api)
    data_source_id = await _register_source(org_id, "Pizza (PostgreSQL)")

    status, chosen = await api.call(
        "PUT",
        f"/v1/orgs/{org_id}/active-data-source",
        "alice",
        {"data_source_id": str(data_source_id)},
    )
    assert status == 200
    assert chosen["data_source_name"] == "Pizza (PostgreSQL)"

    # Bob is a Reader and names nothing — the flow this work package exists for.
    status, conversation = await api.call("POST", f"/v1/orgs/{org_id}/conversations", "bob", {})

    assert status == 201
    assert conversation["data_source_id"] == str(data_source_id)
    assert conversation["data_source_name"] == "Pizza (PostgreSQL)"

    _, detail = await api.call(
        "GET", f"/v1/orgs/{org_id}/conversations/{conversation['id']}", "bob"
    )
    assert detail["data_source_id"] == str(data_source_id)

    _, listed = await api.call("GET", f"/v1/orgs/{org_id}/conversations", "bob")
    assert listed[0]["data_source_id"] == str(data_source_id)


async def test_a_later_change_does_not_repoint_a_thread_that_already_exists(api: Api) -> None:
    """The property that keeps D-022 whole while D-045 moves the choice.

    A thread is about one database. Stamping at creation rather than resolving at
    each run is what makes that true across an Admin changing their mind: the old
    thread keeps the source its answers were drawn from, and its follow-up
    questions still reach the same database as the question they follow.
    """
    org_id = await _org(api)
    first = await _register_source(org_id, "Pizza (PostgreSQL)")
    second = await _register_source(org_id, "Warehouse (PostgreSQL)")

    await api.call(
        "PUT", f"/v1/orgs/{org_id}/active-data-source", "alice", {"data_source_id": str(first)}
    )
    _, thread = await api.call("POST", f"/v1/orgs/{org_id}/conversations", "alice", {})
    assert thread["data_source_id"] == str(first)

    await api.call(
        "PUT", f"/v1/orgs/{org_id}/active-data-source", "alice", {"data_source_id": str(second)}
    )

    _, unchanged = await api.call("GET", f"/v1/orgs/{org_id}/conversations/{thread['id']}", "alice")
    assert unchanged["data_source_id"] == str(first), (
        "an Admin's later choice must not re-point a thread that already ran"
    )

    _, fresh = await api.call("POST", f"/v1/orgs/{org_id}/conversations", "alice", {})
    assert fresh["data_source_id"] == str(second), "a new thread gets the new choice"


async def test_naming_a_source_still_beats_the_organizations_choice(api: Api) -> None:
    """D-045 fills a blank. It does not override a caller who said what they meant."""
    org_id = await _org(api)
    default = await _register_source(org_id, "Pizza (PostgreSQL)")
    other = await _register_source(org_id, "Warehouse (PostgreSQL)")

    await api.call(
        "PUT", f"/v1/orgs/{org_id}/active-data-source", "alice", {"data_source_id": str(default)}
    )

    _, conversation = await api.call(
        "POST", f"/v1/orgs/{org_id}/conversations", "alice", {"data_source_id": str(other)}
    )

    assert conversation["data_source_id"] == str(other)


async def test_clearing_the_choice_returns_a_conversation_to_naming_none(api: Api) -> None:
    """Null is a real value here, which is why the route is a PUT.

    An organization whose Admin clears the choice is back in the state every
    organization was in before revision 0031 — and that state still works.
    """
    org_id = await _org(api)
    data_source_id = await _register_source(org_id, "Pizza (PostgreSQL)")
    await api.call(
        "PUT",
        f"/v1/orgs/{org_id}/active-data-source",
        "alice",
        {"data_source_id": str(data_source_id)},
    )

    status, cleared = await api.call(
        "PUT", f"/v1/orgs/{org_id}/active-data-source", "alice", {"data_source_id": None}
    )
    assert status == 200
    assert cleared["data_source_id"] is None
    assert cleared["data_source_name"] is None

    _, conversation = await api.call("POST", f"/v1/orgs/{org_id}/conversations", "alice", {})
    assert conversation["data_source_id"] is None


async def test_the_choice_cannot_be_another_organizations_database(api: Api) -> None:
    """The check the foreign key cannot make — the same one D-022 needed.

    A constraint check does not consult row-level security, so Globex's source id
    satisfies the database perfectly well from inside Acme. Pointing an entire
    organization at another tenant's data is the worst version of this mistake:
    every question every member asks would be answered, confidently and with
    citations, from somebody else's database.
    """
    acme = await _org(api)
    _, globex = await api.call("POST", "/v1/orgs", "alice", {"name": "Globex"})
    theirs = await _register_source(str(globex["org_id"]), "Globex warehouse")

    status, _ = await api.call(
        "PUT", f"/v1/orgs/{acme}/active-data-source", "alice", {"data_source_id": str(theirs)}
    )

    assert status == 404

    _, still = await api.call("GET", f"/v1/orgs/{acme}/active-data-source", "alice")
    assert still["data_source_id"] is None, "a refused choice must not have been written"


async def test_a_source_that_does_not_exist_is_refused(api: Api) -> None:
    org_id = await _org(api)

    status, _ = await api.call(
        "PUT",
        f"/v1/orgs/{org_id}/active-data-source",
        "alice",
        {"data_source_id": str(uuid.uuid4())},
    )

    assert status == 404


async def test_only_an_admin_may_choose_and_every_member_may_read(api: Api) -> None:
    """B-008's rule, at the API end of it.

    Bob is a Reader. He must be refused the choice — and he must be *allowed* the
    read, because the chat screen has to know whether asking is possible before
    it offers him a composer, and a screen that cannot ask has to guess.
    """
    org_id = await _org_with_a_reader(api)
    data_source_id = await _register_source(org_id, "Pizza (PostgreSQL)")
    await api.call(
        "PUT",
        f"/v1/orgs/{org_id}/active-data-source",
        "alice",
        {"data_source_id": str(data_source_id)},
    )

    refused, _ = await api.call(
        "PUT", f"/v1/orgs/{org_id}/active-data-source", "bob", {"data_source_id": None}
    )
    assert refused == 403

    allowed, seen = await api.call("GET", f"/v1/orgs/{org_id}/active-data-source", "bob")
    assert allowed == 200
    assert seen["data_source_name"] == "Pizza (PostgreSQL)"

    # And the refusal changed nothing.
    _, unchanged = await api.call("GET", f"/v1/orgs/{org_id}/active-data-source", "alice")
    assert unchanged["data_source_id"] == str(data_source_id)


async def test_removing_the_chosen_source_leaves_the_organization_choosing_none(api: Api) -> None:
    """`ON DELETE SET NULL`, which is the reason this is a column and not JSON.

    The migration claims a removed source degrades the pointer to "none chosen" —
    a state the resolver already handles — rather than to an id that resolves to
    nothing. This is that claim, proven rather than asserted in a docstring.
    """
    from sqlalchemy import text as sql_text

    from dataagent.tenancy.session import org_session

    org_id = await _org(api)
    data_source_id = await _register_source(org_id, "Pizza (PostgreSQL)")
    await api.call(
        "PUT",
        f"/v1/orgs/{org_id}/active-data-source",
        "alice",
        {"data_source_id": str(data_source_id)},
    )

    async with org_session(uuid.UUID(org_id)) as session:
        await session.execute(
            sql_text("DELETE FROM data_sources WHERE id = :i"), {"i": data_source_id}
        )

    _, after = await api.call("GET", f"/v1/orgs/{org_id}/active-data-source", "alice")
    assert after["data_source_id"] is None
    assert after["data_source_name"] is None

    # And the organization still works: a new thread simply names none.
    status, conversation = await api.call("POST", f"/v1/orgs/{org_id}/conversations", "alice", {})
    assert status == 201
    assert conversation["data_source_id"] is None


# ---------------------------------------------------------------------------
# Whether an answer says what it cost (D-066)
# ---------------------------------------------------------------------------


async def _spent(org_id: str, run_id: str) -> None:
    """Give a run the accounting the loop would have written.

    These tests never run the loop, so `agent_runs.budget` is empty and
    `progress` is correctly `{}` — which proves nothing about the reshaping this
    is here to check. Written as the loop writes it, so the assertions are about
    the shape that reaches the browser.
    """
    from sqlalchemy import text as sql_text

    from dataagent.tenancy.session import org_session

    async with org_session(uuid.UUID(org_id)) as session:
        await session.execute(
            sql_text("UPDATE agent_runs SET budget = :budget WHERE id = :run"),
            {
                "run": uuid.UUID(run_id),
                "budget": json.dumps(
                    {
                        "limits": {
                            "tokens": 225_000,
                            "queries": 14,
                            "llm_calls": 32,
                            "iterations": 12,
                            "wall_seconds": 330.0,
                        },
                        "tokens": 80_268,
                        "queries": 8,
                        "llm_calls": 17,
                        "iterations": 6,
                        "elapsed_seconds": 210.5,
                    }
                ),
            },
        )


#: A run in one of these states is finished, and moving it again raises.
TERMINAL_FOR_TESTS = frozenset({"completed", "failed", "interrupted", "budget_exhausted"})


async def _charged_run(api: Api, org_id: str) -> str:
    """A run with one priced model call against it, through the real meter."""
    from dataagent.llm.base import Usage
    from dataagent.llm.meter import record

    _, conversation = await api.call(
        "POST", f"/v1/orgs/{org_id}/conversations", "alice", {"title": "Spend"}
    )
    _, accepted = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/conversations/{conversation['id']}/messages",
        "alice",
        {"content": "What did we sell?", "idempotency_key": "spend-1"},
    )
    run_id = str(accepted["run_id"])
    await record(
        org_id=uuid.UUID(org_id),
        run_id=uuid.UUID(run_id),
        role="sql",
        tier="strong",
        provider="openai",
        model="m-1",
        usage=Usage(input_tokens=1000, output_tokens=100),
        latency_ms=5,
    )
    # Only if the scheduler has not already taken it there. Two tests calling
    # this raced the background run to `completed` and failed on the transition
    # rather than on anything they were about — a helper that assumes it is the
    # only thing moving a run is a flake waiting for a slower machine.
    view = await service.get_run(org_id=uuid.UUID(org_id), run_id=uuid.UUID(run_id))
    if view.status not in TERMINAL_FOR_TESTS:
        if view.status == "queued":
            await service.transition(
                org_id=uuid.UUID(org_id), run_id=uuid.UUID(run_id), status="running"
            )
        await service.transition(
            org_id=uuid.UUID(org_id), run_id=uuid.UUID(run_id), status="completed"
        )
    return run_id


async def test_an_answer_says_what_it_cost_unless_the_organization_turned_it_off(
    api: Api,
) -> None:
    """**Default visible, and turning it off is a deliberate act** (owner,
    2026-08-29) — so a new organization inherits nothing it did not choose.

    The switch is asserted on the **wire**, which is the whole of D-066. Hiding
    the number in the browser leaves it in the response for anyone who opens the
    network tab, and *"not everyone should see spend"* is not a claim a CSS rule
    can make.
    """
    org_id = await _org(api)
    run_id = await _charged_run(api, org_id)

    status, before = await api.call("GET", f"/v1/orgs/{org_id}/runs/{run_id}", "alice")
    assert status == 200
    assert before["model_usage"]["calls"] == 1, "visible with nobody having chosen anything"

    status, _ = await api.call(
        "PUT", f"/v1/orgs/{org_id}/show-run-cost", "alice", {"visible": False}
    )
    assert status == 200

    status, after = await api.call("GET", f"/v1/orgs/{org_id}/runs/{run_id}", "alice")
    assert status == 200
    assert after["cost_estimate"] is None
    assert after["model_usage"] == {}
    # The answer itself is untouched: this hides spend, not the work.
    assert after["status"] == "completed"

    # And back on, because the owner asked for a switch rather than a decision.
    await api.call("PUT", f"/v1/orgs/{org_id}/show-run-cost", "alice", {"visible": True})
    status, again = await api.call("GET", f"/v1/orgs/{org_id}/runs/{run_id}", "alice")
    assert again["model_usage"]["calls"] == 1


async def test_the_thread_listing_hides_spend_too(api: Api) -> None:
    """The single-run route and the thread's list share `run_out` (B-106), and a
    switch honoured by one of them is a switch that leaks from the other — which
    is the more likely place to be read, since it is where a reader compares two
    answers."""
    org_id = await _org(api)
    run_id = await _charged_run(api, org_id)
    _, run = await api.call("GET", f"/v1/orgs/{org_id}/runs/{run_id}", "alice")
    conversation_id = run["conversation_id"]

    await api.call("PUT", f"/v1/orgs/{org_id}/show-run-cost", "alice", {"visible": False})

    status, listed = await api.call(
        "GET", f"/v1/orgs/{org_id}/conversations/{conversation_id}/runs", "alice"
    )
    assert status == 200
    assert [row["model_usage"] for row in listed] == [{}]


async def test_a_run_says_how_far_through_its_allowance_it_is(api: Api) -> None:
    """**B-177, on the wire.** A compound question now runs to five and a half
    minutes, and five minutes with no sense of progress is worse than four with
    one. The counters were already stored on every run and reached no screen.

    Counters and ceilings — never a prediction. What ends a run is a model
    deciding it has enough, and nothing here knows when that will be.
    """
    org_id = await _org(api)
    run_id = await _charged_run(api, org_id)
    await _spent(org_id, run_id)

    status, run = await api.call("GET", f"/v1/orgs/{org_id}/runs/{run_id}", "alice")

    assert status == 200
    progress = run["progress"]
    assert set(progress["limits"]) == {"iterations", "queries", "wall_seconds"}
    # **Spend is deliberately absent** (D-066). An organization can switch cost
    # off, and a progress strip reporting token counts would hand back through
    # one door what the other was closed to prevent.
    assert "tokens" not in str(progress)
    assert "llm_calls" not in str(progress)


async def test_progress_survives_spend_being_switched_off(api: Api) -> None:
    """Turning cost off must not blind a waiting person to where their question
    is. They are different questions and only one of them is about money."""
    org_id = await _org(api)
    run_id = await _charged_run(api, org_id)
    await _spent(org_id, run_id)
    await api.call("PUT", f"/v1/orgs/{org_id}/show-run-cost", "alice", {"visible": False})

    _, run = await api.call("GET", f"/v1/orgs/{org_id}/runs/{run_id}", "alice")

    assert run["cost_estimate"] is None
    assert run["progress"]["limits"]["iterations"] > 0


async def test_a_reader_cannot_turn_spend_back_on(api: Api) -> None:
    """Changing it is an Admin act and audited. Reading it is not — every member
    is subject to the switch and the settings screen has to show its state."""
    org_id = await _org_with_a_reader(api)
    await api.call("PUT", f"/v1/orgs/{org_id}/show-run-cost", "alice", {"visible": False})

    status, _ = await api.call("PUT", f"/v1/orgs/{org_id}/show-run-cost", "bob", {"visible": True})
    assert status == 403

    status, seen = await api.call("GET", f"/v1/orgs/{org_id}/show-run-cost", "bob")
    assert status == 200
    assert seen["visible"] is False
