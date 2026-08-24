"""Giving `dataagent_app` its login in a deployment (WP12.2, B-121).

**The read-back is what is under test, not the ALTER ROLE.** Granting a login is
one statement and it either works or raises. What this module adds — and what
would be worth nothing if it silently stopped working — is that it *checks what
the role can actually do afterwards* and refuses the deploy if the answer is
wrong. `ops/sql/app_role.sql` has done the same read-back since Phase 1 for the
same reason: the API connects as this role and RLS is the tenant boundary, so a
role that came back with `rolsuper` would make every isolation claim in this
project false, and a deploy is the moment to find out rather than later.

Run against a real Postgres, because what is asserted is what the *server* reports
about a role, and a fake would only assert what this test already believes.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.dialects.postgresql.asyncpg import dialect as asyncpg_dialect
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine

from dataagent.config import Settings
from dataagent.db import grant_app_login as module
from dataagent.db.grant_app_login import ALTER_ROLE_SQL, APP_ROLE, grant

pytestmark = pytest.mark.asyncio


@pytest.fixture
def owner_env(migrated_database: URL, monkeypatch: pytest.MonkeyPatch) -> URL:
    """Point the module's settings at the temp database, the way the job is.

    **Substituting `get_settings` on the module, never clearing its cache.**
    `get_settings` is an `lru_cache` singleton read once per process; clearing it
    mid-session makes every later test re-read configuration from the developer's
    own `.env` instead of whatever the session set up. That is not theoretical —
    the first version of this file did exactly that and took 21 tenancy tests down
    with it in CI, because `tests/db` runs before `tests/orgs`, `tests/runs` and
    `tests/semantic`. Cross-org requests started returning 200 where they had
    returned 404, which is the most alarming possible way to learn that a fixture
    is too clever. `monkeypatch.setattr` is what the rest of this suite uses and
    it is restored per test.
    """
    resolved = Settings(  # pyright: ignore[reportArgumentType]
        database_url=migrated_database.render_as_string(hide_password=False),
        db_password=None,
    )
    monkeypatch.setattr(module, "get_settings", lambda: resolved)
    return migrated_database


async def _roles_of(dsn: URL) -> dict[str, bool]:
    engine = create_async_engine(dsn)
    try:
        async with engine.begin() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole "
                        "FROM pg_roles WHERE rolname = :r"
                    ),
                    {"r": APP_ROLE},
                )
            ).one_or_none()
    finally:
        await engine.dispose()
    assert row is not None, f"{APP_ROLE} does not exist"
    keys = ("login", "super", "bypassrls", "createdb", "createrole")
    return dict(zip(keys, row, strict=True))


async def _alter(dsn: URL, statement: str) -> None:
    engine = create_async_engine(dsn, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.exec_driver_sql(statement)
    finally:
        await engine.dispose()


async def test_the_grant_gives_a_login_and_nothing_else(
    owner_env: URL, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The happy path, and the properties the whole tenancy design rests on."""
    monkeypatch.setenv("APP_DB_PASSWORD", f"pw-{uuid.uuid4().hex}")

    assert await grant() == 0

    facts = await _roles_of(owner_env)
    assert facts["login"] is True
    assert facts["super"] is False
    assert facts["bypassrls"] is False
    assert facts["createdb"] is False
    assert facts["createrole"] is False


