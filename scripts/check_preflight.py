#!/usr/bin/env python
"""`make preflight` really is every CI step it does not explicitly excuse.

**Why this exists, in one sentence: the tool's claim was false in the most
expensive direction available to it.** `preflight` printed *"Preflight clean.
Safe to push."* while skipping `test.dal` — the DAL suite's **90% coverage gate**,
plan §4.4's requirement on the security boundary — and `test.rls`, whose whole
purpose is to fail when the tenant-isolation proofs collect *nothing*. Of every
step it could have quietly dropped, it dropped the two guarding the boundary the
architecture says is not negotiable.

It had already been corrected twice for the same overstatement, once for
`test.web.e2e` and once for `compile.web`, each time by adding the missing target
and moving on. A third correction without a guard would have been a promise to be
more careful, which is what the first two were.

So the claim is checked. Three assertions, and the third is the one that makes
this more than a second list to drift:

1. **Every CI step is accounted for** — either covered by a `preflight`
   prerequisite or excused in writing. A new job cannot arrive unnoticed.
2. **Every entry still matches a real step**, so a stale excuse cannot outlive
   the step it excused and quietly exempt something else.
3. **Every target claimed as coverage is genuinely reachable from `preflight`**,
   read out of the Makefile itself. Without this, the manifest could claim
   `test.dal` while the Makefile had dropped it, and the guard would agree with
   the lie it was written to catch.

Run with `--selftest` to prove it still fails when it should (B-019's rule: a
guard that has stopped catching anything must fail loudly, not pass everything).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
MAKEFILE = ROOT / "Makefile"
MANIFEST = Path(__file__).resolve().parent / "preflight_coverage.json"

#: Actions that set a job up rather than check anything: no local equivalent is
#: owed for a checkout or a language install. Anything else using `uses:` is a
#: check and must be accounted for — `gitleaks` is the reason this list is a
#: list and not "ignore every `uses:` step".
SETUP_ACTIONS = (
    "actions/checkout",
    "actions/setup-node",
    "actions/setup-python",
    "actions/cache",
    "actions/upload-artifact",
    "actions/download-artifact",
    "astral-sh/setup-uv",
    "pnpm/action-setup",
    "docker/setup-buildx-action",
    "dorny/paths-filter",
)


def ci_steps(text: str) -> dict[str, str]:
    """Every step CI runs that is a check, as ``job/step name`` → what it does."""
    document: dict[str, Any] = yaml.safe_load(text)
    found: dict[str, str] = {}
    for job_name, job in (document.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            name = step.get("name")
            if not name:
                continue
            if "run" in step:
                found[f"{job_name}/{name}"] = " ".join(str(step["run"]).split())[:120]
                continue
            uses = str(step.get("uses", ""))
            if uses and not uses.startswith(SETUP_ACTIONS):
                found[f"{job_name}/{name}"] = f"uses {uses}"
    return found


def make_targets(text: str) -> dict[str, list[str]]:
    """Each Makefile target and the targets it depends on."""
    rule = re.compile(r"^([A-Za-z][\w.-]*)\s*:([^=;]*)$", re.M)
    graph: dict[str, list[str]] = {}
    for match in rule.finditer(text):
        prerequisites = match.group(2).split("##")[0].split()
        graph[match.group(1)] = prerequisites
    return graph


def reachable(graph: dict[str, list[str]], start: str) -> set[str]:
    """`start` and everything it pulls in, so umbrella targets expand."""
    seen: set[str] = set()
    stack = [start]
    while stack:
        target = stack.pop()
        if target in seen:
            continue
        seen.add(target)
        stack.extend(graph.get(target, []))
    return seen


def check(workflow: str, makefile: str, manifest: dict[str, Any]) -> list[str]:
    """Every problem found, as sentences. Empty means the claim holds."""
    problems: list[str] = []
    steps = ci_steps(workflow)
    covered: dict[str, str] = manifest.get("covered", {})
    excluded: dict[str, str] = manifest.get("excluded", {})
    accounted = set(covered) | set(excluded)

    unlisted = sorted(set(steps) - accounted)
    for key in unlisted:
        problems.append(
            f"CI runs '{key}' and preflight does not account for it. Add the target "
            f"to `preflight` and list it under 'covered', or write why not under "
            f"'excluded'. What it runs: {steps[key]}"
        )

    for key in sorted(accounted - set(steps)):
        problems.append(
            f"'{key}' is in {MANIFEST.name} and no longer exists in ci.yml. A stale "
            f"entry outlives the step it described and can exempt the next one."
        )

    for key in sorted(set(covered) & set(steps)):
        graph = make_targets(makefile)
        within = reachable(graph, "preflight")
        for target in covered[key].split():
            if target not in within:
                problems.append(
                    f"'{key}' claims coverage by `{target}`, which `preflight` does "
                    f"not reach. This is the failure the guard exists for: the "
                    f"manifest agreeing with a claim the Makefile stopped keeping."
                )

    for key in sorted(set(excluded) & set(steps)):
        if not excluded[key].strip():
            problems.append(f"'{key}' is excluded with no reason given.")
    return problems


def selftest() -> int:
    """Prove the check still fails on each thing it is supposed to catch."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    makefile = MAKEFILE.read_text(encoding="utf-8")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cases: list[tuple[str, list[str]]] = []

    grown = yaml.safe_load(workflow)
    grown["jobs"]["hygiene"]["steps"].append({"name": "A brand new check", "run": "true"})
    cases.append(("a new CI step nobody accounted for", check(yaml.dump(grown), makefile, manifest)))

    stale = dict(manifest)
    stale["excluded"] = {**manifest.get("excluded", {}), "hygiene/A step that was deleted": "gone"}
    cases.append(("an entry for a step that no longer exists", check(workflow, makefile, stale)))

    lying = json.loads(json.dumps(manifest))
    first = next(iter(lying["covered"]))
    lying["covered"][first] = "a.target.preflight.does.not.reach"
    cases.append(("coverage claimed by a target preflight does not reach", check(workflow, makefile, lying)))

    blank = json.loads(json.dumps(manifest))
    if blank.get("excluded"):
        blank["excluded"][next(iter(blank["excluded"]))] = "   "
        cases.append(("an exclusion with no reason", check(workflow, blank and makefile, blank)))

    failures = 0
    for description, problems in cases:
        if problems:
            print(f"  ok    {description} is caught")
        else:
            print(f"  FAIL  {description} passed silently")
            failures += 1
    print(f"check_preflight --selftest: {len(cases) - failures} passed, {failures} failed")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return selftest()
    problems = check(
        WORKFLOW.read_text(encoding="utf-8"),
        MAKEFILE.read_text(encoding="utf-8"),
        json.loads(MANIFEST.read_text(encoding="utf-8")),
    )
    if problems:
        print("preflight no longer covers what CI runs:\n")
        for problem in problems:
            print(f"  - {problem}\n")
        return 1
    print("check_preflight: preflight accounts for every step ci.yml runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
