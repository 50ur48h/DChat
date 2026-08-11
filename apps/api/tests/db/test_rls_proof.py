"""Proof that tenant isolation is structural, not a convention.

Every test here connects as ``dataagent_app`` — the role the API actually uses —
and tries to do the thing the design forbids. They are marked ``rls_proof`` and
may never be skipped or xfailed (plan §4.4); WP1.3 asserts in CI that the marker
really ran.

The load-bearing test is ``test_unfiltered_select_returns_only_the_session_org``:
it issues a deliberately unfiltered ``SELECT *``, the exact bug a repository is
most likely to contain, and shows the database refusing to leak.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

import pytest
from sqlalchemy import URL, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from dataagent.db.models import TENANT_TABLES

pytestmark = pytest.mark.rls_proof

APP_ROLE = "dataagent_app"


async def _connect(url: URL, org_id: uuid.UUID | None = None) -> AsyncConnection:
    engine = create_async_engine(url)
    connection = await engine.connect()
    if org_id is not None:
        await connection.execute(
            text("SELECT set_config('app.org_id', :org, false)"), {"org": str(org_id)}
        )
    return connection


async def _rows(url: URL, statement: str, org_id: uuid.UUID | None = None) -> Sequence[Any]:
    connection = await _connect(url, org_id)
    try:
        result = await connection.execute(text(statement))
        return result.fetchall()
    finally:
        await connection.close()
        await connection.engine.dispose()


async def _seed_two_orgs(owner_url: URL) -> tuple[uuid.UUID, uuid.UUID]:
    """Create two organizations with a row in every tenant table.

    Written as the owner, which is also subject to FORCE RLS, so each insert sets
    ``app.org_id`` first — proving on the way in that the WITH CHECK clause is live.
    """
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    engine = create_async_engine(owner_url)
    try:
        for org_id, name in ((org_a, "Org A"), (org_b, "Org B")):
            async with engine.begin() as connection:
                await connection.execute(
                    text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
                )
                await connection.execute(
                    text("INSERT INTO organizations (id, name) VALUES (:id, :name)"),
                    {"id": org_id, "name": name},
                )
                await connection.execute(
                    text(
                        "INSERT INTO invitations (org_id, email, role, token_hash, expires_at) "
                        "VALUES (:org, :email, 'reader', :hash, now() + interval '7 days')"
                    ),
                    {"org": org_id, "email": f"{name}@example.com", "hash": uuid.uuid4().hex},
                )
                await connection.execute(
                    text("INSERT INTO audit_log (org_id, action) VALUES (:org, 'org.created')"),
                    {"org": org_id},
                )
    finally:
        await engine.dispose()
    return org_a, org_b


async def _expect_failure(
    url: URL, statement: str, *, org_id: uuid.UUID | None = None, match: str | None = None
) -> None:
    """Run one statement on its own connection and require it to be refused.

    A fresh connection per attempt matters: the first refusal aborts the
    transaction, and every later statement on it would fail with "current
    transaction is aborted" — which looks like a pass while proving nothing.
    """
    connection = await _connect(url, org_id)
    try:
        with pytest.raises(DBAPIError, match=match):
            await connection.execute(text(statement))
    finally:
        await connection.close()
        await connection.engine.dispose()


# ---------------------------------------------------------------------------
# What the role is
# ---------------------------------------------------------------------------


async def test_the_api_role_has_no_way_to_bypass_rls(app_database: URL) -> None:
    """The single most important row in pg_roles for this product."""
    rows = await _rows(
        app_database,
        "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolreplication "
        f"FROM pg_roles WHERE rolname = '{APP_ROLE}'",
    )

    assert len(rows) == 1
    attributes = rows[0]._mapping
    assert attributes["rolsuper"] is False, "the API role is a superuser"
    assert attributes["rolbypassrls"] is False, "the API role can bypass RLS"
    assert attributes["rolcreatedb"] is False
    assert attributes["rolcreaterole"] is False
    assert attributes["rolreplication"] is False


async def test_the_api_connects_as_the_application_role(app_database: URL) -> None:
    rows = await _rows(app_database, "SELECT current_user, session_user")

    assert rows[0][0] == APP_ROLE
    assert rows[0][1] == APP_ROLE


async def test_the_api_role_owns_no_tenant_table(app_database: URL) -> None:
    """Ownership is a bypass route of its own — an owner can drop the policies."""
    rows = await _rows(
        app_database,
        "SELECT tablename, tableowner FROM pg_tables WHERE schemaname = 'public'",
    )

    owners = dict(rows)
    for table in TENANT_TABLES:
        assert owners[table] != APP_ROLE, f"{table} is owned by the role that queries it"


async def test_every_tenant_table_enables_and_forces_rls(app_database: URL) -> None:
    """FORCE is the half that people forget: without it the owner is exempt."""
    rows = await _rows(
        app_database,
        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE relname IN ('organizations', 'org_memberships', 'invitations', 'audit_log')",
    )

    state = {name: (enabled, forced) for name, enabled, forced in rows}
    for table in TENANT_TABLES:
        enabled, forced = state[table]
        assert enabled, f"{table} does not have row level security enabled"
        assert forced, f"{table} does not FORCE row level security, so its owner bypasses it"


async def test_every_tenant_table_has_an_isolation_policy(app_database: URL) -> None:
    rows = await _rows(
        app_database, "SELECT tablename, policyname, qual, with_check FROM pg_policies"
    )

    policies = {table: (qual, with_check) for table, _, qual, with_check in rows}
    for table, key in TENANT_TABLES.items():
        assert table in policies, f"{table} has no RLS policy at all"
        qual, with_check = policies[table]
        assert key in qual, f"{table}'s policy does not filter on {key}"
        assert with_check is not None, f"{table}'s policy has no WITH CHECK, so inserts are free"


# ---------------------------------------------------------------------------
# What the role can do with data
# ---------------------------------------------------------------------------


async def test_unfiltered_select_returns_only_the_session_org(
    app_database: URL, migrated_database: URL
) -> None:
    """The flagship. A repository forgets its WHERE clause; nothing leaks.

    Two organizations exist with rows in every tenant table. The query below has
    no filter of any kind — it is the bug — and it still cannot see org B.
    """
    org_a, org_b = await _seed_two_orgs(migrated_database)

    for table in ("organizations", "invitations", "audit_log"):
        rows = await _rows(app_database, f"SELECT * FROM {table}", org_id=org_a)

        assert len(rows) == 1, f"{table} returned {len(rows)} rows for a single-org query"

    visible = await _rows(app_database, "SELECT id FROM organizations", org_id=org_a)
    assert [row[0] for row in visible] == [org_a]

    other = await _rows(app_database, "SELECT id FROM organizations", org_id=org_b)
    assert [row[0] for row in other] == [org_b]


async def test_naming_another_org_explicitly_still_returns_nothing(
    app_database: URL, migrated_database: URL
) -> None:
    """Even asking for it by primary key. The policy is not a default, it is a wall."""
    org_a, org_b = await _seed_two_orgs(migrated_database)

    rows = await _rows(
        app_database,
        f"SELECT * FROM organizations WHERE id = '{org_b}'",
        org_id=org_a,
    )

    assert rows == []


async def test_insert_under_another_orgs_id_is_rejected(
    app_database: URL, migrated_database: URL
) -> None:
    """WITH CHECK: you may not write rows into someone else's organization."""
    org_a, org_b = await _seed_two_orgs(migrated_database)

    connection = await _connect(app_database, org_a)
    try:
        with pytest.raises(DBAPIError, match="row-level security"):
            await connection.execute(
                text("INSERT INTO audit_log (org_id, action) VALUES (:org, 'forged')"),
                {"org": org_b},
            )
    finally:
        await connection.close()
        await connection.engine.dispose()