async def test_the_granted_password_actually_connects(
    owner_env: URL, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proof it is *reached*: the credential this writes is one asyncpg accepts.

    A test that only read `pg_roles` would pass against a grant that set some
    other role's password, or the right role's to something else.
    """
    password = f"pw-{uuid.uuid4().hex}"
    monkeypatch.setenv("APP_DB_PASSWORD", password)
    assert await grant() == 0

    engine = create_async_engine(owner_env.set(username=APP_ROLE, password=password))
    try:
        async with engine.begin() as connection:
            who = (await connection.execute(text("SELECT current_user"))).scalar_one()
    finally:
        await engine.dispose()
    assert who == APP_ROLE


async def test_a_password_full_of_quotes_survives(
    owner_env: URL, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ALTER ROLE ... PASSWORD` takes a literal, not a placeholder, so the value
    has to be inlined — and inlining is where injection lives. The statement is
    built by Postgres's own `format(%L)` from a bound parameter, so a password
    full of quotes is escaped by the server rather than by string handling here.
    """
    password = "a'b''c\"d;--" + uuid.uuid4().hex
    monkeypatch.setenv("APP_DB_PASSWORD", password)

    assert await grant() == 0

    engine = create_async_engine(owner_env.set(username=APP_ROLE, password=password))
    try:
        async with engine.begin() as connection:
            assert (await connection.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        await engine.dispose()


async def test_no_password_refuses_rather_than_granting_an_empty_one(
    owner_env: URL, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty password is a role anyone can be. Refusing stops the deploy.

    **Only the return value is asserted, deliberately.** The obvious extra check —
    that the role still cannot log in — is unsound here, because `ALTER ROLE` is
    cluster-global and conftest's own `app_database` fixture grants
    `dataagent_app` a login for any earlier test that needed one. Asserting
    `login is False` therefore depends on which tests ran first; it failed in CI
    with `assert True is False` for exactly that reason. What this test can honestly
    claim is that `grant` refuses, which is what stops the deploy.
    """
    monkeypatch.delenv("APP_DB_PASSWORD", raising=False)

    assert await grant() == 1


async def test_a_role_with_too_much_privilege_stops_the_deploy(
    owner_env: URL, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check that earns this module its place.

    Reproduces the state rather than asserting a message: a role really does have
    BYPASSRLS, and the grant really does refuse — so a deploy stops before the API
    is rolled onto a revision that would connect with more privilege than RLS
    assumes.

    **Against a throwaway role, never `dataagent_app`.** `ALTER ROLE` is
    cluster-global in PostgreSQL, not scoped to the database the connection is
    on, so giving the real role BYPASSRLS hands it to every other database in the
    cluster — and to every test that runs afterwards if anything raises before the
    restore. The first version of this test did that, `grant()` then failed on an
    unrelated bug, and the whole rls_proof suite went red with "the API role can
    bypass RLS". `grant()` takes a role argument for exactly this reason.
    """
    monkeypatch.setenv("APP_DB_PASSWORD", f"pw-{uuid.uuid4().hex}")
    probe = f"dataagent_probe_{uuid.uuid4().hex[:12]}"
    await _alter(owner_env, f"CREATE ROLE {probe} WITH BYPASSRLS")
    try:
        assert await grant(role=probe) == 1
    finally:
        await _alter(owner_env, f"DROP ROLE IF EXISTS {probe}")

    # And the real role is untouched by any of it.
    assert (await _roles_of(owner_env))["bypassrls"] is False


async def test_the_real_role_is_never_given_extra_privilege_by_these_tests(
    owner_env: URL,
) -> None:
    """A tripwire, not a tautology.

    If a future edit here reaches for `dataagent_app` again — the obvious thing to
    do, and the thing that cost a red rls_proof suite — this fails in the file
    that caused it rather than in twelve unrelated tests downstream.
    """
    facts = await _roles_of(owner_env)
    assert facts["bypassrls"] is False
    assert facts["super"] is False


# --------------------------------------------------------------------------
# The part that needs no database, and would have caught every failure so far.
# --------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="function")
async def test_both_parameters_actually_bind() -> None:
    """Compile the statement and check SQLAlchemy bound what we think it bound.

    **Three CI runs died on this one string and not one of them needed Postgres
    to detect.** Written the PostgreSQL way — `:pw::text` — SQLAlchemy's `text()`
    binds *nothing*: the compiled SQL still carries a literal `:pw`, `params` is
    empty, and the server answers `syntax error at or near ":"`. Written without
    a cast at all, asyncpg cannot infer a type and answers
    `IndeterminateDatatypeError`.

    This test runs on any machine, including one whose Docker is broken, which is
    the whole reason it exists: the five tests above skip without a database and
    a green local run then looks identical to a green one that proved something.
    """
    compiled = text(ALTER_ROLE_SQL).compile(dialect=asyncpg_dialect())

    assert set(compiled.params) == {"role", "pw"}
    # Both placeholders reached the driver as positional parameters, and no
    # `:name` survived into the SQL the server would see.
    assert "$1" in str(compiled)
    assert "$2" in str(compiled)
    assert ":role" not in str(compiled)
    assert ":pw" not in str(compiled)
