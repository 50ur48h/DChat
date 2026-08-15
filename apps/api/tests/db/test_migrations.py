"""Migrations must go up, come back down, and go up again.

A downgrade that has never been executed is not a downgrade — it is a hope. This
runs the full cycle against an empty database, which is what a rollback in Phase
12 will actually do.

These are sync tests on purpose: Alembic's env.py calls ``asyncio.run`` itself,
which would fail inside an already-running event loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import URL, Connection, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from dataagent.db import models
from dataagent.db.base import Base

EXPECTED_TABLES = {
    "organizations",
    "users",
    "org_memberships",
    "invitations",
    "audit_log",
}


async def _run_sync[T](url: URL, fn: Callable[[Connection], T]) -> T:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(fn)
    finally:
        await engine.dispose()


def read[T](url: URL, fn: Callable[[Connection], T]) -> T:
    """Run a synchronous inspection against an async engine."""
    return asyncio.run(_run_sync(url, fn))


def _table_names(url: URL) -> set[str]:
    return read(url, lambda connection: set(inspect(connection).get_table_names()))


def test_upgrade_creates_every_expected_table(alembic_config: Config, temp_database: URL) -> None:
    command.upgrade(alembic_config, "head")

    assert _table_names(temp_database) >= EXPECTED_TABLES


def test_upgrade_downgrade_upgrade_round_trip(alembic_config: Config, temp_database: URL) -> None:
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")

    remaining = _table_names(temp_database)
    assert not (EXPECTED_TABLES & remaining), f"downgrade left tables behind: {remaining}"

    command.upgrade(alembic_config, "head")

    assert _table_names(temp_database) >= EXPECTED_TABLES


def test_models_and_migrations_do_not_drift(alembic_config: Config, temp_database: URL) -> None:
    """Autogenerate against the migrated database must find nothing to do.

    This is what stops a model edit from reaching production without a migration:
    the two descriptions of the schema have to agree.
    """
    command.upgrade(alembic_config, "head")

    def skip_alembic_bookkeeping(
        _object: object, name: str | None, type_: str, *_rest: object
    ) -> bool:
        return not (type_ == "table" and name == "alembic_version")

    def diff(connection: Connection) -> list[object]:
        opts: dict[str, object] = {
            "compare_type": True,
            "include_object": skip_alembic_bookkeeping,
        }
        return compare_metadata(MigrationContext.configure(connection, opts=opts), Base.metadata)

    differences = read(temp_database, diff)

    assert differences == [], f"models and migrations disagree: {differences}"


def test_every_tenant_table_exists_and_carries_its_key(
    alembic_config: Config, temp_database: URL
) -> None:
    """The map WP1.2 builds RLS policies from must describe reality.

    A typo here would produce a policy on a column that does not exist, or worse,
    a tenant table nobody remembered to protect.
    """
    command.upgrade(alembic_config, "head")

    def columns(connection: Connection) -> dict[str, set[str]]:
        inspector = inspect(connection)
        return {
            table: {column["name"] for column in inspector.get_columns(table)}
            for table in models.TENANT_TABLES
        }

    found = read(temp_database, columns)

    for table, key in models.TENANT_TABLES.items():
        assert key in found[table], f"{table} has no {key} column to scope rows by"


def test_extensions_are_installed(alembic_config: Config, temp_database: URL) -> None:
    """pgvector has to be present from revision 0001, not bolted on in Phase 4."""
    command.upgrade(alembic_config, "head")

    def extensions(connection: Connection) -> set[str]:
        return set(connection.execute(text("SELECT extname FROM pg_extension")).scalars().all())

    installed = read(temp_database, extensions)

    assert {"pgcrypto", "vector"} <= installed


def test_there_is_exactly_one_head(alembic_config: Config) -> None:
    """Two heads mean two branches of history and an ambiguous `upgrade head`."""
    script = ScriptDirectory.from_config(alembic_config)

    assert len(script.get_heads()) == 1


# ---------------------------------------------------------------------------
# Revision 0013 rewrites data, so an empty database proves nothing about it
# ---------------------------------------------------------------------------


def _write(url: URL, statements: list[tuple[str, dict[str, object]]]) -> None:
    async def run() -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                for statement, params in statements:
                    await connection.execute(text(statement), params)
        finally:
            await engine.dispose()

    asyncio.run(run())


def _seed_a_card(url: URL, card_text: str) -> str:
    """One catalogued table carrying a card, written as revision 0012 left it."""
    org = "11111111-1111-1111-1111-111111111111"
    _write(
        url,
        [
            ("SELECT set_config('app.org_id', :org, false)", {"org": org}),
            ("INSERT INTO organizations (id, name) VALUES (:org, 'Cards')", {"org": org}),
            (
                "INSERT INTO data_sources (id, org_id, name, engine, host_display, secret_ref) "
                "VALUES ('22222222-2222-2222-2222-222222222222', :org, 'src', 'pg', "
                "'db:5432/x', 'ref')",
                {"org": org},
            ),
            (
                "INSERT INTO catalog_snapshots (id, org_id, data_source_id, version, status) "
                "VALUES ('33333333-3333-3333-3333-333333333333', :org, "
                "'22222222-2222-2222-2222-222222222222', 1, 'active')",
                {"org": org},
            ),
            (
                "INSERT INTO catalog_tables (org_id, snapshot_id, schema_name, table_name, "
                "kind, structural_hash, card_text) VALUES (:org, "
                "'33333333-3333-3333-3333-333333333333', 'public', 'menu_items', 'table', "
                "'hash', :card)",
                {"org": org, "card": card_text},
            ),
        ],
    )
    return org


def _card_of(url: URL, org: str) -> tuple[str, bool]:
    """The stored card, and whether its index matches the table's own name."""

    async def run() -> tuple[str, bool]:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                await connection.execute(
                    text("SELECT set_config('app.org_id', :org, false)"), {"org": org}
                )
                row = (
                    await connection.execute(
                        text(
                            "SELECT card_text, "
                            "card_tsv @@ websearch_to_tsquery('english', table_name) AS findable "
                            "FROM catalog_tables"
                        )
                    )
                ).one()
                return row.card_text, row.findable
        finally:
            await engine.dispose()

    return asyncio.run(run())