async def test_a_session_without_an_org_sees_nothing_and_says_so(
    app_database: URL, migrated_database: URL
) -> None:
    """Fail closed and fail loudly.

    The policy dereferences ``app.org_id``; with none set the query errors rather
    than quietly returning an empty result that could be mistaken for "no data".
    """
    await _seed_two_orgs(migrated_database)

    with pytest.raises(DBAPIError, match=r"app\.org_id"):
        await _rows(app_database, "SELECT * FROM organizations")


# ---------------------------------------------------------------------------
# What the role cannot do to the controls themselves
# ---------------------------------------------------------------------------


async def test_the_api_role_cannot_turn_rls_off(app_database: URL) -> None:
    await _expect_failure(app_database, "ALTER TABLE organizations DISABLE ROW LEVEL SECURITY")


async def test_the_api_role_cannot_drop_a_policy(app_database: URL) -> None:
    await _expect_failure(app_database, "DROP POLICY org_isolation ON organizations")


async def test_the_api_role_cannot_become_the_owner(app_database: URL, database_url: URL) -> None:
    """`SET ROLE` to the owner would undo every guarantee above."""
    await _expect_failure(app_database, f'SET ROLE "{database_url.username}"')


async def test_audit_log_is_append_only_for_the_api_role(
    app_database: URL, migrated_database: URL
) -> None:
    """History can be written and never rewritten (architecture Part 8.2)."""
    org_a, _ = await _seed_two_orgs(migrated_database)

    connection = await _connect(app_database, org_a)
    try:
        await connection.execute(
            text("INSERT INTO audit_log (org_id, action) VALUES (:org, 'allowed')"),
            {"org": org_a},
        )
        await connection.commit()
    finally:
        await connection.close()
        await connection.engine.dispose()

    for forbidden in ("UPDATE audit_log SET action = 'rewritten'", "DELETE FROM audit_log"):
        await _expect_failure(app_database, forbidden, org_id=org_a, match="permission denied")
