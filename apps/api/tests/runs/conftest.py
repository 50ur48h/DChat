"""Fixtures for the runs suite.

Against a real platform database, for the same reason the meter and the audit
hook are: the properties worth testing here are the ones the database enforces —
the CHECK constraint pairing a terminal status with a finish time, the unique
index behind idempotency, the unique ``(run_id, seq)`` behind the replay
contract, and row-level security over five new tenant tables.

Two users in one organization, always. Ownership is the access rule for
conversations (architecture 6.2 grants every role "view *own* conversations"),
and a fixture with one user could not tell a working check from an absent one.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dataagent.db import engine as engine_module
from dataagent.tenancy import session as session_module


@dataclass(frozen=True, slots=True)
class Tenant:
    """One organization, and two people in it."""

    org_id: uuid.UUID
    user_id: uuid.UUID
    #: A second member of the same organization. Same tenant, different person —
    #: which is the case row-level security cannot help with.
    other_user_id: uuid.UUID


@pytest.fixture
async def platform(
    app_database: URL, migrated_database: URL, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[URL]:
    owner = create_async_engine(migrated_database)
    app_engine = create_async_engine(app_database)
    monkeypatch.setattr(engine_module, "get_engine", lambda: owner)
    monkeypatch.setattr(
        session_module,
        "_session_factory",
        lambda: async_sessionmaker(app_engine, expire_on_commit=False, autoflush=False),
    )
    try:
        yield app_database
    finally:
        await owner.dispose()
        await app_engine.dispose()


@pytest.fixture
async def tenant(platform: URL, migrated_database: URL) -> Tenant:
    org_id, user_id, other_user_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    engine = create_async_engine(migrated_database)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            await connection.execute(
                text("INSERT INTO organizations (id, name) VALUES (:id, 'Asking')"), {"id": org_id}
            )
            for identifier, email in (
                (user_id, "asker@example.com"),
                (other_user_id, "colleague@example.com"),
            ):
                await connection.execute(
                    text("INSERT INTO users (id, external_subject, email) VALUES (:i, :s, :e)"),
                    {"i": identifier, "s": f"sub-{identifier}", "e": email},
                )
                await connection.execute(
                    text(
                        "INSERT INTO org_memberships (org_id, user_id, role) "
                        "VALUES (:org, :user, 'admin')"
                    ),
                    {"org": org_id, "user": identifier},
                )
    finally:
        await engine.dispose()
    return Tenant(org_id=org_id, user_id=user_id, other_user_id=other_user_id)
