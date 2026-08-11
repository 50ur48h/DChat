"""Signup → org → invite → accept, across two organizations.

The M2 flow, run over real HTTP against a real database. Two orgs exist
throughout so that every assertion about what someone can see is also an
assertion about what they cannot.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import URL, Row, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dataagent.auth.jwt_validator import TokenValidator
from dataagent.auth.principal import Principal, TokenError
from dataagent.config import Settings
from dataagent.db import engine as engine_module
from dataagent.main import create_app
from dataagent.tenancy import session as session_module


class _SubjectAsToken(TokenValidator):
    """The bearer token *is* the subject, so these tests are about the flow."""

    def __init__(self) -> None:
        pass

    async def validate(self, token: str) -> Principal:
        if not token or token == "invalid":  # noqa: S105
            raise TokenError("bad_signature", "nope")
        return Principal(subject=token, email=f"{token}@example.com", name=token.title())


class Api:
    def __init__(self, app: FastAPI) -> None:
        self._app = app

    async def call(
        self, method: str, path: str, who: str | None = None, body: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        headers = {"Authorization": f"Bearer {who}"} if who else {}
        async with AsyncClient(
            transport=ASGITransport(app=self._app), base_url="http://testserver"
        ) as client:
            response = await client.request(method, path, headers=headers, json=body)
        payload = None if response.status_code == 204 else response.json()
        return response.status_code, payload


@pytest.fixture
async def api(
    app_database: URL, migrated_database: URL, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Api]:
    owner = create_async_engine(migrated_database)
    app_engine = create_async_engine(app_database)
    factory = async_sessionmaker(app_engine, expire_on_commit=False, autoflush=False)

    monkeypatch.setattr(engine_module, "get_engine", lambda: owner)
    monkeypatch.setattr(session_module, "_session_factory", lambda: factory)

    app = create_app(settings=Settings(auth_mode="dev", env="ci", build_env="dev"))
    app.state.token_validator = _SubjectAsToken()

    try:
        yield Api(app)
    finally:
        await owner.dispose()
        await app_engine.dispose()


async def _audit(url: URL, org_id: uuid.UUID) -> Sequence[Row[Any]]:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            rows = await connection.execute(
                text("SELECT action, object_type, details FROM audit_log ORDER BY id")
            )
            return rows.fetchall()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------


async def test_me_works_before_you_belong_to_anything(api: Api) -> None:
    """Bootstrap has to start somewhere: a first login with no organization."""
    status, body = await api.call("GET", "/v1/me", who="alice")

    assert status == 200
    assert body["subject"] == "alice"
    assert body["memberships"] == []


async def test_the_full_signup_invite_accept_flow(api: Api, app_database: URL) -> None:
    # 1. Alice signs up and creates an organization; she becomes its Admin.
    status, org = await api.call("POST", "/v1/orgs", who="alice", body={"name": "Acme"})
    assert status == 201
    assert org["role"] == "admin"
    org_id = uuid.UUID(org["org_id"])

    # 2. Bob exists but belongs to nothing, and cannot see Acme's members.
    _, bob_me = await api.call("GET", "/v1/me", who="bob")
    assert bob_me["memberships"] == []
    denied, _ = await api.call("GET", f"/v1/orgs/{org_id}/members", who="bob")
    assert denied == 403

    # 3. Alice invites Bob as a Reader.
    status, invitation = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/invitations",
        who="alice",
        body={"email": "bob@example.com", "role": "reader"},
    )
    assert status == 201
    token = invitation["token"]

    # 4. Bob redeems it and is now a Reader.
    status, accepted = await api.call(
        "POST", "/v1/invitations/accept", who="bob", body={"token": token}
    )
    assert status == 200
    assert accepted["role"] == "reader"
    assert accepted["org_name"] == "Acme"

    # 5. Which shows up everywhere it should.
    _, bob_me = await api.call("GET", "/v1/me", who="bob")
    assert [(m["org_name"], m["role"]) for m in bob_me["memberships"]] == [("Acme", "reader")]

    status, members = await api.call("GET", f"/v1/orgs/{org_id}/members", who="bob")
    assert status == 200
    assert sorted(m["role"] for m in members) == ["admin", "reader"]

    # 6. And the whole story is in the organization's audit log.
    actions = [row[0] for row in await _audit(app_database, org_id)]
    assert actions == ["org.created", "invitation.created", "invitation.accepted"]


async def test_a_reader_cannot_do_admin_things(api: Api) -> None:
    """The M2 acceptance criterion, at the route level."""
    _, org = await api.call("POST", "/v1/orgs", who="alice", body={"name": "Acme"})
    org_id = uuid.UUID(org["org_id"])
    _, invitation = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/invitations",
        who="alice",
        body={"email": "bob@example.com", "role": "reader"},
    )
    await api.call("POST", "/v1/invitations/accept", who="bob", body={"token": invitation["token"]})

    status, _ = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/invitations",
        who="bob",
        body={"email": "carol@example.com", "role": "reader"},
    )

    assert status == 403


async def test_an_invitation_cannot_be_redeemed_twice(api: Api) -> None:
    _, org = await api.call("POST", "/v1/orgs", who="alice", body={"name": "Acme"})
    org_id = uuid.UUID(org["org_id"])
    _, invitation = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/invitations",
        who="alice",
        body={"email": "bob@example.com", "role": "reader"},
    )

    first, _ = await api.call(
        "POST", "/v1/invitations/accept", who="bob", body={"token": invitation["token"]}
    )
    second, body = await api.call(
        "POST", "/v1/invitations/accept", who="carol", body={"token": invitation["token"]}
    )

    assert first == 200
    assert second == 400
    assert body["detail"] == "That invitation is not valid. Ask an admin for a new one."


async def test_an_unknown_token_fails_identically_to_a_used_one(api: Api) -> None:
    """Same message, or this becomes an oracle for guessing tokens."""
    status, body = await api.call(
        "POST", "/v1/invitations/accept", who="bob", body={"token": "not-a-real-token"}
    )

    assert status == 400
    assert body["detail"] == "That invitation is not valid. Ask an admin for a new one."


async def test_the_raw_token_is_never_stored(api: Api, migrated_database: URL) -> None:
    """Only the hash is kept, so a leaked backup hands out no invitations."""
    _, org = await api.call("POST", "/v1/orgs", who="alice", body={"name": "Acme"})
    org_id = uuid.UUID(org["org_id"])
    _, invitation = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/invitations",
        who="alice",
        body={"email": "bob@example.com", "role": "reader"},
    )
    token = invitation["token"]

    engine = create_async_engine(migrated_database)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            stored = (
                (await connection.execute(text("SELECT token_hash FROM invitations")))
                .scalars()
                .all()
            )
            audited = (
                (await connection.execute(text("SELECT details::text FROM audit_log")))
                .scalars()
                .all()
            )
    finally:
        await engine.dispose()

    assert token not in stored
    assert all(token not in row for row in audited), "the raw token reached the audit log"


# ---------------------------------------------------------------------------
# Last-admin protection
# ---------------------------------------------------------------------------


async def test_the_only_admin_cannot_demote_themselves(api: Api) -> None:
    """An organization with no Admin cannot be repaired from inside the product."""
    _, org = await api.call("POST", "/v1/orgs", who="alice", body={"name": "Acme"})
    org_id = uuid.UUID(org["org_id"])
    _, me = await api.call("GET", "/v1/me", who="alice")

    status, body = await api.call(
        "PATCH",
        f"/v1/orgs/{org_id}/members/{me['user_id']}",
        who="alice",
        body={"role": "reader"},
    )

    assert status == 409
    assert "only Admin" in body["detail"]


async def test_the_only_admin_cannot_be_removed(api: Api) -> None:
    _, org = await api.call("POST", "/v1/orgs", who="alice", body={"name": "Acme"})
    org_id = uuid.UUID(org["org_id"])
    _, me = await api.call("GET", "/v1/me", who="alice")

    status, _ = await api.call("DELETE", f"/v1/orgs/{org_id}/members/{me['user_id']}", who="alice")

    assert status == 409


async def test_an_admin_may_step_down_once_someone_else_is_admin(api: Api) -> None:
    _, org = await api.call("POST", "/v1/orgs", who="alice", body={"name": "Acme"})
    org_id = uuid.UUID(org["org_id"])
    _, invitation = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/invitations",
        who="alice",
        body={"email": "bob@example.com", "role": "admin"},
    )
    await api.call("POST", "/v1/invitations/accept", who="bob", body={"token": invitation["token"]})
    _, alice = await api.call("GET", "/v1/me", who="alice")

    status, member = await api.call(
        "PATCH",
        f"/v1/orgs/{org_id}/members/{alice['user_id']}",
        who="alice",
        body={"role": "reader"},
    )

    assert status == 200
    assert member["role"] == "reader"


# ---------------------------------------------------------------------------
# Two organizations
# ---------------------------------------------------------------------------


async def test_an_invitation_only_admits_you_to_its_own_organization(api: Api) -> None:
    _, acme = await api.call("POST", "/v1/orgs", who="alice", body={"name": "Acme"})
    _, globex = await api.call("POST", "/v1/orgs", who="dave", body={"name": "Globex"})
    acme_id, globex_id = uuid.UUID(acme["org_id"]), uuid.UUID(globex["org_id"])

    _, invitation = await api.call(
        "POST",
        f"/v1/orgs/{acme_id}/invitations",
        who="alice",
        body={"email": "bob@example.com", "role": "reader"},
    )
    await api.call("POST", "/v1/invitations/accept", who="bob", body={"token": invitation["token"]})

    inside, _ = await api.call("GET", f"/v1/orgs/{acme_id}/members", who="bob")
    outside, _ = await api.call("GET", f"/v1/orgs/{globex_id}/members", who="bob")

    assert inside == 200
    assert outside == 403


async def test_each_orgs_audit_log_holds_only_its_own_events(api: Api, app_database: URL) -> None:
    _, acme = await api.call("POST", "/v1/orgs", who="alice", body={"name": "Acme"})
    _, globex = await api.call("POST", "/v1/orgs", who="dave", body={"name": "Globex"})

    acme_rows = await _audit(app_database, uuid.UUID(acme["org_id"]))
    globex_rows = await _audit(app_database, uuid.UUID(globex["org_id"]))

    assert [row[0] for row in acme_rows] == ["org.created"]
    assert [row[0] for row in globex_rows] == ["org.created"]
    assert acme_rows[0][2]["name"] == "Acme"
    assert globex_rows[0][2]["name"] == "Globex"
