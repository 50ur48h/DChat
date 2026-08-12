"""Proof that tenant isolation is structural, not a convention.

Every test here connects as ``dataagent_app`` — the role the API actually uses —
and tries to do the thing the design forbids. They are marked ``rls_proof`` and
may never be skipped or xfailed (plan §4.4); WP1.3 asserts in CI that the marker
really ran.

The load-bearing test is ``test_unfiltered_select_leaks_nothing_from_any_tenant_table``:
it issues a deliberately unfiltered ``SELECT *`` against every tenant table, the
exact bug a repository is most likely to contain, and shows the database
refusing to leak.

``test_no_tenant_table_can_be_added_without_protecting_it`` is what keeps this
suite honest as the schema grows: it asks the database which tables carry
``org_id`` and fails if any of them is undeclared or unprotected.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
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


@dataclass(frozen=True)
class SeededOrgs:
    """Two organizations, each with exactly one row in every tenant table."""

    a: uuid.UUID
    b: uuid.UUID
    user_id: uuid.UUID
    #: Org B's catalog chain, by id. The forged inserts need real parents: a
    #: made-up snapshot id would be refused by the foreign key and the test
    #: would pass while proving nothing about the policy.
    b_data_source: uuid.UUID
    b_snapshot: uuid.UUID
    b_table: uuid.UUID


async def _seed_two_orgs(owner_url: URL) -> SeededOrgs:
    """Populate every tenant table for two organizations.

    The *same* user belongs to both, which is the realistic case and a sharper
    test: isolation has to come from the organization, not from the user.

    Written as the owner, which FORCE RLS also subjects to the policies, so every
    insert sets ``app.org_id`` first — proving the WITH CHECK clause is live on
    the way in.
    """
    org_a, org_b, user_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    catalog: dict[str, uuid.UUID] = {}
    engine = create_async_engine(owner_url)
    try:
        async with engine.begin() as connection:
            # users is global: no org_id, no policy, one row shared by both orgs.
            await connection.execute(
                text(
                    "INSERT INTO users (id, external_subject, email) VALUES (:id, :subject, :email)"
                ),
                {"id": user_id, "subject": f"sub-{user_id}", "email": "person@example.com"},
            )

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
                        "INSERT INTO org_memberships (org_id, user_id, role) "
                        "VALUES (:org, :user, 'admin')"
                    ),
                    {"org": org_id, "user": user_id},
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
                data_source_id = (
                    await connection.execute(
                        text(
                            "INSERT INTO data_sources "
                            "(org_id, name, engine, host_display, secret_ref) VALUES "
                            "(:org, :name, 'pg', 'db.example:5432/pizza', :ref) RETURNING id"
                        ),
                        {
                            "org": org_id,
                            "name": f"{name} source",
                            "ref": f"ds/{org_id}/x/credentials",
                        },
                    )
                ).scalar_one()

                # The catalog chain (WP4.1): a snapshot, one table in it, one
                # column of that table, and one relationship. Each hangs off the
                # one above by id, so this is also a small proof that the
                # foreign keys and the policies agree about what a tenant owns.
                snapshot_id = (
                    await connection.execute(
                        text(
                            "INSERT INTO catalog_snapshots "
                            "(org_id, data_source_id, version, status) VALUES "
                            "(:org, :ds, 1, 'active') RETURNING id"
                        ),
                        {"org": org_id, "ds": data_source_id},
                    )
                ).scalar_one()
                table_id = (
                    await connection.execute(
                        text(
                            "INSERT INTO catalog_tables "
                            "(org_id, snapshot_id, schema_name, table_name, kind, "
                            "structural_hash) VALUES "
                            "(:org, :snap, 'public', 'orders', 'table', :hash) RETURNING id"
                        ),
                        {"org": org_id, "snap": snapshot_id, "hash": uuid.uuid4().hex},
                    )
                ).scalar_one()
                await connection.execute(
                    text(
                        "INSERT INTO catalog_columns "
                        "(org_id, table_id, name, ordinal, data_type, nullable) VALUES "
                        "(:org, :table, 'id', 1, 'integer', false)"
                    ),
                    {"org": org_id, "table": table_id},
                )
                await connection.execute(
                    text(
                        "INSERT INTO catalog_relationships "
                        "(org_id, snapshot_id, constraint_name, from_schema, from_table, "
                        "from_columns, to_schema, to_table, to_columns) VALUES "
                        "(:org, :snap, 'fk_orders_store', 'public', 'orders', "
                        "ARRAY['store_id'], 'public', 'stores', ARRAY['id'])"
                    ),
                    {"org": org_id, "snap": snapshot_id},
                )
                if org_id == org_b:
                    catalog.update(data_source=data_source_id, snapshot=snapshot_id, table=table_id)
    finally:
        await engine.dispose()
    return SeededOrgs(
        a=org_a,
        b=org_b,
        user_id=user_id,
        b_data_source=catalog["data_source"],
        b_snapshot=catalog["snapshot"],
        b_table=catalog["table"],
    )


def _forged_insert(table: str, seeded: SeededOrgs) -> str:
    """A row that belongs to somebody else, written as SQL for one table.

    ``organizations`` is scoped by its own ``id``, so "somebody else's row" there
    means any organization that is not the one this session is scoped to — which
    is also why bootstrap must set ``app.org_id`` to the new id before inserting.
    """
    other_org, user_id = seeded.b, seeded.user_id
    statements = {
        "organizations": f"INSERT INTO organizations (id, name) VALUES ('{other_org}', 'forged')",
        "org_memberships": (
            "INSERT INTO org_memberships (org_id, user_id, role) "
            f"VALUES ('{other_org}', '{user_id}', 'admin')"
        ),
        "invitations": (
            "INSERT INTO invitations (org_id, email, role, token_hash, expires_at) VALUES "
            f"('{other_org}', 'forged@example.com', 'reader', '{uuid.uuid4().hex}', "
            "now() + interval '7 days')"
        ),
        "audit_log": f"INSERT INTO audit_log (org_id, action) VALUES ('{other_org}', 'forged')",
        "data_sources": (
            "INSERT INTO data_sources (org_id, name, engine, host_display, secret_ref) VALUES "
            f"('{other_org}', 'forged', 'pg', 'db.example:5432/pizza', "
            f"'ds/{other_org}/forged/credentials')"
        ),
        # The catalog chain names real parents belonging to the other
        # organization, so nothing here can be refused by a foreign key and pass
        # for the wrong reason: the only thing standing in the way is the policy.
        "catalog_snapshots": (
            "INSERT INTO catalog_snapshots (org_id, data_source_id, version, status) VALUES "
            f"('{other_org}', '{seeded.b_data_source}', 99, 'building')"
        ),
        "catalog_tables": (
            "INSERT INTO catalog_tables "
            "(org_id, snapshot_id, schema_name, table_name, kind, structural_hash) VALUES "
            f"('{other_org}', '{seeded.b_snapshot}', 'public', 'forged', 'table', "
            f"'{uuid.uuid4().hex}')"
        ),
        "catalog_columns": (
            "INSERT INTO catalog_columns (org_id, table_id, name, ordinal, data_type, nullable) "
            f"VALUES ('{other_org}', '{seeded.b_table}', 'forged', 1, 'text', true)"
        ),
        "catalog_relationships": (
            "INSERT INTO catalog_relationships "
            "(org_id, snapshot_id, constraint_name, from_schema, from_table, from_columns, "
            "to_schema, to_table, to_columns) VALUES "
            f"('{other_org}', '{seeded.b_snapshot}', 'forged', 'public', 'a', ARRAY['x'], "
            "'public', 'b', ARRAY['y'])"
        ),
    }
    return statements[table]


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


_TABLES_WITH_ORG_ID = (
    "SELECT c.relname FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "JOIN pg_attribute a ON a.attrelid = c.oid "
    "WHERE n.nspname = 'public' AND c.relkind = 'r' "
    "AND a.attname = 'org_id' AND a.attnum > 0 AND NOT a.attisdropped"
)

_TABLES_WITH_ENFORCED_RLS = (
    "SELECT c.relname FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "JOIN pg_policies p ON p.tablename = c.relname AND p.schemaname = n.nspname "
    "WHERE n.nspname = 'public' AND c.relrowsecurity AND c.relforcerowsecurity"
)


async def _names(url: URL, statement: str) -> set[str]:
    return {row[0] for row in await _rows(url, statement)}


# ---------------------------------------------------------------------------
# What the role is
# ---------------------------------------------------------------------------


async def test_no_tenant_table_can_be_added_without_protecting_it(app_database: URL) -> None:
    """The guard that keeps this suite honest as the schema grows.

    Plan §6 WP1.3 requires that every tenant table added in any later phase
    extends this proof. Relying on someone remembering is not a control, so this
    asks the database instead: any table carrying ``org_id`` must be declared in
    ``TENANT_TABLES`` and must already have RLS enabled, forced, and a policy.

    Phase 4 adds catalog tables, Phase 7 adds runs and events, Phase 10 adds
    documents. Each of those will fail here until it is protected — which is the
    point.
    """
    carrying_org_id = await _names(app_database, _TABLES_WITH_ORG_ID)
    declared = {table for table, key in TENANT_TABLES.items() if key == "org_id"}

    assert carrying_org_id == declared, (
        "tables with an org_id column disagree with TENANT_TABLES. Undeclared "
        f"tables are unprotected: {sorted(carrying_org_id - declared)}; declared "
        f"but missing from the database: {sorted(declared - carrying_org_id)}"
    )

    protected = await _names(app_database, _TABLES_WITH_ENFORCED_RLS)

    assert set(TENANT_TABLES) <= protected, (
        f"declared tenant tables without enforced RLS: {sorted(set(TENANT_TABLES) - protected)}"
    )


async def test_the_guard_above_actually_detects_an_unprotected_table(
    app_database: URL, migrated_database: URL
) -> None:
    """A guard that has never failed is a guard nobody has tested.

    Create the exact mistake a later phase might make — a tenant table with an
    ``org_id`` and no policy — and show that the query the guard uses reports it
    as both undeclared and unprotected.
    """
    engine = create_async_engine(migrated_database)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE TABLE rogue_findings ("
                    "  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
                    "  org_id uuid NOT NULL,"
                    "  statement text"
                    ")"
                )
            )
    finally:
        await engine.dispose()

    carrying_org_id = await _names(app_database, _TABLES_WITH_ORG_ID)
    protected = await _names(app_database, _TABLES_WITH_ENFORCED_RLS)

    assert "rogue_findings" in carrying_org_id, "the guard cannot see a new tenant table"
    assert "rogue_findings" not in set(TENANT_TABLES), "fixture leaked into the declaration"
    assert "rogue_findings" not in protected, "an unprotected table looked protected"

    # And therefore the guard's own assertion would have failed:
    assert carrying_org_id != {table for table, key in TENANT_TABLES.items() if key == "org_id"}


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
    # Built from TENANT_TABLES rather than written out: a hand-kept list here
    # would go stale the first time a phase adds a table, and would do so
    # silently — the loop below would simply not look at the new one.
    named = ", ".join(f"'{table}'" for table in TENANT_TABLES)
    rows = await _rows(
        app_database,
        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
        f"WHERE relname IN ({named})",
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


async def test_unfiltered_select_leaks_nothing_from_any_tenant_table(
    app_database: URL, migrated_database: URL
) -> None:
    """The M1 acceptance test, across every tenant table.

    Two organizations hold one row each in all four tables. The queries below
    carry no filter of any kind — they are the bug a repository is most likely to
    contain — and each still returns exactly its own organization's row.

    Looping rather than parametrising keeps this to one migrated database instead
    of four; the assertion messages name the table, so a failure is still precise.
    """
    seeded = await _seed_two_orgs(migrated_database)

    for table in TENANT_TABLES:
        for org_id in (seeded.a, seeded.b):
            rows = await _rows(app_database, f"SELECT * FROM {table}", org_id=org_id)

            assert len(rows) == 1, (
                f"{table}: an unfiltered SELECT returned {len(rows)} rows while scoped "
                f"to one organization — isolation is not holding"
            )


async def test_the_visible_row_is_the_right_one(app_database: URL, migrated_database: URL) -> None:
    """Not merely "one row", but *that* organization's row."""
    seeded = await _seed_two_orgs(migrated_database)

    for org_id in (seeded.a, seeded.b):
        organizations = await _rows(app_database, "SELECT id FROM organizations", org_id=org_id)
        memberships = await _rows(app_database, "SELECT org_id FROM org_memberships", org_id=org_id)

        assert [row[0] for row in organizations] == [org_id]
        assert [row[0] for row in memberships] == [org_id]


