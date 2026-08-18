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

#: The definitions this organization needs to answer its own questions the way
#: its own truths compute them (**B-070**).
#:
#: Golden **#10** — *"what proportion of customers ordered more than once?"* —
#: has two defensible readings and the English does not choose between them. The
#: truth is 7861/**7985**, customers who have *placed an order*; a live model
#: wrote a `LEFT JOIN` from `customers` and computed 7861/**8000**, the
#: proportion of everyone on file. Both are correct readings, and the model's is
#: arguably the better one — "customers" plainly means all of them. The gap is
#: 0.0018 against a tolerance of 0.001, so the eval failed a run that did nothing
#: wrong.
#:
#: Widening the tolerance would accept genuinely wrong numbers and rewriting the
#: question to match the answer is the self-deception B-070 is about. **The
#: semantic layer is whose job this is**: the organization says which reading is
#: authoritative, once, and the question stops depending on a coin flip.
#:
#: **No `required_filters`, and that is not an omission.** The ambiguity is in
#: the *denominator* — which rows are counted at all — and no `{table, column,
#: op, values}` predicate expresses "customers that appear in orders". So this
#: definition **informs and does not bind** (D-033), which is the honest shape
#: for it: the critic cannot check a denominator, and claiming to would be worse
#: than saying plainly that only the filters bind.
#:
#: The synonyms are what make it reachable. Matching is whole-word against the
#: question, and nobody types `repeat_rate` — the question says *"ordered more
#: than once"*, so that is what it answers to. Narrow on purpose: a broader
#: synonym like "proportion of customers" would attach this definition to
#: questions about a different proportion entirely.
EVAL_DEFINITIONS: tuple[dict[str, object], ...] = (
    {
        "name": "repeat_rate",
        "description": (
            "The proportion of customers who have placed more than one order, out of "
            "customers who have placed at least one. The denominator is customers with "
            "an order — not every customer on file. A customer who has never ordered is "
            "counted in neither half, so compute both halves from the orders table "
            "rather than joining out from customers."
        ),
        "expression": (
            "count(customers with more than one order) / count(customers with at least one order)"
        ),
        "synonyms": ("ordered more than once", "repeat customers", "repeat rate"),
    },
)
SOURCE_NAME = "Demo"
EVAL_USER_EMAIL = "evals@localhost"


async def main() -> int:
    from dotenv import load_dotenv
    from sqlalchemy import text

    from dataagent.catalog import cards, discovery, profiler
    from dataagent.datasources import service as datasources
    from dataagent.db.engine import build_engine
    from dataagent.knowledge import embeddings
    from dataagent.semantic import definitions as semantic
    from dataagent.tenancy.session import org_session

    # The repository's own .env, when there is one. `load_dotenv` does not
    # override what is already set, so a workflow's environment still wins — CI
    # has no .env of its own until `make` creates a placeholder one from
    # .env.example, and that placeholder must not replace the real values.
    if (env := REPO_ROOT / ".env").exists():
        load_dotenv(env)

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

    # Cards are embedded here or nowhere (**B-018**): the evals' catalog is built
    # by this script and never through the UI, so a run with no vectors would
    # measure the lexical half of a hybrid search and call it the product. In CI
    # there is no embedding key, `get_embedder` returns None, and this is a
    # no-op — which is correct there, because FakeLLM mode takes its SQL from
    # `golden.yaml` and never depends on which cards were retrieved.
    embedder = embeddings.get_embedder()
    print(f"  embedder: {'configured' if embedder is not None else 'none — cards stay lexical'}")

    found = await discovery.discover(
        org_id=org_id, actor_user_id=user_id, data_source_id=view.id, embedder=embedder
    )
    print(f"  discovery: {found.detail}")
    profiled = await profiler.profile(
        org_id=org_id, actor_user_id=user_id, data_source_id=view.id, embedder=embedder
    )
    print(f"  profiling: {profiled.detail}")
    # Idempotent, so a rerun after a key is configured fills in what the first
    # run could not — which is the whole point of the backfill being separate.
    embedded = await cards.embed_cards(org_id, view.id, embedder=embedder)
    print(f"  embedded {embedded} card(s)")

    # The definitions this organization has agreed on (**B-070**). Idempotent
    # like everything else here: a name this source already knows is left alone,
    # so a rerun does not fail on the unique constraint and does not overwrite an
    # edit somebody made by hand.
    known = {definition.name for definition in await semantic.definitions_for(org_id, view.id)}
    for spec in EVAL_DEFINITIONS:
        name = str(spec["name"])
        if name in known:
            print(f"  definition {name}: already defined")
            continue
        await semantic.create(
            org_id=org_id,
            data_source_id=view.id,
            actor_user_id=user_id,
            name=name,
            description=str(spec["description"]),
            expression=str(spec["expression"]),
            synonyms=tuple(spec["synonyms"]),  # type: ignore[arg-type]
        )
        print(f"  definition {name}: created")

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
