"""The bounded research loop (architecture 4.4, diagram 5).

WP7.2b's single-shot middle — plan, run, at most one repair — becomes this: a
`for` loop with a hard ceiling, which is the difference between a product that
answers one question and one that investigates. The *ends* are unchanged and were
built for exactly this: `runner.py` still opens the run, builds the context,
composes the answer, verifies citations and ends the run exactly once.

**It is a `for` loop, not a `while`.** The iteration ceiling is the range, so the
loop terminates whatever the model says, whatever the tools return, and whatever
a future editor forgets. Every other budget is checked inside it, before spending
anything, so a run never overshoots a cap it was told to respect.

**Two model calls per iteration, and that is a decision** (DECISIONS **D-024**).
4.4 lists Plan, Observe and Reflect, with Observe on a cheap model. Three calls
across eight iterations plus a compose is 25, against 4.4's own ceiling of 20 —
its defaults do not fit its own loop if every stage is a model call. Observe is
therefore **deterministic** here: it is a mechanical transformation of a typed
tool result into a one-line summary, and doing it in code is cheaper, exactly
repeatable, and cannot hallucinate a number that was never in the result. What
4.4 actually asks of Observe — *raw rows never accumulate in the prompt* — is
kept, and kept more strictly than a model would.

**Progress is measured, not asserted.** Two consecutive iterations that add no
finding force the loop to finish (4.4's monotone-progress rule). Without it a
model that has run out of ideas will keep saying "let me check one more thing"
until the iteration ceiling, spending the whole budget to arrive where it was.

**The same query is never run twice.** A duplicate is refused before it is sent,
so a loop going in circles pays nothing to do it. `state.has_run` is the check
and the loop does not second-guess it.

**Every ending is an ending.** Budget exhaustion, no progress, an unanswerable
plan, or the model saying it is done — all of them leave the loop with what it
has, and the caller composes an answer that says so. 4.4's "guaranteed
finalize-with-caveats" is this: there is no path out of here that leaves a run
without an answer.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from dataagent.agent.budget import BudgetState
from dataagent.agent.capability import JoinGraph
from dataagent.agent.context import ContextBundle, Definition, history_block
from dataagent.agent.planner import Plan, plan_query
from dataagent.agent.state import ExecutionRef, ResearchState, StateFinding, Step
from dataagent.agent.tools.base import ToolContext, ToolResult
from dataagent.agent.tools.knowledge import PassageOut, SearchKnowledgeOut
from dataagent.agent.tools.registry import ToolRegistry
from dataagent.agent.tools.sql import RunSqlOut
from dataagent.config import Settings
from dataagent.dal.validator import tables_named
from dataagent.llm import service as llm
from dataagent.llm.base import Message
from dataagent.runs.events import EventWriter

__all__ = ["LoopOutcome", "Reflection", "research"]

#: Two consecutive barren iterations end the loop (4.4). Two rather than one
#: because a single fruitless step is normal — a query that returns nothing is
#: information — while two in a row is a pattern.
BARREN_LIMIT = 2

#: How many recent results the composer is shown in full. Bounded, because the
#: composing prompt must not grow with the length of the investigation — three
#: is enough for "compare this with that" and small enough to stay cheap.
COMPOSE_PREVIEWS = 3

#: A result small enough that its values *are* the finding — the aggregate case.
#: Anything larger is summarised by shape alone, so a prompt cannot grow with the
#: size of a customer's table.
INLINE_CELLS = 3

#: How many terms one run may look up in its documents (**B-075**, D-032). Two,
#: and it is a ceiling rather than a target: a question that turns on three
#: undefined terms is one the organization has not written down enough about, and
#: spending the whole iteration budget discovering that helps nobody. Every other
#: ceiling in this loop is a number the controller enforces, and so is this.
MAX_LOOKUPS = 2

#: How many passages a lookup puts in front of the next plan. Smaller than the
#: tool's own default because these are carried *forward* — they stay in the
#: prompt for the rest of the run, so each one is paid for on every subsequent
#: iteration.
LOOKUP_PASSAGES = 3


class ReflectFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=500)
    supported_by: list[str] = Field(default_factory=list[str], max_length=10)
    confidence: str = Field(default="medium")


class Reflection(BaseModel):
    """What the model concluded from the last result, and whether to go on.

    Closed and fully required, like every other structured call in this codebase,
    so a provider that can constrain decoding enforces the shape rather than
    suggesting it (B-033).

    ``rationale`` is written into the trace, so it is explicitly a **short public
    string** — 10.3's payloads are built for eyes and never carry raw model
    reasoning.
    """

    model_config = ConfigDict(extra="forbid")

    findings: list[ReflectFinding] = Field(default_factory=list[ReflectFinding], max_length=10)
    open_questions: list[str] = Field(default_factory=list[str], max_length=10)
    next_purpose: str = Field(default="", max_length=300)
    done: bool = Field(description="True when the question is answered or cannot be taken further.")
    rationale: str = Field(default="", max_length=300)


@dataclass(frozen=True, slots=True)
class LoopOutcome:
    """Why the loop stopped, for the caller that has to compose an answer."""

    #: answered | refused | exhausted | no_progress
    ending: str
    #: Set when a ceiling stopped it, so the answer can say which.
    caveat: str = ""
    #: The planner's own words when it judged the question unanswerable.
    refusal: str = ""
    #: Successful executions, newest last — what a citation may name.
    execution_ids: tuple[str, ...] = ()
    #: `(execution_id, rendered result)` for the most recent successful queries,
    #: for the composer alone. **Held in memory and never checkpointed**: 4.4
    #: forbids rows *accumulating*, and this does not accumulate — it is a
    #: bounded snapshot handed to one final call, replaced each time. Without it
    #: the composer sees only one-line summaries and cannot answer any question
    #: whose result has more than one row, which is most real questions.
    previews: tuple[tuple[str, str], ...] = ()


@dataclass
class _Progress:
    """Whether the last iteration was worth its budget."""

    findings_added: int = 0
    questions_opened: int = 0
    notes: list[str] = field(default_factory=list[str])

    @property
    def moved(self) -> bool:
        return self.findings_added > 0 or self.questions_opened > 0


_WHITESPACE = re.compile(r"\s+")


def proposed_hash(sql: str) -> str:
    """A hash of what the model *proposed*, normalised for whitespace and case.

    **Deliberately not the canonical hash of the validated statement**, which is
    what WP8.1a's docstring first promised. The canonical form only exists after
    the DAL has validated and rewritten the statement — by which time the query
    has been spent, and a duplicate rule that answers after the cost is not a
    rule. So the check happens on the model's own text, normalised, which catches
    the failure this exists for: a loop out of ideas re-proposing the query it
    already ran.

    What it does not catch is the same question written two different ways
    (**B-049**). That is a weaker guarantee than the canonical hash would give
    and it is stated rather than glossed.
    """
    collapsed = _WHITESPACE.sub(" ", sql.strip().lower())
    return hashlib.sha256(collapsed.encode()).hexdigest()[:16]


def summarize(result: RunSqlOut, purpose: str) -> str:
    """One line describing what came back — architecture 4.4's Observe, in code.

    Values appear only for a result small enough that the values *are* the answer
    (a count, a total, a single row of aggregates). Anything larger is described
    by its shape, so the prompt cannot grow with the size of a customer's table
    and no accidental bulk of personal data reaches a model. Everything here is
    already masked by the DAL regardless.
    """
    shape = f"{result.row_count} row{'' if result.row_count == 1 else 's'}"
    if result.masked_columns:
        shape += f", {len(result.masked_columns)} column(s) masked by policy"
    if result.row_count == 0:
        return f"{purpose}: no rows matched."
    first = result.rows[0] if result.rows else []
    if result.row_count == 1 and len(first) <= INLINE_CELLS:
        pairs = ", ".join(
            f"{name}={value!r}" for name, value in zip(result.columns, first, strict=False)
        )
        return f"{purpose}: {pairs}"
    return f"{purpose}: {shape} over columns {', '.join(result.columns)}."


async def research(
    *,
    context: ToolContext,
    tools: ToolRegistry,
    events: EventWriter,
    state: ResearchState,
    budget: BudgetState,
    bundle: ContextBundle,
    graph: JoinGraph,
    dialect: str,
    checkpoint: Callable[[], Awaitable[None]],
    record_finding: Callable[[StateFinding], Awaitable[None]],
    settings: Settings | None = None,
) -> LoopOutcome:
    """Investigate until there is an answer or a ceiling says stop.

    ``checkpoint`` and ``record_finding`` are awaitables the caller supplies —
    one invoked at every transition, one when a finding is reached. Passed in
    rather than imported so this module owns no persistence and stays testable
    without a database, the same reason `runner.execute_run` takes ids rather
    than sessions.

    A finding is written **when it is reached**, not at the end. Two reasons: an
    interrupted run keeps what it had concluded, and the trace shows the
    investigation arriving at things in order, which is what WP8.3's timeline
    renders. It also means the `finding_added` event is emitted exactly once, by
    the code that persists it — emitting here as well would put every finding in
    the trace twice.
    """
    save = checkpoint
    previews: list[tuple[str, str]] = []

    for _ in range(budget.budget.iterations):
        stop = budget.exhausted()
        if stop is not None:
            state.stopped_by = stop.dimension
            state.phase = "finished"
            await save()
            await events.emit(
                "budget_exhausted", {"dimension": stop.dimension, "reason": stop.reason}
            )
            return LoopOutcome(
                ending="exhausted",
                caveat=stop.reason,
                execution_ids=state.execution_ids(),
                previews=tuple(previews),
            )

        warning = budget.approaching()
        if warning is not None:
            await events.emit("budget_warning", {"dimension": warning})

        budget.spend_iteration()
        state.iteration += 1
        state.phase = "planning"
        await save()
        await events.emit("step_started", {"iteration": state.iteration})

        plan, tokens = await _next_step(context, bundle, tools, state, settings)
        budget.spend_llm(tokens)
        await events.emit(
            "plan_created",
            {
                "purpose": plan.purpose,
                "answerable": plan.answerable,
                "iteration": state.iteration,
                "tokens": tokens,
            },
        )

        # **The agent asked what a word means here** (B-075, D-032). Checked
        # **before `answerable`**, and that order was bought with a live run: a
        # model that needs a definition says so by refusing — *"the reference
        # data does not define 'anchor order'; the organization's definition is
        # needed"* — with `answerable` false and the term in `define`. Refusing
        # first turned the one state this feature exists for into a dead run.
        # An unanswerable plan that names something to look up is not a refusal;
        # it is a request, and the refusal below is what it becomes if the
        # documents have nothing to say.
        #
        # It is also checked before the statement, because `sql` on a lookup step
        # is whatever the model wrote while it still did not know — running it
        # would spend a query on the guess this branch exists to avoid.
        if wanted := _lookup_wanted(plan, state):
            bundle = await _look_up(
                context=context,
                tools=tools,
                events=events,
                state=state,
                bundle=bundle,
                term=wanted,
            )
            await save()
            # Costs this iteration and no model call beyond the plan that asked:
            # nothing ran, so there is nothing to reflect on. That is what keeps
            # D-024's and D-028's arithmetic true — an iteration spent looking
            # something up is *cheaper* than an ordinary one.
            continue

        if not plan.answerable:
            state.phase = "finished"
            await save()
            return LoopOutcome(
                ending="refused",
                refusal=plan.reason or "The available data cannot answer that.",
                execution_ids=state.execution_ids(),
                previews=tuple(previews),
            )

        # Can these tables be joined at all? Checked **before the statement is
        # sent**, because a check that answered afterwards would have spent the
        # query it exists to prevent — and because a join between unrelated
        # tables does not error, it returns a cartesian product, and an answer
        # computed from one looks exactly like a real answer (4.3).
        verdict = graph.check(tables_named(plan.sql, dialect=dialect))
        if not verdict.answerable:
            state.capability = verdict.as_payload()
            state.phase = "finished"
            await save()
            await events.emit("capability_checked", verdict.as_payload())
            return LoopOutcome(
                ending="refused",
                refusal=verdict.reason(),
                execution_ids=state.execution_ids(),
                previews=tuple(previews),
            )
        if verdict.chasms:
            # A chasm is recorded and **not blocked**, deliberately (D-026).
            # `tables_named` gives the set of tables the statement mentions and
            # not how it joins them, so a statement that already aggregates each
            # side in its own CTE — the correct query for this pair — is
            # indistinguishable here from one that joins the detail rows. To
            # block on the table set alone would refuse the right answer along
            # with the wrong one, which is B-058 arriving from the other
            # direction. The steering that *is* safe happens before the model
            # writes anything, in `runner._investigate`, where 4.3 puts it.
            # Blocking waits on reading the join predicates themselves, which
            # belongs in `dal/validator.py` and therefore in its own reviewed PR.
            state.capability = verdict.as_payload()
            await events.emit("capability_checked", verdict.as_payload())

        digest = proposed_hash(plan.sql)
        if state.has_run(digest):
            # Refused before it is sent: a loop going in circles pays nothing to
            # do it. Counted as a barren iteration, because proposing what has
            # already been run is precisely the absence of progress.
            await events.emit(
                "reflection", {"continue": False, "public_rationale": "That query has already run."}
            )
            state.barren_iterations += 1
            if state.barren_iterations >= BARREN_LIMIT:
                return _no_progress(state, tuple(previews))
            continue

        state.phase = "executing"
        state.plan.append(Step(purpose=plan.purpose, sql=plan.sql, status="pending"))
        await save()

        result = await tools.call(
            context, "run_sql", {"sql": plan.sql, "purpose": plan.purpose}, events=events
        )
        budget.spend_query()

        progress = _Progress()
        if result.ok and isinstance(result.data, RunSqlOut):
            state.plan[-1].status = "done"
            summary = summarize(result.data, plan.purpose)
            state.record_execution(
                ExecutionRef(
                    execution_id=result.data.execution_id,
                    purpose=plan.purpose,
                    sql_hash=digest,
                    row_count=result.data.row_count,
                    summary=summary,
                    ok=True,
                )
            )
            await events.emit(
                "query_executed",
                {
                    "execution_id": result.data.execution_id,
                    "row_count": result.data.row_count,
                    "duration_ms": result.data.duration_ms,
                    "masked_columns": result.data.masked_columns,
                },
            )
            await events.emit("result_summarized", {"one_liner": summary})
            previews.append((result.data.execution_id, result.render()))
            del previews[:-COMPOSE_PREVIEWS]
        else:
            # A refusal is recorded too, and this is what replaces WP7.2b's
            # single explicit repair: the next iteration's planner is told what
            # was refused and why, so correcting a hallucinated column is just
            # the next step rather than a special case. Recording the hash also
            # stops the loop re-proposing the identical statement — without this
            # it would burn every iteration on the same refused query.
            state.plan[-1].status = "failed"
            state.record_execution(
                ExecutionRef(
                    execution_id="",
                    purpose=plan.purpose,
                    sql_hash=digest,
                    summary=f"refused ({result.code or 'unknown'}): {result.error}",
                    ok=False,
                )
            )
            await events.emit(
                "sql_rejected", {"rule": result.code or "unknown", "repairable": result.repairable}
            )
            if not result.repairable:
                # WP7.2b's rule, kept: rewriting cannot fix a database that is
                # down, so another iteration would spend the whole budget
                # learning what this one already knows. `repairable` is set by
                # the tool layer and the loop does not second-guess it.
                state.phase = "finished"
                await save()
                return LoopOutcome(
                    ending="refused",
                    refusal=(
                        "I could not answer that from this data. The query could not "
                        f"be run: {result.error}"
                    ),
                    execution_ids=state.execution_ids(),
                    previews=tuple(previews),
                )

        state.phase = "observing"
        await save()

        reflection, tokens = await _reflect(context, bundle, state, plan, result, settings)
        budget.spend_llm(tokens)
        state.phase = "reflecting"

        for candidate in reflection.findings:
            proposed = StateFinding(
                statement=candidate.statement,
                support=candidate.supported_by,
                confidence=candidate.confidence,
            )
            if state.add_finding(proposed):
                progress.findings_added += 1
                # The stored copy, whose citations have been filtered down to
                # executions this run really produced — never the model's claim.
                await record_finding(state.findings[-1])
        for question in reflection.open_questions:
            if question not in state.open_questions:
                state.open_questions.append(question)
                progress.questions_opened += 1

        state.barren_iterations = 0 if progress.moved else state.barren_iterations + 1
        await events.emit(
            "reflection",
            {"continue": not reflection.done, "public_rationale": reflection.rationale[:300]},
        )
        await save()

        if reflection.done:
            state.phase = "finished"
            await save()
            return LoopOutcome(
                ending="answered",
                execution_ids=state.execution_ids(),
                previews=tuple(previews),
            )

        if state.barren_iterations >= BARREN_LIMIT:
            return _no_progress(state, tuple(previews))

    # The `for` ran out: the iteration ceiling is the range, so this is the one
    # exhaustion that needs no check.
    state.stopped_by = "iterations"
    state.phase = "finished"
    await save()
    await events.emit(
        "budget_exhausted", {"dimension": "iterations", "reason": "iteration ceiling"}
    )
    return LoopOutcome(
        ending="exhausted",
        caveat="I reached the maximum number of research steps for one question.",
        execution_ids=state.execution_ids(),
        previews=tuple(previews),
    )


def _no_progress(state: ResearchState, previews: tuple[tuple[str, str], ...]) -> LoopOutcome:
    """Two barren iterations in a row: finish with what there is (4.4)."""
    state.stopped_by = "no_progress"
    state.phase = "finished"
    return LoopOutcome(
        ending="no_progress",
        caveat="I stopped because the last few steps were not adding anything new.",
        execution_ids=state.execution_ids(),
        previews=previews,
    )


def _lookup_wanted(plan: Plan, state: ResearchState) -> str:
    """The term to look up, or empty when this step is not a lookup.

    Three ways to get nothing back, and each is a ceiling rather than a
    judgement about the term:

    * the model did not ask;
    * it has asked before in this run — the duplicate-query rule's shape applied
      to a second kind of repetition, because a corpus does not change mid-run
      and asking it twice buys an iteration's worth of nothing;
    * the run has spent its lookups (`MAX_LOOKUPS`).

    In the last two cases the plan's SQL runs as written. That is deliberate: the
    model wrote a statement, and refusing to run it because it also asked a
    question would turn a ceiling into a dead end. It is told what it already
    knows through the ordinary progress notes.
    """
    term = plan.define.strip()
    if not term:
        return ""
    if term.lower() in state.lookups:
        return ""
    if len(state.lookups) >= MAX_LOOKUPS:
        return ""
    return term


async def _look_up(
    *,
    context: ToolContext,
    tools: ToolRegistry,
    events: EventWriter,
    state: ResearchState,
    bundle: ContextBundle,
    term: str,
) -> ContextBundle:
    """Dispatch `search_knowledge` and put what it found in front of the next plan.

    **Through the registry**, like every other tool call: it validates the
    arguments, filters by role, and emits `tool_called` — so a lookup appears in
    the trace as the act it was, which is what makes *"the agent consulted a
    document"* something a person can verify rather than take on trust.

    Recorded as spent **whatever comes back**, including nothing. A corpus that
    has no answer for a term will still have none next iteration, and a run that
    could re-ask on failure would spend its whole budget discovering that.
    """
    state.lookups.append(term.lower())
    result = await tools.call(
        context, "search_knowledge", {"query": term, "limit": LOOKUP_PASSAGES}, events=events
    )

    passages: list[PassageOut] = []
    note = ""
    if result.ok and isinstance(result.data, SearchKnowledgeOut):
        passages, note = result.data.passages, result.data.note

    for passage in passages:
        bundle = bundle.with_definition(
            Definition(term=term, text=passage.text, source=passage.source)
        )

    await events.emit(
        "knowledge_consulted",
        {
            # The three facts a reader of the timeline needs: what was asked,
            # whether the organization had written anything down, and which
            # documents answered. `found_by` says whether meaning or wording
            # reached them, which is the retrieval regression B-018 made visible.
            "term": term,
            "passages": len(passages),
            "sources": [passage.source for passage in passages][:LOOKUP_PASSAGES],
            "found_by": sorted({passage.found_by for passage in passages}),
            # Empty on the happy path. When the corpus said nothing, or said it
            # through half a search, this is the sentence that explains an answer
            # that went on without a definition.
            "note": note,
        },
    )
    return bundle


async def _next_step(
    context: ToolContext,
    bundle: ContextBundle,
    tools: ToolRegistry,
    state: ResearchState,
    settings: Settings | None,
) -> tuple[Plan, int]:
    """The next query to run, from the same planner the single-shot path uses.

    Reused rather than re-implemented: it already renders the layered prompt,
    holds the closed schema and asks for aliased projections (B-020). What the
    loop adds is *where it has got to* — the findings so far and what is still
    open — so the second iteration does not re-plan the first.
    """
    return await plan_query(
        org_id=context.org_id,
        bundle=bundle,
        registry=tools,
        role=context.role,
        run_id=context.run_id,
        actor_user_id=context.actor_user_id,
        settings=settings,
        repair_of=_progress_so_far(state) or None,
    )


def _progress_so_far(state: ResearchState) -> str:
    """What the planner is told about earlier iterations.

    Summaries and open questions — never rows. A model that can see the whole of
    every previous result would write its next query against a transcript instead
    of against the catalog, and the prompt would grow with the data.
    """
    if not state.executions and not state.findings and not state.lookups:
        return ""
    lines = ["What you have already established in this investigation:"]
    for reference in state.executions:
        lines.append(f"- ran `{reference.purpose}` -> {reference.summary}")
    for finding in state.findings:
        lines.append(f"- concluded: {finding.statement}")
    if state.lookups:
        # **Told, because a lookup it cannot see it already made is a lookup it
        # will ask for again.** Found live: given the definition of a term at
        # iteration 2, the model asked for the same term at iteration 3, had the
        # duplicate refused in silence, and hedged an answer it had already
        # computed correctly. The refusal was right; saying nothing about it was
        # not. Nothing here repeats the definition — that is above, at L4, where
        # it belongs.
        lines.append(
            "- already looked up, and the answer is in the documents section of the "
            f"reference material above: {', '.join(sorted(state.lookups))}. Do not ask "
            "for these again; use what it says."
        )
    if state.open_questions:
        lines.append("Still open: " + "; ".join(state.open_questions))
    lines.append(
        "Write the next query that moves this forward. Do not repeat a query you "
        "have already run. If nothing further is needed, set answerable to false "
        "with reason 'complete'."
    )
    return "\n".join(lines)


async def _reflect(
    context: ToolContext,
    bundle: ContextBundle,
    state: ResearchState,
    plan: Plan,
    result: ToolResult,
    settings: Settings | None,
) -> tuple[Reflection, int]:
    """What did that tell us, and is there more worth doing?

    A failure is reflected on too, rather than short-circuited: "that query was
    refused" is information the next step should have, and letting the model see
    it is what turns a refusal into a different approach instead of the same one
    again.
    """
    # The thread first and framed (**D-029**), for the reason the planner needs
    # it too: "is the question now answered" cannot be judged when the question
    # is *"check again"* and nothing says what was being checked.
    thread = history_block(bundle.history)
    prompt = (
        (f"{thread}\n\n" if thread else "") + f"The question is: {state.question}\n\n"
        f"You have completed {state.iteration} research step(s).\n"
        f"{_progress_so_far(state) or 'Nothing established yet.'}\n\n"
        f"The step you just ran was for: {plan.purpose}\n"
        f"{result.render()}\n\n"
        "Record any finding this establishes, citing the execution id it came "
        "from. Say whether the question is now answered. If it is not, say what "
        "is still open. Keep the rationale to one short public sentence — it is "
        "shown to the person who asked."
    )
    completion = await llm.complete(
        role="plan",
        org_id=context.org_id,
        messages=[Message(role="user", content=prompt)],
        schema=Reflection,
        run_id=context.run_id,
        actor_user_id=context.actor_user_id,
        settings=settings,
    )
    return completion.parsed_as(Reflection), completion.usage.total_tokens
