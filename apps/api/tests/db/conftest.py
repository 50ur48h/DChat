"""Fixtures for tests that need a real PostgreSQL server.

Each test that wants a database gets a freshly created, empty one and drops it
afterwards, so migrations are always exercised from nothing — the state a new
environment is actually in.

Locally these tests skip when no server is reachable, so `make test.api` stays
useful without Docker. In CI ``REQUIRE_DB=1`` turns that skip into a failure:
a silently skipped migration or isolation test is worse than no test at all.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import URL, make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

from dataagent.config import Settings

API_DIR = Path(__file__).resolve().parents[2]

#: The unprivileged role the API connects as, created by migration 0002.
APP_ROLE = "dataagent_app"

#: Connecting to a database in order to create another one has to happen through
#: some existing database; `postgres` is present on every server.
MAINTENANCE_DATABASE = "postgres"


def _configured_url() -> URL | None:
    """Resolve the DSN the way the application does.

    Through ``Settings`` rather than ``os.environ`` so the repository's ``.env``
    counts: otherwise these tests would skip on every developer machine and only
    ever run in CI, which is the same as not having them.
    """
    raw = Settings().database_url
    return make_url(raw) if raw else None


async def _server_is_reachable(url: URL) -> bool:
    engine = create_async_engine(url.set(database=MAINTENANCE_DATABASE))
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        return False
    else:
        return True
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def database_url() -> URL:
    """The configured server, or a skip — unless CI demands otherwise."""
    url = _configured_url()
    required = os.environ.get("REQUIRE_DB") == "1"

    if url is None:
        message = "DATABASE_URL is not set"
        if required:
            pytest.fail(f"REQUIRE_DB=1 but {message}")
        pytest.skip(f"{message} — run `make up`, or set REQUIRE_DB=1 to make this a failure")

    if not asyncio.run(_server_is_reachable(url)):
        message = f"no PostgreSQL server reachable at {url.render_as_string(hide_password=True)}"
        if required:
            pytest.fail(f"REQUIRE_DB=1 but {message}")
        pytest.skip(f"{message} — run `make up`")

    return url


@pytest.fixture
def temp_database(database_url: URL) -> Iterator[URL]:
    """An empty database, dropped when the test finishes."""
    name = f"dataagent_test_{uuid.uuid4().hex[:12]}"
    admin_url = database_url.set(database=MAINTENANCE_DATABASE)

    async def _run(statement: str) -> None:
        engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as connection:
                await connection.execute(text(statement))
        finally:
            await engine.dispose()

    asyncio.run(_run(f'CREATE DATABASE "{name}"'))
    try:
        yield database_url.set(database=name)
    finally:
        # FORCE detaches any connection the test left behind, so one failing test
        # cannot leave an undroppable database and break every run after it.
        asyncio.run(_run(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture
def alembic_config(temp_database: URL, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", temp_database.render_as_string(hide_password=False))
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(API_DIR / "src" / "dataagent" / "db" / "alembic"))
    return config


@pytest.fixture(scope="session")
def app_role_password() -> str:
    """The password the application role should carry, taken from APP_DATABASE_URL.

    Reusing the configured value rather than inventing one matters: ``ALTER ROLE``
    is cluster-wide, so a test that made up its own password would silently
    change the credential of the developer's running stack.
    """
    raw = Settings().app_database_url
    if not raw:
        message = "APP_DATABASE_URL is not set"
        if os.environ.get("REQUIRE_DB") == "1":
            pytest.fail(f"REQUIRE_DB=1 but {message}")
        pytest.skip(f"{message} — run `make db.setup`")
    return make_url(raw).password or ""


@pytest.fixture
def migrated_database(alembic_config: Config, temp_database: URL) -> URL:
    """A temp database at head: schema, RLS policies and the application role."""
    command.upgrade(alembic_config, "head")
    return temp_database


@pytest.fixture
def app_database(migrated_database: URL, app_role_password: str) -> URL:
    """A DSN for the same database, connecting as ``dataagent_app``.

    Migration 0002 creates that role without LOGIN, exactly as it will exist in
    production; granting it a password is environment provisioning, which here
    means this fixture.
    """

    # ALTER ROLE is DDL and cannot take a bind parameter, so the password has to
    # be a literal. Doubling single quotes is the correct and complete escaping
    # for a string literal while standard_conforming_strings is on, which is the
    # default and which PostgreSQL has not allowed to be turned off since 9.1.
    literal = "'{}'".format(app_role_password.replace("'", "''"))

    async def _grant_login() -> None:
        engine = create_async_engine(migrated_database, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as connection:
                await connection.execute(
                    text(f"ALTER ROLE {APP_ROLE} WITH LOGIN PASSWORD {literal}")
                )
        finally:
            await engine.dispose()

    asyncio.run(_grant_login())
    return migrated_database.set(username=APP_ROLE, password=app_role_password)