OLD_CARD = "public.menu_items is a table with about 40 rows.\nColumns (2):"
NEW_CARD = "menu_items (public.menu_items) is a table with about 40 rows.\nColumns (2):"


def test_0013_makes_an_existing_card_findable_by_its_own_name(
    alembic_config: Config, temp_database: URL
) -> None:
    """The whole point of the revision, and an empty database cannot show it.

    A card written before B-039 named its table only in qualified form, which
    PostgreSQL's English parser reads as one host token — so the card could not
    be found by searching for the table's name.
    """
    command.upgrade(alembic_config, "0012")
    org = _seed_a_card(temp_database, OLD_CARD)

    _, findable_before = _card_of(temp_database, org)
    assert findable_before is False, "the fixture is not reproducing the defect"

    command.upgrade(alembic_config, "0013")

    card_after, findable_after = _card_of(temp_database, org)
    assert card_after == NEW_CARD
    assert findable_after is True


def test_0013_leaves_a_card_that_is_already_correct_alone(
    alembic_config: Config, temp_database: URL
) -> None:
    """The LIKE guard. Re-running must not prepend the name a second time, and a
    database that already holds new-format cards must come through untouched."""
    command.upgrade(alembic_config, "0012")
    org = _seed_a_card(temp_database, NEW_CARD)

    command.upgrade(alembic_config, "0013")

    assert _card_of(temp_database, org)[0] == NEW_CARD


def test_0013_can_be_undone(alembic_config: Config, temp_database: URL) -> None:
    command.upgrade(alembic_config, "0012")
    org = _seed_a_card(temp_database, OLD_CARD)
    command.upgrade(alembic_config, "0013")

    command.downgrade(alembic_config, "0012")

    assert _card_of(temp_database, org)[0] == OLD_CARD
