"""Run the twenty golden questions and say which ones are right (M9).

    make evals                 # FakeLLM, deterministic, free, required in CI
    EVALS_LIVE=1 make evals    # real models, real money, nightly

**What is being tested differs between the two modes, and saying so matters.**
With the FakeLLM the SQL comes from `golden.yaml`, so the model is not under
test — everything around it is: catalog grounding, the DAL's validation and
masking, execution against the real seed database, the critic's rules, the
composer's citations and limitations. The statements really run and the results
are really compared against `ops/seed/truths.json`. With `EVALS_LIVE=1` a real
model writes the SQL and the prose, the scripts are ignored, and the same checks
apply — which is when `answer_contains` starts meaning anything.

**Every expected number is a path into `truths.json`, never a literal here.**
There is one copy of each number, `make check.truths` fails if it moves, and an
eval that hardcoded 3718 would keep passing after the fixture changed underneath
it — which is the failure this arrangement exists to prevent.

**`as_of` is pinned to 2026-08-16** (D-027, B-005), two weeks past the fixture's
last row. That is the entire mechanism by which *"revenue last full month"*
resolves to July 2026 today and still resolves to July 2026 next year, and it is
why the seed's `END_DATE` could stay frozen.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

#: Two homes, because this runs in two places. From the repository it resolves
#: against the tree; inside the API container `ops/scripts/evals.sh` copies the
#: four files it needs beside it, and the container's own `/app/src` is already
#: importable. Running it inside is not a convenience — the data source is
#: registered with a compose hostname that resolves nowhere else.
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for candidate in (REPO_ROOT / "apps" / "api" / "src", REPO_ROOT / "apps" / "api" / "tests", HERE):
    if candidate.exists():
        sys.path.insert(0, str(candidate))

GOLDEN = HERE / "golden.yaml"
TRUTHS = next(
    path
    for path in (REPO_ROOT / "ops" / "seed" / "truths.json", HERE / "truths.json")
    if path.exists()
)

#: Pinned, and the reason is D-027. Two weeks past the seed's last row.
AS_OF = date(2026, 8, 16)

#: Live runs cost money. A run that has spent this much stops, so a loop that
#: goes wrong on a schedule cannot bill all night.
TOKEN_BUDGET = int(os.environ.get("EVALS_TOKEN_BUDGET", "400000"))


@dataclass
class Result:
    """One question's verdict, and enough to see why."""

    id: int
    question: str
    passed: bool = True
    failures: list[str] = field(default_factory=list["str"])
    answered: bool = False
    citations: int = 0
    iterations: int = 0
    queries: int = 0
    limitations: int = 0
    tokens: int = 0
    answer: str = ""
    #: What the critic said, when it said anything. Surfaced on a failure
    #: because it is almost always the *reason*: an eval that reports "did not
    #: answer" and stops has hidden the one line that explains why.
    critic: list[str] = field(default_factory=list["str"])

    def fail(self, why: str) -> None:
        self.passed = False
        self.failures.append(why)


def truth_at(truths: dict[str, Any], path: str) -> Any:
    """Resolve a dotted path, with negative integers indexing from the end.

    `revenue.by_month.-1.revenue` is the last month's revenue, which is what
    "last full month" means once `as_of` is pinned — and writing it as a path
    rather than as `2026-07` keeps the eval correct if the seed's window moves.
    """
    node: Any = truths
    for part in path.split("."):
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def close_enough(got: Any, want: Any, tolerance: float) -> bool:
    """Numbers within tolerance; everything else compared as text.

    Text comparison is case-insensitive and stripped, because `'saturday'` and
    `'Saturday '` are the same weekday and an eval that failed on that would be
    testing `TO_CHAR` padding.
    """
    try:
        left, right = float(got), float(want)
    except (TypeError, ValueError):
        return str(got).strip().lower() == str(want).strip().lower()
    if tolerance <= 0:
        return left == right
    scale = max(abs(right), 1.0)
    return abs(left - right) / scale <= tolerance


# ---------------------------------------------------------------------------
# Scripting the model
# ---------------------------------------------------------------------------