async def test_naming_another_org_explicitly_still_returns_nothing(
    app_database: URL, migrated_database: URL
) -> None:
    """Even asking for it by primary key. The policy is not a default, it is a wall."""
    seeded = await _seed_two_orgs(migrated_database)

    rows = await _rows(
        app_database,
        f"SELECT * FROM organizations WHERE id = '{seeded.b}'",
        org_id=seeded.a,
    )

    assert rows == []


async def test_insert_under_another_org_is_rejected_on_every_tenant_table(
    app_database: URL, migrated_database: URL
) -> None:
    """WITH CHECK, everywhere: you may not write into somebody else's organization."""
    seeded = await _seed_two_orgs(migrated_database)

    for table in TENANT_TABLES:
        await _expect_failure(
            app_database,
            _forged_insert(table, seeded),
            org_id=seeded.a,
            match="row-level security",
        )


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
    seeded = await _seed_two_orgs(migrated_database)

    connection = await _connect(app_database, seeded.a)
    try:
        await connection.execute(
            text("INSERT INTO audit_log (org_id, action) VALUES (:org, 'allowed')"),
            {"org": seeded.a},
        )
        await connection.commit()
    finally:
        await connection.close()
        await connection.engine.dispose()

    for forbidden in ("UPDATE audit_log SET action = 'rewritten'", "DELETE FROM audit_log"):
        await _expect_failure(app_database, forbidden, org_id=seeded.a, match="permission denied")
