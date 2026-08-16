"""The answer, and everything a reader needs to disbelieve it (arch 4.2, M9).

An answer is four things, and this module is where the last three stop being
optional. **The answer itself** in plain words. **The evidence** — which query
produced which number, as execution ids a person can open. **The method**, in one
line, so "how did you get this" has an answer that is not the SQL. And the
**limitations**: what this answer does not establish.

**Limitations are assembled, not requested.** A model asked to list its own
caveats produces either nothing or a paragraph of hedging, and neither is
information. So this module builds them from things the run *knows*: the budget
that stopped the search, the critic's warnings, a period the data does not cover,
an answer resting on a single row. The model writes prose; the platform writes
the caveats, because the platform is the part that cannot be talked out of them.

**A limitation is never a substitute for the answer.** They render beside it, and
an empty list is the common case and a good one — a run that answered cleanly
should not be made to sound uncertain. The failure this guards against is the
opposite one: a partial answer presented as complete, which is what architecture
4.4 added budgets to make visible in the first place.

**Findings are marked cited when the answer rests on them.** A run reaches
several conclusions and an answer uses some. Without the mark the card shows
every intermediate step, which buries the answer, or none, which hides the
evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dataagent.agent.critic import CriticVerdict
from dataagent.agent.state import ResearchState
from dataagent.agent.tools.finalize import FinalizeIn

__all__ = ["ComposedAnswer", "assemble", "limitations_for"]

@dataclass(frozen=True, slots=True)
class ComposedAnswer:
    """Architecture 4.2's `ComposedAnswer`, as much of it as Phase 9 fills.

    `chart_specs` is WP11.1's and is absent rather than empty — a field nothing
    writes is a field a reader has to check the code to understand.
    """

    text: str
    answered: bool
    confidence: str
    #: `query_executions.id` values, already verified to belong to this run.
    citations: tuple[str, ...] = ()
    #: One line on how the answer was reached, for a reader who will not read SQL.
    method: str = ""
    limitations: tuple[str, ...] = field(default_factory=tuple)


def method_note(state: ResearchState) -> str:
    """One sentence on how the answer was reached.

    Deterministic, and built from counts the controller already holds rather than
    from a model's account of its own reasoning — which would be a story about
    the work rather than a record of it.
    """
    queries = sum(1 for reference in state.executions if reference.ok)
    if not queries:
        return "Answered without running a query."
    steps = max(state.iteration, 1)
    plural = "query" if queries == 1 else "queries"
    step_words = "one step" if steps == 1 else f"{steps} steps"
    return f"{queries} {plural} over {step_words}, against {_source_phrase(state)}."


def _source_phrase(state: ResearchState) -> str:
    names = [name.split(".")[-1] for name in state.table_names]
    if not names:
        return "this data source"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])} and {names[-1]}"


def limitations_for(
    state: ResearchState,
    verdict: CriticVerdict | None,
    *,
    caveat: str = "",
) -> tuple[str, ...]:
    """What this answer does not establish, in the order a reader needs it.

    Assembled from what the run knows, never asked of the model. Order matters:
    a ceiling that stopped the search changes how much of the answer to trust and
    comes first; a reviewer's warning is next; the thin-evidence note is last,
    because it qualifies rather than undercuts.

    Deduplicated, and empty when there is nothing to say — a clean run should not
    be made to sound uncertain by a component that always finds something.
    """
    notes: list[str] = []

    if caveat:
        # The budget's own words. Already written for a person (`Exhaustion.reason`).
        notes.append(f"The investigation stopped before it was finished: {caveat}")

    if verdict is not None:
        # Warnings only. A blocking finding either sent the run round again or is
        # already reflected in an answer that says it could not be established;
        # repeating it here would read as a second, unexplained doubt.
        notes.extend(finding.detail for finding in verdict.warnings)
        if verdict.verdict == "insufficient_evidence":
            notes.append(
                "A review judged the evidence insufficient to answer fully; what "
                "is here is the part the queries do support."
            )

    thin = [
        reference
        for reference in state.executions
        if reference.ok and reference.row_count is not None and reference.row_count == 0
    ]
    if thin and len(thin) != len([r for r in state.executions if r.ok]):
        notes.append(
            f"{len(thin)} of the queries returned no rows, so any part of the "
            "answer resting on them is unsupported."
        )

    seen: set[str] = set()
    ordered: list[str] = []
    for note in notes:
        if note not in seen:
            seen.add(note)
            ordered.append(note)
    return tuple(ordered)


def assemble(
    draft: FinalizeIn,
    state: ResearchState,
    verdict: CriticVerdict | None,
    *,
    citations: tuple[str, ...],
    caveat: str = "",
) -> ComposedAnswer:
    """Everything the answer card renders, from what the run produced.

    ``citations`` arrives already verified against this run's executions — the
    runner does that, because dropping an invented id is a decision about trust
    and belongs where the trace can record it.
    """
    return ComposedAnswer(
        text=draft.answer,
        answered=draft.answered,
        confidence=draft.confidence if draft.confidence in {"high", "medium", "low"} else "medium",
        citations=citations,
        method=method_note(state),
        limitations=limitations_for(state, verdict, caveat=caveat),
    )