def script_for(case: dict[str, Any], fake: Any) -> None:
    """Teach the FakeLLM to answer this question, step by step.

    Each iteration costs a `sql` call and a `plan` (reflect) call, then one
    `compose` and one `critic` — the arithmetic D-028 counts. `times=1` on every
    script, because a script with no limit answers every later call of the same
    role and the loop would then propose the same statement twice and refuse it
    as a duplicate.
    """
    from dataagent.agent.critic import CriticOut
    from dataagent.agent.loop import ReflectFinding, Reflection
    from dataagent.agent.planner import Plan

    steps = case["script"].get("steps")
    if steps is None:
        refusal = case["script"].get("refuse")
        if refusal is not None:
            fake.script(
                Plan(
                    sql="SELECT 1", purpose="check", answerable=False, reason=refusal
                ).model_dump_json(),
                role="sql",
                times=1,
            )
            return
        steps = [{"sql": case["script"]["sql"]}]

    for index, step in enumerate(steps):
        last = index == len(steps) - 1
        fake.script(
            Plan(
                sql=step["sql"], purpose=f"step {index + 1}", answerable=True, reason=""
            ).model_dump_json(),
            role="sql",
            times=1,
        )
        found = step.get("finding")
        fake.script(
            Reflection(
                findings=(
                    [ReflectFinding(statement=found, supported_by=[], confidence="medium")]
                    if found
                    else []
                ),
                open_questions=[],
                next_purpose="" if last else "keep going",
                done=last,
                rationale="that is enough" if last else "more to do",
            ).model_dump_json(),
            role="plan",
            times=1,
        )

    fake.script(_compose_from_prompt, role="compose", times=1)
    fake.script(CriticOut(verdict="pass", reasons=[]).model_dump_json(), role="critic", times=1)


_UUID_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def _compose_from_prompt(request: Any) -> str:
    """Compose by citing whatever executions the prompt actually names.

    The ids are minted at run time, so they cannot be scripted. Reading them back
    out of the prompt is also a real assertion in disguise: if the runner ever
    stops showing the composer its execution ids, there is nothing to find and
    this raises rather than quietly citing nothing.
    """
    import re

    from dataagent.agent.tools.finalize import FinalizeIn

    text = getattr(request, "prompt_text", "")
    ids = list(dict.fromkeys(re.findall(_UUID_PATTERN, text)))
    if not ids:
        raise AssertionError("the composing prompt named no execution")
    return FinalizeIn(
        answer=f"Answered from {len(ids)} query result(s).",
        answered=True,
        supported_by=ids,
        confidence="high",
    ).model_dump_json()


# ---------------------------------------------------------------------------
# Running one question
# ---------------------------------------------------------------------------


async def resolve_target() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """The organization, data source and user to ask as.

    Taken from the environment when given, otherwise the single registered
    PostgreSQL source is used and anything ambiguous is refused rather than
    guessed — the same rule `agent_smoke.py` follows, and for the same reason:
    an eval that quietly ran against the wrong database would report confidently
    about numbers from somewhere else.
    """
    from sqlalchemy import text

    from dataagent.datasources import service as datasources
    from dataagent.tenancy.session import org_session

    org_env = os.environ.get("EVALS_ORG_ID")
    if not org_env:
        raise SystemExit(
            "EVALS_ORG_ID is not set. The eval harness needs an organization with "
            "the pizza seed registered as a data source — `make up && make seed` "
            "and then register it, the same state `make agent.smoke` needs."
        )
    org_id = uuid.UUID(org_env)

    wanted = os.environ.get("EVALS_SOURCE", "Demo")
    sources = await datasources.list_data_sources(org_id)
    matching = [source for source in sources if source.name == wanted]
    if not matching:
        listed = ", ".join(source.name for source in sources) or "none"
        raise SystemExit(f"No data source named {wanted!r} in this org. There is: {listed}")

    async with org_session(org_id) as session:
        user_id = uuid.UUID(
            str(
                (
                    await session.execute(
                        text("SELECT user_id FROM org_memberships WHERE org_id = :o LIMIT 1"),
                        {"o": org_id},
                    )
                ).scalar_one()
            )
        )
    return org_id, matching[0].id, user_id


