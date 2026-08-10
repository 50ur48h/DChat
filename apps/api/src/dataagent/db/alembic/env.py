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
    fileConfig(config.config_file_name)

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
