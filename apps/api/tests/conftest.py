"""Shared test fixtures.

Unit tests run against the ASGI app in-process — no network, no server, no ports.
Anything under ``tests/db`` or exercising authorization also needs a real
PostgreSQL server; those fixtures live here rather than in ``tests/db`` so both
suites can use them.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Generator, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import URL, make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

import customer_db
from customer_db import CustomerDatabase
from dataagent.config import Settings
from dataagent.datasources import service as datasource_service
from dataagent.llm import registry as llm_registry
from dataagent.llm.fake import FakeLLM
from dataagent.main import create_app
from dataagent.secrets.local import LocalSecretsProvider

API_DIR = Path(__file__).resolve().parents[1]

#: The unprivileged role the API connects as, created by migration 0002.
APP_ROLE = "dataagent_app"

#: Connecting to a database in order to create another one has to happen through
#: some existing database; `postgres` is present on every server.
MAINTENANCE_DATABASE = "postgres"


@pytest.fixture
def settings() -> Settings:
    """Settings for tests, independent of the developer's environment."""
    return Settings(env="ci", build_env="dev", git_sha="testsha", log_level="WARNING")


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings=settings)


@pytest.fixture
def secrets_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LocalSecretsProvider:
    """A real local backend, isolated per test, wired into the data-source service.

    The real implementation rather than a fake: this is the thing that actually
    holds customer credentials, and a stand-in would be free to drift from it in
    exactly the ways that matter. The key is generated per test and the file
    lives under tmp_path, so nothing here can touch a developer's own store.
    """
    provider = LocalSecretsProvider(
        key=Fernet.generate_key().decode(), path=tmp_path / "secrets" / "secrets.json"
    )
    monkeypatch.setattr(datasource_service, "get_secrets_provider", lambda: provider)
    return provider


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# ---------------------------------------------------------------------------
# A real PostgreSQL server
# ---------------------------------------------------------------------------


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


@contextmanager
def _temporary_database(database_url: URL) -> Generator[URL]:
    """An empty database on the configured server, dropped afterwards."""
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
def temp_database(database_url: URL) -> Iterator[URL]:
    """An empty database, dropped when the test finishes."""
    with _temporary_database(database_url) as url:
        yield url


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
def customer_database(temp_database: URL) -> CustomerDatabase:
    """An empty database, populated to look like a customer's.

    Separate from ``migrated_database``: that one is the platform's own schema,
    this one is somebody else's database that a connector must describe without
    knowing anything about it in advance.

    Both live in the same temporary database, which is fine for a connector test
    that reads named tables. Anything that *enumerates* what is there wants
    ``isolated_customer_database`` instead.
    """
    return asyncio.run(customer_db.build(temp_database))


@pytest.fixture
def isolated_customer_database(database_url: URL) -> Iterator[CustomerDatabase]:
    """A customer database in a database of its own.

    ``customer_database`` shares one temporary database with the platform schema,
    so a crawl that lists every table finds ``organizations`` and
    ``catalog_tables`` alongside the customer's. That is an artefact of the
    fixture and not of the product — no real deployment puts its own schema
    inside a customer's database — and it would make every catalog assertion
    both wrong and brittle. This fixture is the one to use whenever a test cares
    about *what is in* a customer's database rather than about one table in it.
    """
    with _temporary_database(database_url) as url:
        yield asyncio.run(customer_db.build(url))


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


@pytest.fixture
def fake_llm() -> Iterator[FakeLLM]:
    """A FakeLLM registered as the provider named ``fake``, and unregistered after.

    At the root rather than in one suite: the agent tests need the same provider
    the LLM tests do, and two definitions of one fixture drift.

    The teardown matters more than it looks. A provider left in the registry
    would answer the next test's calls, which is exactly the cross-test coupling
    that makes a deterministic harness stop being deterministic.
    """
    fake = FakeLLM()
    llm_registry.register_provider("fake", lambda: fake)
    try:
        yield fake
    finally:
        llm_registry.clear_provider_cache()
