"""Give ``dataagent_app`` its login in a deployed environment (WP12.2).

    python -m dataagent.db.grant_app_login

**This is the Phase 12 half of `ops/sql/app_role.sql`**, whose own header has
promised it since Phase 1: *"in Phase 12 this step is replaced by Key Vault and a
managed identity, and the migration is unchanged."* Half of that is delivered
here — the password comes from Key Vault, and no migration contains a credential.
The other half is not: the API still authenticates to Postgres with a password
rather than as its managed identity, which is filed as **B-121** and deferred
deliberately rather than quietly.

**Why this cannot be a migration.** Migration 0002 creates the role and every
grant it holds, and a migration must never contain a credential — the file is in
git, and a password in it would be in git forever. So the role arrives without a
login and something outside the migration gives it one: `make db.setup` locally,
this module in a deployment.

**Why it runs in the migration job and not the pipeline.** The Postgres server
has no public endpoint. It answers inside the Container Apps environment's subnet
and nowhere else, so the only process that can reach it is one running in that
environment — and the only such process holding the *owner* credential is the
migration job. The API runs as `dataagent_app` and could not do this even if it
were asked to, which is the separation working rather than an inconvenience.

**Idempotent, and it never logs the password.** Runs on every deploy; `ALTER
ROLE` is the same statement whether the password is new or unchanged, which also
makes it the rotation path — change the vault secret, redeploy, done.
"""

from __future__ import annotations

import asyncio
import os
import sys

from dataagent.config import get_settings

#: The role migration 0002 creates. Named here rather than imported so this
#: module does not drag the migration's module graph into a job that only needs
#: one statement.
APP_ROLE = "dataagent_app"

#: The statement builder, as a constant so it can be **compiled and asserted
#: without a database**. Every failure this module has had was in how SQLAlchemy
#: and asyncpg read this one string, and none of them needed Postgres to detect:
#: written `:pw::text`, SQLAlchemy binds *nothing* — the compiled SQL still holds
#: a literal `:pw` and the server answers `syntax error at or near ":"`. A test
#: that compiles this and checks both parameters bound catches that on any
#: machine, including one whose Docker is broken.
ALTER_ROLE_SQL = (
    "SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', CAST(:role AS text), CAST(:pw AS text))"
)

#: `grant()` takes the role as an argument purely so its refusal path can be
#: tested against a throwaway role. **`ALTER ROLE` is cluster-global in
#: PostgreSQL, not per-database**, so a test that gave the real `dataagent_app`
#: BYPASSRLS would be handing it to every other database in the cluster — and if
#: anything raised before the restore, to every test that ran afterwards. That is
#: not hypothetical: it happened, and it failed the entire rls_proof suite with
#: "the API role can bypass RLS", which reads exactly like the security boundary
#: had broken.


async def grant(role: str = APP_ROLE) -> int:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    # Module-level so a test can substitute it the way the rest of this suite
    # does (`monkeypatch.setattr(module, "get_settings", ...)`). The alternative
    # — clearing the lru_cache — mutates process-wide state that every later test
    # in the session then re-reads from the developer's own .env.
    settings = get_settings()
    password = os.environ.get("APP_DB_PASSWORD", "")
    if not password:
        print(
            "APP_DB_PASSWORD is not set, so dataagent_app would have no login and "
            "the API could not connect. The deploy seeds it from Key Vault.",
            file=sys.stderr,
        )
        return 1

    # The owner connection. `require_database_url` rejoins the password the
    # template deliberately kept out of the URL.
    engine = create_async_engine(settings.require_database_url(), isolation_level="AUTOCOMMIT")
    try:
        async with engine.begin() as connection:
            # **A bound parameter cannot be used here.** `ALTER ROLE ... PASSWORD`
            # takes a literal, not a placeholder, so the value has to be inlined —
            # and inlining is exactly the shape of thing that becomes an
            # injection. `format` with `%I` and `%L` is Postgres's own quoting,
            # applied by the server to *bound* parameters, so neither the role
            # name nor the password appears in a string this process concatenates.
            #
            # **The casts are not decoration, and they are spelled `CAST(...)`
            # for a second reason.** Without a cast, asyncpg cannot infer a type
            # for a parameter whose only use is inside `format()` and refuses the
            # statement with `IndeterminateDatatypeError: could not determine
            # data type of parameter $1`. Written the PostgreSQL way, `:pw::text`,
            # SQLAlchemy's `text()` mis-parses the `::` against its own `:name`
            # bind syntax and emits SQL the server rejects with `syntax error at
            # or near ":"`. `CAST(x AS text)` has no colons in it and satisfies
            # both. CI said each of those in turn.
            statement = (
                await connection.execute(
                    text(ALTER_ROLE_SQL),
                    {"role": role, "pw": password},
                )
            ).scalar_one()
            await connection.exec_driver_sql(statement)

            # Read back what the role can actually do, exactly as app_role.sql
            # does. If any of these is true, tenant isolation is not what this
            # project claims it is — and a deploy is the moment to find out.
            row = (
                await connection.execute(
                    text(
                        "SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole "
                        "FROM pg_roles WHERE rolname = :r"
                    ),
                    {"r": role},
                )
            ).one()
        can_login, is_super, can_bypass, can_createdb, can_createrole = row
    finally:
        await engine.dispose()

    print(
        f"{role}: login={can_login} superuser={is_super} bypassrls={can_bypass} "
        f"createdb={can_createdb} createrole={can_createrole}"
    )
    if not can_login:
        print(f"{role} still cannot log in.", file=sys.stderr)
        return 1
    if is_super or can_bypass or can_createdb or can_createrole:
        # Refusing here stops the deploy before the API is rolled onto a
        # revision that would connect with more privilege than the design allows.
        print(
            f"{role} has privileges it must not have. The API connects as this "
            "role and RLS is the tenant boundary; refusing to continue.",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    return asyncio.run(grant())


if __name__ == "__main__":
    raise SystemExit(main())
