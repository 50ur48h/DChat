"""Build the state the evals need, from nothing (plan WP9.2b).

    make evals.setup

An organization, a member, the pizza database registered as a read-only data
source, and a catalog discovered and profiled. Prints the organization id, which
is what `make evals` needs.

**This exists so the evals can be a required check.** Twenty golden questions
that only run when someone remembers are not a regression net, and the thing
standing between them and CI was that they needed a registered data source —
which until now only ever came from a person clicking through the UI. So the
provisioning is code, and CI runs it the same way a developer does.

**Idempotent, and it says what it found.** Run twice and the second run reuses
the organization and the source rather than making a second of each; that is what
makes it safe to put in a workflow that reruns, and what makes it usable locally
without a database reset first.

It registers the **read-only** login, never the owner — `readonly_verified` is
part of what the evals exercise, and a source registered with a writing
credential would quietly skip that check.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))

#: Stable, so a rerun finds what the last run made rather than creating another.
ORG_NAME = "evals"
SOURCE_NAME = "Demo"
EVAL_USER_EMAIL = "evals@localhost"


async def main() -> int:
    from sqlalchemy import text

    from dataagent.catalog import discovery, profiler
    from dataagent.datasources import service as datasources
    from dataagent.db.engine import build_engine
    from dataagent.tenancy.session import org_session

    host = os.environ.get("SEED_PIZZA_HOST", "localhost")
    port = int(os.environ.get("SEED_PIZZA_PORT", "6543"))
    database = os.environ.get("SEED_PIZZA_DB", "pizza")
    username = os.environ.get("SEED_PIZZA_READONLY_USER", "pizza_readonly")
    password = os.environ.get("SEED_PIZZA_READONLY_PASSWORD")
    if not password:
        raise SystemExit("SEED_PIZZA_READONLY_PASSWORD is not set; `make seed` creates the role.")

    # The organization and its member are written with the owner connection: they
    # are the rows RLS is *about*, so there is no org session to make them in.
    engine = build_engine()
    async with engine.begin() as connection:
        org_id = (
            await connection.execute(
                text("SELECT id FROM organizations WHERE name = :n"), {"n": ORG_NAME}
            )
        ).scalar_one_or_none()
        if org_id is None:
            org_id = uuid.uuid4()
            await connection.execute(
                text("INSERT INTO organizations (id, name) VALUES (:i, :n)"),
                {"i": org_id, "n": ORG_NAME},
            )
            print(f"created organization {ORG_NAME} -> {org_id}")
        else:
            org_id = uuid.UUID(str(org_id))
            print(f"reusing organization {ORG_NAME} -> {org_id}")

        user_id = (
            await connection.execute(
                text("SELECT id FROM users WHERE email = :e"), {"e": EVAL_USER_EMAIL}
            )
        ).scalar_one_or_none()
        if user_id is None:
            user_id = uuid.uuid4()
            await connection.execute(
                text(
                    "INSERT INTO users (id, external_subject, email, name) "
                    "VALUES (:i, :s, :e, 'Eval harness')"
                ),
                {"i": user_id, "s": f"evals-{user_id}", "e": EVAL_USER_EMAIL},
            )
        else:
            user_id = uuid.UUID(str(user_id))
        await connection.execute(
            text(
                # (org_id, user_id) is the primary key — no surrogate id.
                "INSERT INTO org_memberships (org_id, user_id, role) "
                "VALUES (:o, :u, 'admin') ON CONFLICT DO NOTHING"
            ),
            {"o": org_id, "u": user_id},
        )
    await engine.dispose()

    existing = {source.name: source for source in await datasources.list_data_sources(org_id)}
    if SOURCE_NAME in existing:
        view = existing[SOURCE_NAME]
        print(f"reusing data source {SOURCE_NAME} -> {view.id}")
    else:
        view = await datasources.create_data_source(
            org_id=org_id,
            actor_user_id=user_id,
            name=SOURCE_NAME,
            engine="pg",
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
        )
        print(f"registered data source {SOURCE_NAME} -> {view.id}")

    health = await datasources.test_data_source(
        org_id=org_id, actor_user_id=user_id, data_source_id=view.id
    )
    print(f"  reachable={health.reachable} readonly_verified={health.readonly_verified}")
    if not health.readonly_verified:
        # Discovery refuses an unverified source anyway; failing here says why.
        raise SystemExit(f"the credentials were not proven read-only: {health.detail}")

    found = await discovery.discover(org_id=org_id, actor_user_id=user_id, data_source_id=view.id)
    print(f"  discovery: {found.detail}")
    profiled = await profiler.profile(org_id=org_id, actor_user_id=user_id, data_source_id=view.id)
    print(f"  profiling: {profiled.detail}")

    async with org_session(org_id) as session:
        cards = (
            await session.execute(
                text(
                    "SELECT count(*) FROM catalog_tables t JOIN catalog_snapshots s "
                    "ON s.id = t.snapshot_id WHERE s.data_source_id = :d AND s.status = 'active'"
                ),
                {"d": view.id},
            )
        ).scalar_one()
    if not cards:
        raise SystemExit("the catalog is empty; the evals would all fail on grounding")

    print(f"\nEVALS_ORG_ID={org_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
