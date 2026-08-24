"""An engine trial: point the product at an unfamiliar dataset and see where it breaks.

**Why this exists, and why it is not the eval harness.** The golden evals assert
known answers against a known fixture, and they are the right tool for *"did this
change break something we already understood"*. They cannot find what this finds.
Five of the sharpest defects in this repository — **B-057** (join direction),
**B-060** (two defensible sources, answers two orders of magnitude apart),
**B-085** (imported metrics nothing could reach), **B-092** (a code column read as
if every value were equally common) and **B-119** (a fabricated join prohibition)
— all came from pointing the agent at a database nobody had modelled for it and
asking questions whose answers nobody knew yet. None was reachable from the test
suite, which is the defect class CLAUDE.md opens with. The method worked and lived
only in the owner's habit; this is the habit written down.

**Every probe runs at least three times, and that is not a tuning knob.**
`MINIMUM_REPEATS` is enforced rather than defaulted. On 2026-08-25, B-119's exact
question — asked once, on a build whose prompt path was byte-identical to the run
that produced the fabrication — came back correct. A single-shot trial would have
recorded a live P1 as fixed. **A trial that asks each question once measures the
model's luck on the day**, and the defects worth finding here are exactly the ones
that do not happen every time.

**So the output is a comparison, not a transcript.** What a reader needs is not
three answers but the places the three disagree: a question that refused once and
answered twice, or read `fact_sale` twice and `fact_waste` once, is a finding
before anybody reads a word of prose. `divergences` is the whole product of this
module; the per-run records exist to make it checkable.

**What this does not do.** It does not decide whether an answer is *right* — no
program here can, which is the premise of the exercise — and it does not compare
against expected values. It surfaces the runs and their disagreements, and a
person judges. Anything it flags belongs in `BACKLOG.md` with the numbers in it,
in the shape B-060 and B-119 set.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, cast

from sqlalchemy import select

from dataagent.agent.runner import execute_run
from dataagent.db.models import AgentRun, QueryExecution
from dataagent.runs import service as runs
from dataagent.tenancy.session import org_session

#: **Three, and the floor is enforced.** See the module docstring: one run of
#: B-119's question on 2026-08-25 would have closed a live P1. Three is the
#: smallest number that can show a disagreement *and* which side is the outlier;
#: two shows only that they differ.
MINIMUM_REPEATS: Final = 3

#: Numbers in an answer, for the coarse "did they say the same thing" check.
#: Deliberately crude — see `_figures`.
_NUMBER = re.compile(r"\d[\d,]*\.?\d*")


@dataclass(frozen=True, slots=True)
class ProbeRun:
    """One run of one probe. Everything a person needs to judge it, and no prose.

    Assembled from the run's own record rather than from the model's account of
    itself: `tables_read` is what the **validator** resolved on each execution,
    not what the answer claims it read, for the reason B-093 gives.
    """

    run_id: uuid.UUID
    status: str
    answered: bool | None
    answer: str
    #: What context offered that had figures to aggregate. The other half of
    #: B-093: naming both is what lets a reader see that a choice existed.
    sources_offered: tuple[str, ...]
    tables_read: tuple[str, ...]
    statements: tuple[str, ...]
    limitations: tuple[str, ...]
    method: str
    findings: int

    @property
    def refused(self) -> bool:
        """A completed run that did not answer (**B-133**, WP7.2b's rule)."""
        return self.status == "completed" and self.answered is False


@dataclass(frozen=True, slots=True)
class ProbeResult:
    question: str
    runs: tuple[ProbeRun, ...]
    divergences: tuple[str, ...] = field(default_factory=tuple)


def _figures(answer: str) -> tuple[str, ...]:
    """The numbers an answer states, normalised for comparison.

    **Crude on purpose, and its crudeness is the safe direction.** It cannot tell
    a row count from a currency amount, so it over-reports disagreement rather
    than under-reporting it — and a false flag costs a person ten seconds while a
    missed one costs what B-060 cost. Dates are the common false positive and are
    left in: two runs that resolved *"last month"* differently is precisely the
    thing B-005 was about.
    """
    return tuple(sorted({match.group(0).replace(",", "") for match in _NUMBER.finditer(answer)}))


def divergences(runs: tuple[ProbeRun, ...]) -> tuple[str, ...]:
    """Where the runs of one probe disagree. Empty means they agreed.

    **Agreement is not correctness.** Three runs can be consistently wrong, and
    B-060's four readings were each defensible on their own. What this rules out
    is the *other* failure — a defect that shows up one time in three and is
    invisible to anyone who asked once.
    """
    found: list[str] = []

    endings = Counter("refused" if r.refused else r.status for r in runs)
    if len(endings) > 1:
        found.append(
            "the runs ended differently: "
            + ", ".join(f"{count}x {name}" for name, count in sorted(endings.items()))
            + " — a question that refuses sometimes and answers other times is B-119's shape"
        )

    read = {r.tables_read for r in runs}
    if len(read) > 1:
        found.append(
            "they read different tables: "
            + " | ".join(", ".join(t) or "(none)" for t in sorted(read))
            + " — two defensible sources for one question is B-060's shape"
        )

    figures = {_figures(r.answer) for r in runs if r.answer}
    if len(figures) > 1:
        found.append(
            "they stated different numbers: "
            + " | ".join(", ".join(f) or "(none)" for f in sorted(figures))
            + " — check whether the difference is the answer or the wording"
        )

    counts = {len(r.statements) for r in runs}
    if len(counts) > 1:
        found.append(
            f"they ran different numbers of queries: {sorted(counts)} — not a defect by "
            "itself, and worth reading beside the rest"
        )

    return tuple(found)


async def _record(org_id: uuid.UUID, run_id: uuid.UUID) -> ProbeRun:
    """Read back what the run actually did, from the platform's own rows."""
    view = await runs.get_run(org_id=org_id, run_id=run_id)

    async with org_session(org_id) as session:
        rows = (
            (
                await session.execute(
                    select(QueryExecution)
                    .where(QueryExecution.run_id == run_id)
                    .order_by(QueryExecution.created_at)
                )
            )
            .scalars()
            .all()
        )
        # **From the run's checkpoint, because `RunView` does not carry it.** The
        # first version of this read `view.grounding.candidate_sources`, which
        # does not exist — `_grounding` is a function returning a tuple, and the
        # field would have been silently empty on every run. That is the defect
        # this whole module exists to find, written into the module itself, and it
        # is why `offered_sources` has a test that fails when the key is absent
        # rather than returning nothing quietly.
        state = (
            await session.execute(select(AgentRun.state).where(AgentRun.id == run_id))
        ).scalar_one_or_none()

    tables: list[str] = []
    for row in rows:
        for table in row.tables:
            if table not in tables:
                tables.append(table)

    return ProbeRun(
        run_id=run_id,
        status=view.status,
        answered=view.answered,
        answer=view.answer or "",
        sources_offered=tuple(offered_sources(state)),
        tables_read=tuple(sorted(tables)),
        statements=tuple(row.sql_text for row in rows),
        limitations=tuple(view.limitations),
        method=view.method,
        findings=len(view.findings),
    )


def offered_sources(state: object) -> list[str]:
    """What context offered that had figures to aggregate (**B-093**).

    Read out of the run's `state` checkpoint, which is a JSON column written by a
    model of the loop that has changed before and will again — so this is
    defensive in the same way `_grounding` is, and for the same reason: a run
    recorded before the field existed must read as "nothing recorded" rather than
    raise on a report whose whole job is explaining what happened.

    **Empty is a real answer here**, not a failure: a run that refused before
    context was built genuinely had nothing offered, and those are among the runs
    most worth looking at.
    """
    if not isinstance(state, dict):
        return []
    stored = cast("dict[str, object]", state)
    candidates = stored.get("candidate_sources")
    if not isinstance(candidates, list):
        return []
    return sorted(str(candidate) for candidate in cast("list[object]", candidates))


async def run_probe(
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    data_source_id: uuid.UUID,
    question: str,
    repeats: int,
) -> ProbeResult:
    """Ask one question `repeats` times and compare the runs.

    **Each repeat is its own run in the same conversation**, which is how a person
    would do it, and it means the second and third carry the first as history
    (D-029). That is deliberate: the thread is part of the product, so a trial that
    stripped it would be measuring something nobody uses. It also means a
    divergence can come *from* the thread, which is itself worth knowing.
    """
    if repeats < MINIMUM_REPEATS:
        raise ValueError(
            f"repeats={repeats} is below the floor of {MINIMUM_REPEATS}. One run of "
            "B-119's question came back clean on 2026-08-25 against a build whose "
            "prompt path was unchanged, so a single-shot trial would have closed a "
            "live P1. Asking once measures the model's luck on the day."
        )

    records: list[ProbeRun] = []
    for _ in range(repeats):
        asked = await runs.post_message(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            content=question,
            idempotency_key=uuid.uuid4().hex,
        )
        await execute_run(
            org_id=org_id,
            run_id=asked.run_id,
            data_source_id=data_source_id,
            actor_user_id=user_id,
        )
        records.append(await _record(org_id, asked.run_id))

    runs_tuple = tuple(records)
    return ProbeResult(question=question, runs=runs_tuple, divergences=divergences(runs_tuple))


def render(results: list[ProbeResult]) -> str:
    """The report. Divergences first, because they are the reason to read it."""
    lines: list[str] = []
    flagged = [r for r in results if r.divergences]

    lines.append(f"trial: {len(results)} probe(s), {len(flagged)} with disagreements")
    lines.append("")

    if flagged:
        lines.append("=== where the runs disagreed ===")
        for result in flagged:
            lines.append(f"  {result.question}")
            for note in result.divergences:
                lines.append(f"    - {note}")
        lines.append("")

    lines.append("=== every run ===")
    for result in results:
        lines.append(f"  {result.question}")
        for index, run in enumerate(result.runs, start=1):
            ending = "refused" if run.refused else run.status
            lines.append(
                f"    {index}. {ending}  tables={','.join(run.tables_read) or '-'}  "
                f"queries={len(run.statements)}  findings={run.findings}"
            )
            if run.answer:
                lines.append(f"       {run.answer[:160]}")
            for limitation in run.limitations:
                lines.append(f"       caveat: {limitation[:140]}")
        lines.append("")

    return "\n".join(lines)


async def run_trial(
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    source_id: uuid.UUID,
    probes: list[str],
    repeats: int,
) -> str:
    """Every probe, in one conversation, rendered.

    **No file I/O here.** `main` reads the probes and writes the report, because
    `pathlib` on the event loop is blocking — the objection `ruff`'s ASYNC240
    raises and the one **B-023** already records against the artifact store. It
    would be harmless in a one-shot CLI and it would also be one more place the
    rule is quietly not kept.
    """
    conversation = await runs.create_conversation(
        org_id=org_id, user_id=user_id, title="engine trial", data_source_id=source_id
    )

    results = [
        await run_probe(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation.id,
            data_source_id=source_id,
            question=question,
            repeats=repeats,
        )
        for question in probes
    ]
    return render(results)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", required=True, help="Organization id.")
    parser.add_argument("--user", required=True, help="Actor user id.")
    parser.add_argument("--source", required=True, help="Data source id to ask against.")
    parser.add_argument("--probes", required=True, help="JSON file: a list of questions.")
    parser.add_argument(
        "--repeats",
        type=int,
        default=MINIMUM_REPEATS,
        help=f"Runs per probe. Refused below {MINIMUM_REPEATS} — see the module docstring.",
    )
    parser.add_argument("--out", help="Also write the report here.")
    args = parser.parse_args()

    probes: list[str] = json.loads(Path(args.probes).read_text(encoding="utf-8"))
    report = asyncio.run(
        run_trial(
            org_id=uuid.UUID(args.org),
            user_id=uuid.UUID(args.user),
            source_id=uuid.UUID(args.source),
            probes=probes,
            repeats=args.repeats,
        )
    )
    print(report)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"\nwritten to {args.out}")

    # **Exit 0 even when probes disagreed.** A disagreement is a finding for a
    # person, not a build failure — this is not a gate and must not become one by
    # accident, or it will end up run with whatever flag makes it quiet.


if __name__ == "__main__":
    main()
