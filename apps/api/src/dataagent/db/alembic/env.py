"""Alembic environment (async).

The DSN comes from ``ALEMBIC_DATABASE_URL`` when set — which is how the migration
tests point at a throwaway database — and otherwise from application settings.
It is never written into ``alembic.ini``: that would either commit a credential
or hand a password containing ``%`` to Alembic's interpolation.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from dataagent.config import Settings

# Imported from `models` rather than `base`: reaching Base through the module
# that defines the tables is what registers them on the metadata Alembic
# compares against. Importing `base` alone would autogenerate an empty schema.
from dataagent.db.engine import build_engine
from dataagent.db.models import Base

config = context.config

if config.config_file_name is not None:
    # **`disable_existing_loggers=False`, and it is not a style preference**
    # (**B-126**). `fileConfig` defaults to True, which switches off every logger
    # that already exists — which is every `dataagent.*` module logger, since
    # they are created at import. Any process that runs a migration and then
    # keeps working loses its logging entirely from that point on.
    #
    # The test suite is that process. `conftest` migrates once at session start,
    # so **no test has ever been able to observe a log line from application
    # code** — a `caplog` assertion here captured nothing at all, silently, which
    # is how the runner came to have no logging on its failure path for eleven
    # phases with a green suite the whole time.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def database_url() -> str:
    return os.environ.get("ALEMBIC_DATABASE_URL") or Settings().require_database_url()


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine: AsyncEngine = build_engine(Settings(database_url=database_url()))
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