async def run_case(
    case: dict[str, Any],
    truths: dict[str, Any],
    target: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    *,
    live: bool,
) -> Result:
    """Ask one question through the whole stack and check what came back."""
    from sqlalchemy import text

    from dataagent.agent.runner import execute_run
    from dataagent.runs import service as runs
    from dataagent.tenancy.session import org_session

    org_id, source_id, user_id = target
    result = Result(id=case["id"], question=case["question"])

    settings = None
    if not live:
        from dataagent.llm.fake import FakeLLM
        from dataagent.llm.registry import clear_provider_cache, register_provider
        from llm_fixture import build_settings

        fake = FakeLLM()
        # A factory, not the instance: the registry builds providers on demand.
        # Registered per case and the cache cleared with it, so one question's
        # scripts can never answer the next question's calls — the same reason
        # the test fixture tears itself down.
        clear_provider_cache()
        register_provider("fake", lambda: fake)
        script_for(case, fake)
        settings = build_settings()

    conversation = await runs.create_conversation(
        org_id=org_id, user_id=user_id, title=f"eval {case['id']}"
    )
    asked = await runs.post_message(
        org_id=org_id,
        user_id=user_id,
        conversation_id=conversation.id,
        content=case["question"],
        idempotency_key=uuid.uuid4().hex,
    )
    outcome = await execute_run(
        org_id=org_id,
        run_id=asked.run_id,
        data_source_id=source_id,
        actor_user_id=user_id,
        settings=settings,
        as_of=AS_OF,
    )

    result.answered = outcome.answered
    result.answer = outcome.answer
    result.citations = len(outcome.execution_ids)
    result.iterations = outcome.iterations
    result.limitations = len(outcome.limitations)
    result.tokens = await tokens_spent(org_id, asked.run_id)
    result.critic = await critic_reasons(org_id, asked.run_id)

    async with org_session(org_id) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id::text AS id, sql_text, row_count, status "
                    "FROM query_executions WHERE run_id = :r ORDER BY created_at"
                ),
                {"r": asked.run_id},
            )
        ).all()
    result.queries = len(rows)

    artifacts = await artifacts_for(org_id, [row.id for row in rows])
    _check(case, truths, result, rows, artifacts, live=live)
    return result


def _check(
    case: dict[str, Any],
    truths: dict[str, Any],
    result: Result,
    rows: list[Any],
    artifacts: dict[str, dict[str, Any]],
    *,
    live: bool,
) -> None:
    """Every check the case declares, against what the run actually did."""
    expect = case.get("expect", {})

    if expect.get("must_refuse"):
        if result.answered:
            result.fail("answered a question the schema cannot answer")
    elif not result.answered:
        # The critic's own words first: they say *why*, where "did not answer"
        # only says that it did not.
        for reason in result.critic:
            result.fail(f"the critic blocked it: {reason}")
        if not result.critic:
            result.fail(f"did not answer: {result.answer[:120] or '(no answer was composed)'}")

    if "queries_run" in expect and result.queries != expect["queries_run"]:
        result.fail(f"ran {result.queries} queries, expected {expect['queries_run']}")

    if result.citations < expect.get("must_cite", 0):
        result.fail(f"cited {result.citations}, expected at least {expect.get('must_cite')}")

    if "min_iterations" in expect and result.iterations < expect["min_iterations"]:
        result.fail(f"took {result.iterations} steps, expected at least {expect['min_iterations']}")

    if "limitations_min" in expect and result.limitations < expect["limitations_min"]:
        result.fail(f"carried {result.limitations} limitations, expected more")

    executed = " ".join(row.sql_text for row in rows)
    for needle in expect.get("sql_contains", []):
        if needle not in executed:
            result.fail(f"no query mentioned {needle!r}")
    for banned in expect.get("sql_excludes", []):
        if banned in executed:
            result.fail(f"a query used {banned!r}, which D-027 forbids")

    phrase = expect.get("answer_contains")
    if live and phrase and phrase.lower() not in result.answer.lower():
        result.fail(f"the answer never said {phrase!r}")

    if "truth" in case and (column := expect.get("value_of")):
        want = truth_at(truths, case["truth"])
        got = _single_value(artifacts, column)
        if got is None:
            result.fail(f"no result had a column called {column!r}")
        elif not close_enough(got, want, float(case.get("tolerance", 0))):
            result.fail(f"{column} was {got!r}, expected {want!r}")

    if "value_is" in expect and (column := expect.get("value_of")):
        got = _single_value(artifacts, column)
        if got is None or not close_enough(got, expect["value_is"], 0):
            result.fail(f"{column} was {got!r}, expected {expect['value_is']!r}")

    if "rows" in expect:
        counts = [row.row_count for row in rows if row.status == "ok"]
        if expect["rows"] not in counts:
            result.fail(f"no query returned {expect['rows']} rows; got {counts}")


def _single_value(artifacts: dict[str, dict[str, Any]], column: str) -> Any:
    """The named column's first value, from the stored result artifact.

    Read from what the DAL persisted — already masked — rather than by running
    the query again. Two reasons: a second run could return something different,
    and the artifact is what a citation opens, so this checks the number a person
    would actually see rather than one only the harness can reach.
    """
    for frame in artifacts.values():
        names = frame.get("columns") or []
        rows = frame.get("rows") or []
        if column in names and rows:
            return rows[0][names.index(column)]
    return None


