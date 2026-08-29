"""Load a definitions file into a **deployed** environment over the public API.

    export DATAAGENT_TOKEN='...'        # from the browser, see below
    uv run ops/seed/push_definitions.py \
        --api https://<the api host> --org <org id> --source <data source id>

`provision_definitions.py` is the local twin and is the better tool where it
works: it uses the service layer directly and needs no credential. It cannot be
used against dev, because the platform database sits behind a VNet with
`publicNetworkAccess` disabled and `ops/` is not in the API image, so there is
nothing on the inside to run it from. The public API is the remaining door.

**The token is read from the environment and never written anywhere.** Not into
a file, not into the argument list — an argument is visible in the process table
and lands in shell history. Get one from the browser on a signed-in tab
(Application -> Local Storage, or the `Authorization` header of any request the
app makes on the Network tab) and export it into the shell you run this from.
It is a short-lived user token: it expires, and this script does not persist it.

Idempotent by name, like its local twin: a rerun updates rather than duplicating,
so correcting a caveat is editing the JSON and running it again.
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", required=True, help="Base URL, e.g. https://api.example.net")
    parser.add_argument("--org", required=True, help="Organization id")
    parser.add_argument("--source", required=True, help="Data source id")
    parser.add_argument("--file", default="ops/seed/miseq_definitions.json")
    args = parser.parse_args()

    token = os.environ.get("DATAAGENT_TOKEN", "").strip()
    if not token:
        print("Set DATAAGENT_TOKEN in the environment first. See this file's docstring.")
        return 2

    wanted: list[dict[str, Any]] = json.loads(Path(args.file).read_text(encoding="utf-8"))[
        "definitions"
    ]

    base = f"{args.api.rstrip('/')}/v1/orgs/{args.org}/data-sources/{args.source}/definitions"
    created = updated = unchanged = 0

    with httpx.Client(
        headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT, follow_redirects=True
    ) as client:
        listing = client.get(base)
        if listing.status_code == 401:
            print("The API refused the token. It has probably expired; take a fresh one.")
            return 1
        listing.raise_for_status()
        existing = {row["name"]: row for row in listing.json()}

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
