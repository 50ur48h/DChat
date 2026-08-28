"""Load a file of metric definitions into a registered data source.

    make seed.definitions FILE=ops/seed/miseq_definitions.json SOURCE="MiseQ v6.4"

Runs **inside the api container**, for `provision_demo.py`'s reason: writing a
definition validates its `required_filters` against the catalog, which means
loading a `SourcePolicy`, which means being the process that can reach the
platform database. A host script cannot honestly do it.

**Why active rather than proposed.** `accept()` replaces a row's filters with
whatever the accepting call sends, so a definition seeded as a proposal loses
its filter the moment somebody clicks Accept without re-typing it. These arrive
already reviewed — each one was checked against the data before it was written —
so the review that matters already happened, and asking for a second one would
cost `edible_waste` the single filter that makes it bind.

**Idempotent by name.** A rerun updates the definition in place rather than
failing or duplicating, so correcting a caveat is editing this file and running
it again. That path goes through `update()`, which versions the change, so the
history says what moved and when.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

#: The container mounts the API's source here; nothing else is on the path.
sys.path.insert(0, "/app/src")

FILE = os.environ.get("DEFINITIONS_FILE", "/tmp/definitions.json")  # noqa: S108
SOURCE_NAME = os.environ.get("DEFINITIONS_SOURCE", "")
ORG_NAME = os.environ.get("DEMO_ORG_NAME", "Demo")
SETUP_USER_EMAIL = "setup@localhost"


async def _org_and_actor() -> tuple[uuid.UUID, uuid.UUID]:
    from sqlalchemy import text

    from dataagent.db.engine import build_engine

    engine = build_engine()
    try:
        async with engine.begin() as connection:
            org = (
                await connection.execute(
                    text("SELECT id FROM organizations WHERE name = :n"), {"n": ORG_NAME}
                )
            ).scalar_one_or_none()
            if org is None:
                raise SystemExit(
                    f"No organization called {ORG_NAME!r}. Run `make demo.setup` first."
                )
            user = (
                await connection.execute(
                    text("SELECT id FROM users WHERE email = :e"), {"e": SETUP_USER_EMAIL}
                )
            ).scalar_one_or_none()
            if user is None:
                raise SystemExit(f"No {SETUP_USER_EMAIL} user. Run `make demo.setup` first.")
    finally:
        # Disposed explicitly: this engine is built for two queries and the
        # process goes on to open tenant sessions of its own.
        await engine.dispose()
    return uuid.UUID(str(org)), uuid.UUID(str(user))


async def _source(org_id: uuid.UUID) -> tuple[uuid.UUID, str]:
    from dataagent.datasources import service as sources

    registered = await sources.list_data_sources(org_id)
    if not registered:
        raise SystemExit("No data source is registered. Run `make demo.setup` first.")
    if SOURCE_NAME:
        for view in registered:
            if view.name == SOURCE_NAME:
                return view.id, view.name
        names = ", ".join(repr(view.name) for view in registered)
        raise SystemExit(f"No data source called {SOURCE_NAME!r}. Registered: {names}")
    if len(registered) > 1:
        names = ", ".join(repr(view.name) for view in registered)
        raise SystemExit(f"More than one data source is registered; name one. Have: {names}")
    return registered[0].id, registered[0].name


async def main(wanted: list[dict[str, Any]]) -> int:
    from dataagent.semantic import definitions as service

    org_id, actor = await _org_and_actor()
    source_id, source_name = await _source(org_id)
    print(f"Organization {ORG_NAME!r}, data source {source_name!r}")

    existing = {view.name: view for view in await service.definitions_for(org_id, source_id)}
    created = updated = unchanged = 0

    for item in wanted:
        name = item["name"]
        fields: dict[str, Any] = {
            "description": item["description"],
            "expression": item.get("expression"),
            "caveat": item.get("caveat"),
            "synonyms": item.get("synonyms", []),
            "required_filters": item.get("required_filters", []),
        }
        try:
            if name in existing:
                before = existing[name]
                after = await service.update(
                    org_id=org_id,
                    definition_id=before.id,
                    actor_user_id=actor,
                    **fields,
                )
                if after.version == before.version:
                    print(f"  = {name}")
                    unchanged += 1
                else:
                    print(f"  ~ {name}  (v{before.version} -> v{after.version})")
                    updated += 1
            else:
                view = await service.create(
                    org_id=org_id,
                    data_source_id=source_id,
                    name=name,
                    kind=item.get("kind", "metric"),
                    actor_user_id=actor,
                    **fields,
                )
                binds = "enforced" if view.required_filters else "prose"
                print(f"  + {name}  ({binds}, {len(view.synonyms)} synonyms)")
                created += 1
        except Exception as error:
            # Named, because the API's own error says which column a filter got
            # wrong, and a generic failure would send whoever ran this to guess.
            print(f"  ! {name}: {error}")
            return 1

    print(f"\n{created} created, {updated} updated, {unchanged} already current.")
    print("Review them at Settings -> Definitions for this data source.")
    return 0


if __name__ == "__main__":
    # Read before the loop starts: it is a local file, and reading it inside an
    # async function is a blocking call dressed as a non-blocking one.
    document: dict[str, Any] = json.loads(Path(FILE).read_text(encoding="utf-8"))
    raise SystemExit(asyncio.run(main(document["definitions"])))
