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


def _prose_definitions(state: ResearchState) -> tuple[str, ...]:
    """Terms this run took from a document and nothing enforced (**D-033**).

    A term qualifies when the run looked it up, **the documents answered**, and
    **no semantic definition covers it**. All three matter.

    `state.prose_terms` is the second: a lookup the corpus could not explain left
    the model no worse informed than before, and caveating it would be a warning
    about nothing — which is how a reader learns to skip warnings.

    `state.applied_definitions` is the third, and it is D-033's seam made
    visible. A term with a definition **is** enforced — the critic checks the
    statement against its filters — so saying otherwise would be a false warning
    about the one case the layer actually handles. This is what "the limitation
    goes away when an Admin blesses the passage into a definition" means in code.
    """
    enforced = {name.lower().replace("_", " ") for name in state.applied_definitions}
    enforced |= {name.lower() for name in state.applied_definitions}
    return tuple(sorted({term for term in state.prose_terms if term.lower() not in enforced}))


def _and_list(names: tuple[str, ...]) -> str:
    """`a`, `a and b`, `a, b and c`. Qualified names, because two schemas can
    hold a table of the same name and this sentence is about which one."""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


#: How many alternatives one note will name. A question that retrieved six fact
#: tables did not have six answers; it had a retrieval that was broad, and a
#: sentence listing all of them is one nobody finishes reading.
MAX_NAMED_ALTERNATIVES = 2


def _unused_sources(state: ResearchState) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The sources this answer read, and the ones it could have read and did not.

    **B-093, and it exists because of B-060.** Asked which raw ingredients cost
    the most, the agent was handed a purchase ledger *and* a stock-movement
    table, used one, and said nothing — and the two disagree by more than a
    factor of a hundred depending on which filter you believe. The failure was
    never the SQL, which ran and was cited correctly. It was that a genuinely
    ambiguous choice was made silently, so the trace showed *what* was chosen and
    never that a choice existed.

    Both halves come from the run's own record: `candidate_sources` is what
    context offered that had figures to aggregate, and each execution carries the
    tables the **validator** resolved, so this compares what was read against
    what was available rather than against what the model said it read.

    Empty when the question had one source, when nothing was read, and when
    everything offered was used — which is most runs. A note on every answer is
    a note nobody reads.
    """
    candidates = tuple(dict.fromkeys(state.candidate_sources))
    if len(candidates) < 2:
        return (), ()
    read = {table for reference in state.executions if reference.ok for table in reference.tables}
    used = tuple(name for name in candidates if name in read)
    unused = tuple(name for name in candidates if name not in read)
    if not used or not unused:
        return (), ()
    return used, unused


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

    # **First, and ahead of the budget caveat** (D-034). This module used to take
    # warnings only, on the reasoning that a block either sent the run round
    # again or was already reflected in an answer saying it could not be
    # established. A live run disproved both halves: the critic blocked twice,
    # the run spent its one permitted re-entry (M9), and the draft shipped
    # claiming to have done exactly what the critic said it had not — so the one
    # finding strong enough to stop a run was the only one the reader never saw
    # (**B-079**).
    #
    # A block that survives to here is by definition unresolved: re-entry has
    # either happened or was not available, and the answer is going out anyway.
    # It is louder than the budget caveat because the two are different doubts —
    # a ceiling says the answer is *incomplete*, a block says it may be *wrong* —
    # and it is quoted in the critic's own words rather than paraphrased, because
    # the critic wrote the sentence for a reader.
    if verdict is not None and verdict.blocked:
        notes.extend(
            f"A review of this answer did not pass, and it is shown anyway: {finding.detail}"
            for finding in verdict.blocking
        )

    if caveat:
        # The budget's own words. Already written for a person (`Exhaustion.reason`).
        notes.append(f"The investigation stopped before it was finished: {caveat}")

    if verdict is not None:
        notes.extend(finding.detail for finding in verdict.warnings)
        if verdict.verdict == "insufficient_evidence":
            notes.append(
                "A review judged the evidence insufficient to answer fully; what "
                "is here is the part the queries do support."
            )

    if unverified := _prose_definitions(state):
        # **Prose informs the model; a structured definition binds it** (D-033).
        # This run was shown what a term means and nothing checked that the SQL
        # kept to it — which is not a hypothetical: given the definition of an
        # *anchor order*, a live model wrote it into the statement and then
        # reasoned its way back out of it two iterations later, answering 1,054
        # where the document said 747 (**B-078**). The critic could not object,
        # because a passage carries no filters to compare an AST against.
        #
        # So the answer says so. It names the terms rather than gesturing at
        # "a document", because a reader who knows *which* definition was
        # unenforced can go and check that one. The note disappears when an Admin
        # blesses the passage into a definition — at which point the claim stops
        # being unverifiable, which is the whole distinction.
        terms = ", ".join(f"“{term}”" for term in unverified)
        notes.append(
            f"This answer relies on how your documents define {terms}. That "
            "definition was read as prose, so nothing checked that the query "
            "actually followed it — unlike a defined metric, which is enforced."
        )

    used, unused = _unused_sources(state)
    if used:
        # **Stated, not judged.** The claim is only that a choice existed and
        # what it was between — not that the other source would disagree, which
        # this run has no way to know without running it. That is the honest
        # sentence, and it is the one missing from every reproduction of B-060.
        named = list(unused[:MAX_NAMED_ALTERNATIVES])
        if len(unused) > MAX_NAMED_ALTERNATIVES:
            # Joined as a list item rather than appended as a clause: "a and b,
            # and others" reads as an afterthought, "a, b and others" as a list.
            named.append("others")
        notes.append(
            f"This answer reads {_and_list(used)}. The question also matched "
            f"{_and_list(tuple(named))} — tables with figures it could have been "
            "answered from, and not read here. A different source can give a "
            "different number."
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
        confidence=_confidence(draft, verdict),
        citations=citations,
        method=method_note(state),
        limitations=limitations_for(state, verdict, caveat=caveat),
    )


def _confidence(draft: FinalizeIn, verdict: CriticVerdict | None) -> str:
    """The model's own confidence, capped by what the review found (**D-034**).

    A draft the platform has judged and disputed cannot be `high`, whatever the
    model thinks of it — the model is the party whose work is in question. Capped
    rather than forced to `low`, because a block is a reason to doubt the answer
    and not a reason to assert it is wrong; `low` would be its own overstatement,
    in the other direction.
    """
    stated = draft.confidence if draft.confidence in {"high", "medium", "low"} else "medium"
    if verdict is not None and verdict.blocked and stated == "high":
        return "medium"
    return stated
