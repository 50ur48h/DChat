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
from collections.abc import AsyncIterator, Callable, Generator, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import URL, make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

import customer_db
from customer_db import CustomerDatabase
from dataagent.config import Settings
from dataagent.datasources import service as datasource_service
from dataagent.knowledge import embeddings
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


# ---------------------------------------------------------------------------
# No test may call a real model (B-040)
# ---------------------------------------------------------------------------
#
# This is not hypothetical. A WP7.2c test called OpenAI for real and billed for
# two completions: the route path resolves settings through ``get_settings()``,
# which reads the developer's ``.env``, so any test that reaches the agent
# through HTTP uses whatever provider that file configures. **CI never noticed
# and never could** — it has no key, so the suite passed there while quietly
# charging every developer who ran it locally.
#
# Two mechanisms, because one is not enough:
#
# * the guard **raises**, so the call never leaves the machine;
# * the guard also **records**, and a per-test check fails the test afterwards.
#
# The second exists because the agent runner catches broad exceptions and turns
# them into a failed run — so a guard that only raised would be swallowed by the
# very code most likely to trip it, and the suite would go green having spent
# nothing but also having proved nothing.
#
# **Two doors, not one** (B-073). Chat models come through
# ``registry.get_provider``; embeddings come through
# ``knowledge.embeddings.get_embedder``, which bills the same account through a
# different endpoint. A guard that watched only the first would have let an
# embedding call — one per document chunk, on every upload — straight out to the
# provider, which is a larger bill than the one that prompted B-040. Both wrap
# the same recording list, so the per-test check below covers either.

#: The real factory, captured before the session fixture replaces it. Handed to
#: the few tests that are about ``get_embedder`` itself: *building* an embedder
#: reaches no network and spends nothing, but the guard cannot tell that from a
#: caller about to use one, so those tests ask for the unguarded function by
#: name rather than switching the guard off.
_real_get_embedder = embeddings.get_embedder

#: Provider or embedder names a test used that were not stubs. Cleared per test.
_live_provider_uses: list[str] = []

#: Non-empty while the current test carries ``@pytest.mark.live_provider``.
_live_allowed: list[bool] = []

#: How the refusal tells whoever hit it what to do instead. One string, because
#: two doors refusing in two different dialects is two things to learn.
_ADVICE = (
    "Tests must not reach a real provider: pass `settings=build_settings()` so "
    "configuration comes from the fixture rather than from .env, or mark the "
    "test `@pytest.mark.live_provider` if it genuinely must spend money."
)


@pytest.fixture(scope="session", autouse=True)
def forbid_live_providers() -> Iterator[None]:
    """Wrap both spending doors for the whole session, so no test escapes by ordering."""
    real_provider = llm_registry.get_provider
    real_embedder = embeddings.get_embedder

    def guarded(name: str, settings: Settings | None = None) -> object:
        provider = real_provider(name, settings)
        if provider.capabilities().is_stub or _live_allowed:
            return provider
        _live_provider_uses.append(name)
        raise RuntimeError(f"a test asked for the live provider {name!r}. {_ADVICE}")

    def guarded_embedder(settings: Settings | None = None) -> object:
        embedder = real_embedder(settings)
        # None is the ordinary answer for a deployment with no embedding model,
        # and it spends nothing — so it passes through untouched.
        if embedder is None or embedder.is_stub or _live_allowed:
            return embedder
        _live_provider_uses.append("embeddings")
        raise RuntimeError(f"a test asked for a live embedder. {_ADVICE}")

    llm_registry.get_provider = guarded  # pyright: ignore[reportAttributeAccessIssue]
    embeddings.get_embedder = guarded_embedder  # pyright: ignore[reportAttributeAccessIssue]
    try:
        yield
    finally:
        llm_registry.get_provider = real_provider  # pyright: ignore[reportAttributeAccessIssue]
        embeddings.get_embedder = real_embedder  # pyright: ignore[reportAttributeAccessIssue]


@pytest.fixture
def embedder_factory() -> Callable[..., object]:
    """``knowledge.embeddings.get_embedder`` as the product sees it, unguarded.

    For tests about the factory's own answers — no model configured, no key, a
    width that disagrees with the column. They build an object and never call
    it, which costs nothing; everything that would *use* an embedder still goes
    through the guard.
    """
    return _real_get_embedder


@pytest.fixture
def expected_live_provider_refusal() -> Iterator[None]:
    """For the guard's own tests: this one meant to trip it.

    Requested rather than marked, because a marker would be indistinguishable
    from ``live_provider`` at a glance and these two mean opposite things — one
    says "let it through", this says "it should have been stopped, and it was".

    Teardown order is what makes it work: an autouse fixture sets up first and so
    finalises last, which is after this one has cleared the record.
    """
    yield
    _live_provider_uses.clear()


@pytest.fixture(autouse=True)
def no_live_provider_used(request: pytest.FixtureRequest) -> Iterator[None]:
    """Fail the test if it reached for a real model, however the error was handled."""
    allowed = request.node.get_closest_marker("live_provider") is not None  # pyright: ignore[reportUnknownMemberType]
    _live_provider_uses.clear()
    _live_allowed.clear()
    if allowed:
        _live_allowed.append(True)

    yield

    used, _live_provider_uses[:] = list(_live_provider_uses), []
    _live_allowed.clear()
    if used and not allowed:
        pytest.fail(
            f"this test reached for {sorted(set(used))}, which would spend real "
            f"money. See B-040. {_ADVICE}"
        )


@pytest.fixture
def settings() -> Settings:
    """Settings for tests, independent of the developer's environment.

    ``_env_file=None`` is load-bearing and was missing (**B-032**): without it
    every field this does not pass explicitly comes from the repository's
    ``.env``, so a developer with real LLM configuration ran different tests from
    CI — and the *inverse*, a test passing locally and failing in CI, is the
    version of that which wastes an afternoon.

    It is not sufficient on its own, which is why the live-provider guard above
    exists: the route path never consults this fixture at all, it calls
    ``get_settings()``.
    """
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        env="ci",
        build_env="dev",
        git_sha="testsha",
        log_level="WARNING",
        # **A configuration that could actually serve.** Without these two the
        # fixture is `AUTH_MODE=entra` with no authority and a local secrets
        # backend with no key — a deployment that boots and refuses every
        # authenticated request, which is exactly the shape `/healthz` now
        # reports as `degraded`. Leaving them unset would have made the probe's
        # "ok" test assert nothing, and the fixture was only ever incomplete by
        # omission rather than on purpose.
        oidc_authority="https://tests.ciamlogin.com/tenant/v2.0",
        local_secrets_key=SecretStr(Fernet.generate_key().decode()),
    )


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
