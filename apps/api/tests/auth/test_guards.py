"""Role guards end to end, against a real database.

The role matrix from architecture Part 6.2, plus where each refusal is recorded
(DECISIONS D-008). These run through real HTTP dependencies and real SQL: the
question "does a Reader get a 403, and can an admin find out afterwards?" is not
answerable with mocks.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import URL, Row, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dataagent.auth.context import RequestContext
from dataagent.auth.guards import require_admin, require_contributor, require_member
from dataagent.auth.jwt_validator import TokenValidator
from dataagent.auth.principal import Principal, TokenError
from dataagent.db import engine as engine_module
from dataagent.tenancy import session as session_module

SUBJECT = "idp-subject-1"


class _StubValidator(TokenValidator):
    """Maps a bare token string to a principal, so these tests are about roles."""

    def __init__(self) -> None:
        pass

    async def validate(self, token: str) -> Principal:
        # S105 sees a literal compared against something called "token" and
        # assumes a credential. Here the token *is* the subject name.
        if token == "invalid":  # noqa: S105
            raise TokenError("bad_signature", "nope")
        return Principal(subject=token, email=f"{token}@example.com")


@pytest.fixture
async def wired(
    app_database: URL, migrated_database: URL, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[FastAPI, uuid.UUID]]:
    """An app whose guards, sessions and audit writes all hit the test database."""
    owner = create_async_engine(migrated_database)
    app_engine = create_async_engine(app_database)
    factory = async_sessionmaker(app_engine, expire_on_commit=False, autoflush=False)

    monkeypatch.setattr(engine_module, "get_engine", lambda: owner)
    monkeypatch.setattr(session_module, "_session_factory", lambda: factory)

    org_id = uuid.uuid4()
    async with owner.begin() as connection:
        await connection.execute(
            text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
        )
        await connection.execute(
            text("INSERT INTO organizations (id, name) VALUES (:id, 'Org')"), {"id": org_id}
        )

    app = FastAPI()
    app.state.token_validator = _StubValidator()

    # Registered by the decorator; pyright cannot see that as a use.
    @app.get("/v1/orgs/{org_id}/ask")
    async def ask(  # pyright: ignore[reportUnusedFunction]
        context: Annotated[RequestContext, Depends(require_member)],
    ) -> dict[str, str]:
        return {"role": context.role}

    @app.get("/v1/orgs/{org_id}/knowledge")
    async def knowledge(  # pyright: ignore[reportUnusedFunction]
        context: Annotated[RequestContext, Depends(require_contributor)],
    ) -> dict[str, str]:
        return {"role": context.role}

    @app.get("/v1/orgs/{org_id}/data-sources")
    async def data_sources(  # pyright: ignore[reportUnusedFunction]
        context: Annotated[RequestContext, Depends(require_admin)],
    ) -> dict[str, str]:
        return {"role": context.role}

    try:
        yield app, org_id
    finally:
        await owner.dispose()
        await app_engine.dispose()


async def _add_member(url: URL, org_id: uuid.UUID, subject: str, role: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO users (id, external_subject, email) VALUES (:id, :sub, :email)"),
                {"id": user_id, "sub": subject, "email": f"{subject}@example.com"},
            )
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            await connection.execute(
                text(
                    "INSERT INTO org_memberships (org_id, user_id, role) "
                    "VALUES (:org, :user, :role)"
                ),
                {"org": org_id, "user": user_id, "role": role},
            )
    finally:
        await engine.dispose()
    return user_id


async def _rows(url: URL, statement: str, org_id: uuid.UUID | None = None) -> Sequence[Row[Any]]:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            if org_id is not None:
                await connection.execute(
                    text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
                )
            return (await connection.execute(text(statement))).fetchall()
    finally:
        await engine.dispose()


async def _get(app: FastAPI, path: str, token: str | None) -> tuple[int, dict[str, object]]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(path, headers=headers)
    return response.status_code, response.json()


# ---------------------------------------------------------------------------
# The role matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "ask", "knowledge", "admin"),
    [
        ("admin", 200, 200, 200),
        ("contributor", 200, 200, 403),
        ("reader", 200, 403, 403),
    ],
)
async def test_the_role_matrix_holds(
    wired: tuple[FastAPI, uuid.UUID],
    migrated_database: URL,
    role: str,
    ask: int,
    knowledge: int,
    admin: int,
) -> None:
    app, org_id = wired
    await _add_member(migrated_database, org_id, SUBJECT, role)

    for path, expected in (
        (f"/v1/orgs/{org_id}/ask", ask),
        (f"/v1/orgs/{org_id}/knowledge", knowledge),
        (f"/v1/orgs/{org_id}/data-sources", admin),
    ):
        status, _ = await _get(app, path, SUBJECT)
        assert status == expected, f"{role} on {path}"


# ---------------------------------------------------------------------------
# Where each refusal is recorded
# ---------------------------------------------------------------------------


async def test_a_readers_403_lands_in_their_own_orgs_audit_log(
    wired: tuple[FastAPI, uuid.UUID], migrated_database: URL, app_database: URL
) -> None:
    """The row an admin expects to find, in the organization it happened in."""
    app, org_id = wired
    user_id = await _add_member(migrated_database, org_id, SUBJECT, "reader")

    status, _ = await _get(app, f"/v1/orgs/{org_id}/data-sources", SUBJECT)
    assert status == 403

    rows = await _rows(
        app_database,
        "SELECT actor_user_id, action, object_id, details->>'reason' FROM audit_log",
        org_id=org_id,
    )

    assert len(rows) == 1
    actor, action, object_id, reason = rows[0]
    assert actor == user_id
    assert action == "auth.denied"
    assert object_id == f"GET /v1/orgs/{org_id}/data-sources"
    assert reason == "insufficient_role"


async def test_a_non_members_403_is_not_lost(
    wired: tuple[FastAPI, uuid.UUID], migrated_database: URL, app_database: URL
) -> None:
    """A known account probing an organization it does not belong to.

    There is no membership, so no tenant audit log could honestly own this row —
    it goes to the platform security log, where it is queryable by subject and by
    the organization that was attempted.
    """
    app, org_id = wired
    await _add_member(migrated_database, org_id, SUBJECT, "admin")
    stranger = "idp-subject-stranger"

    status, _ = await _get(app, f"/v1/orgs/{org_id}/ask", stranger)
    assert status == 403

    events = await _rows(
        migrated_database,
        "SELECT actor_subject, attempted_org_id, reason, route, method FROM security_events",
    )

    assert len(events) == 1
    subject, attempted, reason, route, method = events[0]
    assert subject == stranger
    assert attempted == org_id
    assert reason == "unknown_user"
    assert route == f"/v1/orgs/{org_id}/ask"
    assert method == "GET"

    # And nothing was written into the organization's own audit log.
    assert await _rows(app_database, "SELECT 1 FROM audit_log", org_id=org_id) == []


async def test_a_member_of_another_org_is_recorded_as_probing(
    wired: tuple[FastAPI, uuid.UUID], migrated_database: URL
) -> None:
    """Known user, real account, wrong tenant — the interesting case."""
    app, org_id = wired
    other_org = uuid.uuid4()
    engine = create_async_engine(migrated_database)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(other_org)}
            )
            await connection.execute(
                text("INSERT INTO organizations (id, name) VALUES (:id, 'Other')"),
                {"id": other_org},
            )
    finally:
        await engine.dispose()
    await _add_member(migrated_database, other_org, SUBJECT, "admin")

    status, _ = await _get(app, f"/v1/orgs/{org_id}/ask", SUBJECT)
    assert status == 403

    events = await _rows(
        migrated_database, "SELECT actor_subject, attempted_org_id, reason FROM security_events"
    )

    assert len(events) == 1
    assert events[0][0] == SUBJECT
    assert events[0][1] == org_id
    assert events[0][2] == "not_a_member"


async def test_an_unauthenticated_request_writes_no_row_anywhere(
    wired: tuple[FastAPI, uuid.UUID], migrated_database: URL, app_database: URL
) -> None:
    """401s are logged, never stored.

    Taking the organization from the URL would let an unauthenticated caller
    choose whose audit log to fill up.
    """
    app, org_id = wired
    await _add_member(migrated_database, org_id, SUBJECT, "admin")

    missing, _ = await _get(app, f"/v1/orgs/{org_id}/ask", None)
    bad, _ = await _get(app, f"/v1/orgs/{org_id}/ask", "invalid")

    assert missing == 401
    assert bad == 401
    assert await _rows(migrated_database, "SELECT 1 FROM security_events") == []
    assert await _rows(app_database, "SELECT 1 FROM audit_log", org_id=org_id) == []


async def test_a_401_says_nothing_about_why(wired: tuple[FastAPI, uuid.UUID]) -> None:
    app, org_id = wired

    _, missing = await _get(app, f"/v1/orgs/{org_id}/ask", None)
    _, bad = await _get(app, f"/v1/orgs/{org_id}/ask", "invalid")

    assert missing == bad == {"detail": "Not authenticated"}
