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