async def critic_reasons(org_id: uuid.UUID, run_id: uuid.UUID) -> list[str]:
    """Every blocking finding the critic recorded, newest verdict last.

    Read from the trace rather than from the outcome, because a run that was
    blocked and then failed for want of a second script never returns a verdict
    to its caller — and that is exactly the run whose reason someone needs.
    """
    from dataagent.runs.events import read_events

    reasons: list[str] = []
    for event in await read_events(org_id=org_id, run_id=run_id):
        if event.type != "critic_verdict":
            continue
        findings = event.payload.get("findings") or []
        if not isinstance(findings, list):
            continue
        for finding in findings:
            if isinstance(finding, dict) and finding.get("severity") == "block":
                reasons.append(str(finding.get("detail", "")))
    return reasons


async def tokens_spent(org_id: uuid.UUID, run_id: uuid.UUID) -> int:
    """What this run cost, from the ledger rather than from anyone's tally.

    `usage_ledger` is the authoritative record, and reading it here is what makes
    `EVALS_TOKEN_BUDGET` a ceiling rather than a hope: a live suite that has gone
    wrong stops, instead of billing until somebody notices in the morning.
    """
    from sqlalchemy import text

    from dataagent.tenancy.session import org_session

    async with org_session(org_id) as session:
        total = (
            await session.execute(
                text(
                    "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) "
                    "FROM usage_ledger WHERE run_id = :r"
                ),
                {"r": run_id},
            )
        ).scalar_one()
    return int(total or 0)


async def artifacts_for(org_id: uuid.UUID, execution_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Every stored result for this run, keyed by execution."""
    if not execution_ids:
        return {}

    from sqlalchemy import text

    from dataagent.tenancy.session import org_session

    async with org_session(org_id) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT query_execution_id::text AS id, summary, sample_rows "
                    "FROM result_artifacts WHERE query_execution_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": execution_ids},
            )
        ).all()
    return {
        row.id: {
            "columns": list((row.summary or {}).get("columns") or []),
            "rows": list(row.sample_rows or []),
            "masked": list((row.summary or {}).get("masked_columns") or []),
        }
        for row in rows
    }


# ---------------------------------------------------------------------------
# The suite
# ---------------------------------------------------------------------------


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", type=int, nargs="*", help="Run just these ids")
    parser.add_argument("--json", action="store_true", help="Machine-readable summary")
    args = parser.parse_args(argv)

    import yaml
    from dotenv import load_dotenv

    if (env := REPO_ROOT / ".env").exists():
        load_dotenv(env)
    live = os.environ.get("EVALS_LIVE") == "1"

    cases = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    truths = json.loads(TRUTHS.read_text(encoding="utf-8"))
    if args.only:
        cases = [case for case in cases if case["id"] in args.only]

    target = await resolve_target()
    mode = "LIVE — real models, real money" if live else "FakeLLM — deterministic"
    print(f"{len(cases)} golden questions · {mode} · as_of {AS_OF}\n")

    results: list[Result] = []
    spent = 0
    for case in cases:
        # Checked *before* the question, not after: a budget that stops once it
        # is already over is a report rather than a ceiling.
        if live and spent >= TOKEN_BUDGET:
            print(f"\n  stopped: {spent:,} tokens against a budget of {TOKEN_BUDGET:,}")
            break
        try:
            outcome = await run_case(case, truths, target, live=live)
        except Exception as error:
            outcome = Result(id=case["id"], question=case["question"])
            outcome.fail(f"{type(error).__name__}: {error}")
        spent += outcome.tokens
        results.append(outcome)
        mark = "PASS" if outcome.passed else "FAIL"
        cost = f"  {outcome.tokens:>7,} tok" if live else ""
        print(f"  [{mark}] {outcome.id:>2}. {outcome.question[:56]}{cost}")
        for why in outcome.failures:
            print(f"          {why}")

    passed = sum(1 for outcome in results if outcome.passed)
    tail = f" · {spent:,} tokens" if live else ""
    print(f"\n{passed}/{len(results)} passed{tail}")
    if len(results) < len(cases):
        # An unasked question is not a passing one.
        print(f"{len(cases) - len(results)} question(s) were never asked")
        return 1
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "id": r.id,
                        "passed": r.passed,
                        "failures": r.failures,
                        "citations": r.citations,
                        "iterations": r.iterations,
                        "queries": r.queries,
                        "limitations": r.limitations,
                    }
                    for r in results
                ],
                indent=1,
            )
        )
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
