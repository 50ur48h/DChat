"""Load a definitions file into a **deployed** environment over the public API.

    export DATAAGENT_TOKEN='...'                    # from the browser, see below
    uv run ops/seed/push_definitions.py --api https://<api host> --list
    uv run ops/seed/push_definitions.py --api https://<api host> \
        --source-name "MiseQ v6.7" --file ops/seed/miseq_v67_definitions.json

`provision_definitions.py` is the local twin and is the better tool where it
works: it uses the service layer directly and needs no credential. It cannot be
used against dev, because the platform database sits behind a VNet with
`publicNetworkAccess` disabled and `ops/` is not in the API image, so there is
nothing on the inside to run it from. The public API is the remaining door.

**Name the source, not its id.** The first version of this took `--source` as a
UUID and nothing else, and a UUID is a thing a person copies from one browser
tab into another shell — so the run that appeared to succeed had written eight
definitions to a source nobody was looking at. Ids still work; `--source-name`
resolves one, and `--list` prints what is there when neither is certain.

**The token is read from the environment and never written anywhere.** Not into
a file, not into the argument list — an argument is visible in the process table
and lands in shell history. Get one from the browser on a signed-in tab
(Application -> Local Storage, or the `Authorization` header of any request the
app makes on the Network tab) and export it into the shell you run this from.
It is a short-lived user token: it expires, and this script does not persist it.

**It reads back what it wrote.** A push that reports eight created and leaves a
screen empty is the failure this is built against, so the last thing it does is
list the source again and print, per definition, whether the caveat and the
filters actually survived the round trip.

Idempotent by name, like its local twin: a rerun updates rather than
duplicating, so correcting a caveat is editing the JSON and running it again.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

TIMEOUT = httpx.Timeout(30.0)


def _fields(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "description": item["description"],
        "expression": item.get("expression"),
        "caveat": item.get("caveat"),
        "synonyms": item.get("synonyms", []),
        "required_filters": item.get("required_filters", []),
    }


def _orgs(client: httpx.Client, api: str) -> list[dict[str, Any]]:
    response = client.get(f"{api}/v1/me")
    response.raise_for_status()
    payload = response.json()
    return list(payload.get("memberships", []))


def _sources(client: httpx.Client, api: str, org_id: str) -> list[dict[str, Any]]:
    response = client.get(f"{api}/v1/orgs/{org_id}/data-sources")
    response.raise_for_status()
    return list(response.json())


def _resolve(
    client: httpx.Client, api: str, org: str | None, source: str | None, source_name: str | None
) -> tuple[str, str, str]:
    """Turn whatever the caller supplied into one org id and one source id."""
    orgs = _orgs(client, api)
    if not orgs:
        raise SystemExit("This token belongs to no organization.")
    if org is None:
        if len(orgs) > 1:
            names = ", ".join(f"{o['org_name']} ({o['org_id']})" for o in orgs)
            raise SystemExit(f"More than one organization; pass --org. Have: {names}")
        org = str(orgs[0]["org_id"])

    sources = _sources(client, api, org)
    if not sources:
        raise SystemExit(f"Organization {org} has no data source registered.")
    if source is not None:
        match = next((s for s in sources if str(s["id"]) == source), None)
        if match is None:
            names = ", ".join(f"{s['name']} ({s['id']})" for s in sources)
            # Named rather than accepted: the whole point of this script's
            # rewrite is that a wrong id used to be indistinguishable from a
            # right one until somebody opened the screen.
            raise SystemExit(f"No data source with id {source}. Have: {names}")
        return org, source, str(match["name"])
    if source_name is not None:
        matches = [s for s in sources if s["name"] == source_name]
        if not matches:
            names = ", ".join(repr(s["name"]) for s in sources)
            raise SystemExit(f"No data source called {source_name!r}. Have: {names}")
        if len(matches) > 1:
            raise SystemExit(
                f"{len(matches)} data sources are called {source_name!r}; pass --source."
            )
        return org, str(matches[0]["id"]), source_name
    if len(sources) > 1:
        names = ", ".join(f"{s['name']} ({s['id']})" for s in sources)
        raise SystemExit(f"More than one data source; pass --source-name. Have: {names}")
    return org, str(sources[0]["id"]), str(sources[0]["name"])


def _report(client: httpx.Client, base: str, wanted: list[dict[str, Any]]) -> int:
    """List the source again and say what actually landed."""
    response = client.get(base)
    response.raise_for_status()
    live = {row["name"]: row for row in response.json()}

    print(f"\n{'definition':<28} {'caveat':>8}  {'synonyms':>8}  filters")
    print("-" * 62)
    missing = 0
    for item in wanted:
        name = item["name"]
        row = live.get(name)
        if row is None:
            print(f"{name:<28} {'MISSING':>8}")
            missing += 1
            continue
        caveat = row.get("caveat") or ""
        expected = item.get("caveat") or ""
        # Length, not presence: a caveat truncated on the way in is the failure
        # a "yes it is there" check would wave through.
        mark = "ok" if len(caveat) == len(expected) else f"{len(caveat)}/{len(expected)}"
        if mark != "ok":
            missing += 1
        print(
            f"{name:<28} {mark:>8}  {len(row.get('synonyms', [])):>8}  "
            f"{len(row.get('required_filters', []))}"
        )
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", required=True, help="Base URL, e.g. https://api.example.net")
    parser.add_argument("--org", help="Organization id. Resolved when you belong to only one.")
    parser.add_argument("--source", help="Data source id.")
    parser.add_argument("--source-name", help='Data source name, e.g. "MiseQ v6.7".')
    parser.add_argument("--file", default="ops/seed/miseq_definitions.json")
    parser.add_argument(
        "--list", action="store_true", help="Print the organizations and sources, and stop."
    )
    args = parser.parse_args()

    token = os.environ.get("DATAAGENT_TOKEN", "").strip()
    if not token:
        print("Set DATAAGENT_TOKEN in the environment first. See this file's docstring.")
        return 2

    api = args.api.rstrip("/")
    with httpx.Client(
        headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT, follow_redirects=True
    ) as client:
        try:
            if args.list:
                for org in _orgs(client, api):
                    print(f"{org['org_name']}  {org['org_id']}  ({org['role']})")
                    for source in _sources(client, api, str(org["org_id"])):
                        print(f"    {source['name']}  {source['id']}")
                return 0
            org_id, source_id, source_name = _resolve(
                client, api, args.org, args.source, args.source_name
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 401:
                print("The API refused the token. It has probably expired; take a fresh one.")
                return 1
            raise

        wanted: list[dict[str, Any]] = json.loads(Path(args.file).read_text(encoding="utf-8"))[
            "definitions"
        ]
        base = f"{api}/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
        print(f"Organization {org_id}, data source {source_name!r} ({source_id})")
        print(f"{len(wanted)} definition(s) from {args.file}\n")

        listing = client.get(base)
        listing.raise_for_status()
        existing = {row["name"]: row for row in listing.json()}
        created = updated = unchanged = 0

        for item in wanted:
            name = item["name"]
            if name in existing:
                before = existing[name]
                response = client.patch(f"{base}/{before['id']}", json=_fields(item))
                if response.status_code >= 400:
                    print(f"  ! {name}: {response.status_code} {response.text[:300]}")
                    return 1
                after = response.json()
                if after["version"] == before["version"]:
                    print(f"  = {name}")
                    unchanged += 1
                else:
                    print(f"  ~ {name}  (v{before['version']} -> v{after['version']})")
                    updated += 1
                continue
            response = client.post(
                base, json={"name": name, "kind": item.get("kind", "metric"), **_fields(item)}
            )
            if response.status_code >= 400:
                # The API's own message names the column a filter got wrong.
                print(f"  ! {name}: {response.status_code} {response.text[:300]}")
                return 1
            row = response.json()
            print(f"  + {name}  ({'enforced' if row['required_filters'] else 'prose'})")
            created += 1

        print(f"\n{created} created, {updated} updated, {unchanged} already current.")
        problems = _report(client, base, wanted)

    if problems:
        print(f"\n{problems} definition(s) did not land intact. Nothing above is safe to trust.")
        return 1
    print(f"\nAll {len(wanted)} present with their caveats intact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
