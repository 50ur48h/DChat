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
    """``?after=`` is the whole replay contract, and Phase 8's SSE reuses it."""
    org_id = await _org(api)
    _, run_id = await _ask(api, org_id)

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
