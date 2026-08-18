"""One question, investigated and answered (architecture M8, Part 4.4).

**The ends of a run.** Context in, a bounded loop in the middle, a cited answer
out — and the run moved to exactly one ending whatever happened. WP7.2b built
this shape around a single plan-run-repair step and said the ends would not
change when the loop arrived; WP8.1b is that claim being cashed. What changed
here is one call: `_investigate` now hands off to `loop.research`.

Four rules hold this together, and each exists because of a specific way an
agent goes wrong.

**A run is bounded by the controller, not by the prompt.** The loop's ceilings
live in `agent/budget.py` and are checked before anything is spent (4.4). This
module chooses the run's ending status from what the budget says: a run stopped
by a ceiling is `budget_exhausted` — an answer with caveats — while one that
simply had nothing more worth doing is `completed`.

**A refusal is an ending, not a failure.** A run that could not answer completes
with `answered=false` and a reason. `failed` is reserved for the platform
breaking. Getting this wrong would either hide real breakage among honest
refusals or dress a refusal up as an outage. The same applies to exhaustion,
which is why it has a status of its own rather than borrowing either.

**Citations are verified before they are stored.** The model may only cite
execution ids this run actually produced. Anything else is dropped and the trace
says so — architecture 4.2 makes findings-cite-real-rows the spine of trust, and
a citation nobody can resolve looks like evidence while being none.

**Every exit ends the run exactly once.** The transition is in a `finally`, so a
crash anywhere above still moves the run out of `running` and writes its
`run_finished`. A dangling run is the one outcome with no user-visible symptom
until somebody wonders why a page has been spinning for an hour.

**Every prompt that carries the question carries the thread with it** (**D-029**,
B-064). Four do — the layered prompt the planner renders, the loop's reflection,
the critic's rubric and the composer — and all four render it through
`context.history_block`, so a fifth added later has one obvious thing to call. A
prompt that got the question without the thread would be judging *"check again"*
on its own, and for the critic that means blocking a correct answer.

The runner takes a run id and never touches a request, a response or a session
from the web layer. That is what makes architecture 0.2.4's promotion path free:
the same code moves behind a worker in V1.5 without a rewrite.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime

from dataagent.agent import composer, critic
from dataagent.agent.budget import Budget, BudgetState
from dataagent.agent.capability import CapabilityChasm, CapabilityGap, JoinGraph, load_join_graph
from dataagent.agent.context import (
    HISTORY_TURNS,
    ContextBundle,
    HistoryTurn,
    build_context,
    history_block,
)
from dataagent.agent.critic import CriticVerdict
from dataagent.agent.loop import LoopOutcome, research
from dataagent.agent.state import ResearchState, StateFinding
from dataagent.agent.tools.base import ToolContext
from dataagent.agent.tools.finalize import FINALIZE, FinalizeIn
from dataagent.agent.tools.registry import ToolRegistry, default_registry
from dataagent.config import Settings
from dataagent.dal.policy import SourcePolicy
from dataagent.knowledge import embeddings
from dataagent.llm.base import LLMError
from dataagent.runs import service as runs
from dataagent.runs.events import EventWriter
from dataagent.semantic import definitions as semantic
from dataagent.semantic import verified as verified_queries
from dataagent.tenancy.session import org_session

__all__ = ["RunOutcome", "execute_run", "relevant_pairs"]

#: Stopping for one of these is `budget_exhausted`; stopping for `no_progress` is
#: an ordinary completion, because nothing was overspent — the run simply had
#: nothing further worth doing.
_BUDGET_DIMENSIONS = frozenset({"iterations", "queries", "llm_calls", "tokens", "wall_seconds"})

#: How many times a draft may be criticised. Two: the first answer, and the one
#: re-entry architecture M9 allows. A third would make the critic a loop with no
#: ceiling of its own, which is the thing 4.4's budgets exist to refuse.
MAX_CRITIC_PASSES = 2

#: The critic's verdict is three short reasons at most, so its output ceiling can
#: be small — and is set explicitly rather than left to the default, because
#: **B-052** is a ceiling silently below what a schema needs.
CRITIC_OUTPUT_TOKENS = 800


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """What happened, for a caller that wants to know without re-reading the run."""

    run_id: uuid.UUID
    status: str
    answered: bool
    answer: str
    execution_ids: tuple[str, ...] = ()
    llm_calls: int = 0
    #: How many research steps it took. 0 for a run that never got past context.
    iterations: int = 0
    #: The ceiling or rule that stopped the search, when one did — so a caller
    #: can tell a complete answer from a partial one without re-reading the run.
    stopped_by: str | None = None
    #: What the answer does not establish (WP9.2). Empty is the common case and
    #: a good one: a clean run should not be made to sound uncertain.
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Working:
    """Everything one run carries while it is running.

    Two objects rather than one, and stored in two columns, per **D-023**: the
    research state is the agent's scratchpad, the budget is the ceiling the agent
    is held to, and a limit that travels inside the thing it limits is one bad
    deserialization away from being editable.
    """

    state: ResearchState
    budget: BudgetState


async def execute_run(
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    data_source_id: uuid.UUID,
    actor_user_id: uuid.UUID | None = None,
    role: str = "reader",
    registry: ToolRegistry | None = None,
    settings: Settings | None = None,
    budget: Budget | None = None,
    as_of: date | None = None,
) -> RunOutcome:
    """Drive one queued run to an ending. Never raises for the question's sake.

    Takes ids rather than objects so it can be called from anywhere — a request,
    a background task, a script, a test — which is the constraint that keeps the
    V1.5 move behind a worker free.

    ``as_of`` is what this run calls today (**D-027**). None means the wall clock,
    which is what a person asking in a browser means; the eval harness passes a
    fixed date, and that is the whole mechanism by which *"revenue last month"*
    has the same answer next year as it does now.
    """
    tools = registry if registry is not None else _registry_with_finalize()
    events = EventWriter(org_id=org_id, run_id=run_id)
    working = _Working(
        state=ResearchState(run_id=run_id, org_id=org_id),
        budget=BudgetState(budget=budget if budget is not None else Budget()),
    )
    context = ToolContext(
        org_id=org_id,
        run_id=run_id,
        role=role,
        actor_user_id=actor_user_id,
        data_source_id=data_source_id,
        as_of=as_of,
        # Resolved once for the run rather than per tool call, and from the
        # settings this run was given rather than from the process — the same
        # reason `execute_run` takes settings at all. None is an ordinary answer
        # (no embedding model configured) and costs the run its vector arm, not
        # its search (**B-073**). Called through the module rather than imported
        # by name, which is what lets the test guard wrap the one door an
        # embedder comes out of — the same reason `llm/service.py` says
        # `registry.get_provider` rather than importing the function.
        embedder=embeddings.get_embedder(settings),
        settings=settings,
    )

    await runs.transition(org_id=org_id, run_id=run_id, status="running")
    ending, outcome = "failed", None
    try:
        outcome = await _investigate(
            context=context, tools=tools, events=events, working=working, settings=settings
        )
        # A run stopped by a ceiling is an answer with caveats, not a failure —
        # `agent_runs` has carried this status since revision 0012 for exactly
        # this, and conflating it with `completed` would hide the cost signal
        # while conflating it with `failed` would invent an outage.
        ending = (
            "budget_exhausted" if working.state.stopped_by in _BUDGET_DIMENSIONS else "completed"
        )
        return outcome
    except LLMError as error:
        # The provider, not the question. Sanitized already, and distinct from a
        # refusal: the user should be told the platform failed, not that their
        # data could not answer them.
        await _record_failure(events, working, str(error), category="llm_error")
        return _failed(run_id, working)
    except Exception as error:
        await _record_failure(events, working, str(error), category="internal_error")
        return _failed(run_id, working)
    finally:
        # In `finally`, because a run that never ends is the one failure with no
        # symptom until somebody notices a page spinning.
        await _finish(org_id=org_id, run_id=run_id, status=ending, working=working, outcome=outcome)


#: How many capability facts the planner is told up front. The limit is real —
#: a star schema yields hundreds of pairs and the prompt has a budget — but which
#: ones it keeps is the whole point (B-056).
CAPABILITY_NOTE_LIMIT = 20


def relevant_pairs[PairT: CapabilityGap | CapabilityChasm](
    pairs: Sequence[PairT], selected: Sequence[str]
) -> tuple[PairT, ...]:
    """The pairs this question is about, not the ones that sort first (B-056).

    4.3 hands the planner its capability facts up front *"so a well-behaved model
    avoids the dead end rather than being caught in it"*. That only works if the
    facts concern the tables in hand. This used to be `pairs[:20]` over a list
    sorted by table name, which on the F&B source meant twenty facts about
    `bridge_item_ingredient` and **none** of the fourteen about `fact_sale` —
    the warning was noise, and the check caught the model afterwards instead of
    steering it.

    Both endpoints selected beats one, because a pair the question spans is worth
    more than a pair it merely touches. Ordering is otherwise left alone, so the
    result stays deterministic for the same catalog and question.
    """
    wanted = {name.strip().strip('"').split(".")[-1].lower() for name in selected}
    if not wanted:
        return tuple(pairs[:CAPABILITY_NOTE_LIMIT])

    def rank(pair: PairT) -> int:
        return -((pair.left in wanted) + (pair.right in wanted))

    touching = [pair for pair in pairs if pair.left in wanted or pair.right in wanted]
    return tuple(sorted(touching, key=rank)[:CAPABILITY_NOTE_LIMIT])


def source_of(context: ToolContext) -> uuid.UUID:
    """The run's data source. `ToolContext` allows none because a tool may run
    without one; `execute_run` requires one, so this narrows rather than
    defends."""
    if context.data_source_id is None:  # pragma: no cover - execute_run requires it
        raise ValueError("a run cannot be executed without a data source")
    return context.data_source_id


async def _investigate(
    *,
    context: ToolContext,
    tools: ToolRegistry,
    events: EventWriter,
    working: _Working,
    settings: Settings | None,
) -> RunOutcome:
    """Context, then the bounded loop, then an answer — whatever the loop found.

    The middle used to be plan-run-repair. It is now `loop.research`, and the
    shape of the ends is why WP7.2b built them the way it did: this function
    still opens with context and closes with a composed, citation-verified
    answer, and everything between is the loop's business.
    """
    state, budget = working.state, working.budget
    state.question = await _question_of(context.org_id, context.run_id)
    history = await _history_of(context.org_id, context.run_id)

    bundle = await build_context(
        org_id=context.org_id,
        question=state.question,
        data_source_id=context.data_source_id,
        as_of=context.as_of,
        history=history,
        # The run's own embedder, so card search is hybrid and its query
        # embedding is charged to this run and bounded by its ceiling (B-018,
        # B-073). None here costs the search its vector arm and nothing else.
        embedder=context.embedder,
        run_id=context.run_id,
        actor_user_id=context.actor_user_id,
        settings=settings,
    )
    # **What this organization means by the words in the question** (D-033,
    # WP10.2c). Matched by name and synonym, narrowly: a definition applied to a
    # question it is not about becomes a critic rule enforcing a filter the
    # answer never needed, which is a false block.
    available = await semantic.definitions_for(context.org_id, source_of(context))
    applied = semantic.matching(available, state.question)
    # **Both numbers, always** (B-087). A run that matched nothing looks exactly
    # like a run with nothing to match, and for three gate walks in a row that
    # made a naming problem read as a broken feature. Recording what was on
    # offer is what lets the answer say which of the two happened.
    state.definitions_available = len(available)
    if applied:
        bundle = replace(bundle, definitions_applied=applied)
        state.applied_definitions = [definition.name for definition in applied]

    # **How this organization has answered questions like this one** (arch 5.4).
    # Matched lexically and for free: no embedding, so no spend and no dependency
    # on a provider being reachable, and a miss costs the run an example it never
    # had while a wrong example is actively misleading.
    #
    # Loaded whether or not a definition matched — the two are independent. A
    # question can name no defined metric and still be one this organization has
    # a worked answer for, which is the common case for "which table do I use".
    examples = verified_queries.matching(
        await verified_queries.verified_for(context.org_id, source_of(context)), state.question
    )
    if examples:
        bundle = replace(bundle, verified_applied=examples)

    state.phase = "context"
    state.table_names = list(bundle.table_names)
    state.as_of = bundle.as_of.isoformat()
    await _checkpoint(context, working)
    await events.emit(
        "context_selected",
        {
            "tables": list(bundle.table_names),
            "restrictions": len(bundle.restrictions),
            # How much of the thread this run was given, and whether the thread
            # is what found the tables (D-029). Both belong in the trace: a
            # follow-up answered from three turns of context is a different act
            # from one answered cold, and nothing else would say which happened.
            "history_turns": len(bundle.history),
            # Which definitions governed this run, and how many there were to
            # match (**B-087**). Emitted even when the list is empty, because an
            # empty list beside a non-zero count is the whole finding.
            "definitions_applied": list(state.applied_definitions),
            "definitions_available": state.definitions_available,
            "tables_found_via": "thread" if bundle.cards_from_thread else "question",
            # Which arm of the card search reached each table (**B-018**).
            # `tables_found_via` says which *words* chose them; this says by
            # which *mechanism*, and a run whose tables all came from the
            # lexical arm on a deployment that has an embedder is a retrieval
            # regression with no other symptom.
            "tables_found_by": {card.qualified: card.found_by for card in bundle.cards},
            # In the trace because a person reading an answer about "last month"
            # is owed the date that phrase was resolved against (D-027). It is
            # also the only way to tell a stale answer from a wrong one.
            "as_of": bundle.as_of.isoformat(),
        },
    )

    # The join graph, loaded once. The pairs that cannot be joined are told to
    # the planner **as fact** (4.3), so a well-behaved model avoids the dead end
    # rather than being caught in it — and the loop still checks every proposed
    # statement, because being told is not the same as being bound.
    from dataagent.dal.policy import source_policy

    source_id = source_of(context)

    graph = await load_join_graph(context.org_id, source_id)
    policy = await source_policy(context.org_id, source_id)
    gaps = relevant_pairs(graph.unreachable_pairs(), bundle.table_names)
    chasms = relevant_pairs(graph.comparable_pairs(), bundle.table_names)
    state.capability = {
        "unreachable": [{"left": gap.left, "right": gap.right} for gap in gaps],
        "comparable": [
            {"left": chasm.left, "right": chasm.right, "via": chasm.via} for chasm in chasms
        ],
    }
    await events.emit(
        "capability_checked",
        {
            "unreachable": [f"{gap.left} ↔ {gap.right}" for gap in gaps],
            "comparable": [f"{chasm.left} ↔ {chasm.right} via {chasm.via}" for chasm in chasms],
        },
    )
    notes: list[str] = []
    if gaps:
        notes.append(
            "These tables cannot be combined in one query — this database has no "
            "link between them: "
            + "; ".join(f"{gap.left} and {gap.right}" for gap in gaps)
            + ". Do not write a query joining any such pair; if the question needs "
            "one, set answerable to false and say which link is missing."
        )
    if chasms:
        # Deliberately phrased as an instruction rather than a prohibition
        # (D-026): these pairs have a correct query, and a model told only "do
        # not" would refuse a question it could have answered.
        notes.append(
            "These tables are related only through a shared parent, so joining them "
            "directly multiplies their rows together instead of matching them: "
            + "; ".join(
                f"{chasm.left} and {chasm.right} (both under {chasm.via})" for chasm in chasms
            )
            + ". To combine such a pair, aggregate each side to the shared key in its "
            "own subquery or CTE first, then join the two aggregates. Do not join the "
            "detail rows."
        )
    if notes:
        bundle = bundle.with_capability_note(" ".join(notes))

    async def save() -> None:
        await _checkpoint(context, working)

    async def keep(finding: StateFinding) -> None:
        # Written when it is reached, so an interrupted run keeps what it had
        # concluded and the trace shows the investigation arriving at things in
        # order. `runs.add_finding` emits `finding_added`, which is why the loop
        # does not emit one of its own.
        await runs.add_finding(
            org_id=context.org_id,
            run_id=context.run_id,
            statement=finding.statement,
            support=finding.support,
            confidence=finding.confidence
            if finding.confidence in {"high", "medium", "low"}
            else "medium",
        )

    outcome = await research(
        context=context,
        tools=tools,
        events=events,
        state=state,
        budget=budget,
        bundle=bundle,
        graph=graph,
        dialect=policy.dialect,
        checkpoint=save,
        record_finding=keep,
        settings=settings,
    )

    if outcome.ending == "refused" and not state.executions:
        # Nothing was ever run, so there is nothing to compose from and nothing
        # to cite. Ending here spends no further call to have a model rephrase a
        # refusal it already wrote.
        return await _finalize_refusal(context, events, working, outcome.refusal)

    # Compose, criticise, and — at most once — go round again (arch M9).
    draft = await _compose(context, events, working, outcome, bundle, settings)
    verdict = await _validate(context, events, working, outcome, draft, bundle, settings)

    if verdict.blocked and _may_re_enter(working):
        outcome, draft, verdict = await _second_pass(
            context=context,
            tools=tools,
            events=events,
            working=working,
            bundle=bundle,
            graph=graph,
            dialect=policy.dialect,
            save=save,
            keep=keep,
            verdict=verdict,
            settings=settings,
        )

    state.critic = verdict.as_payload()
    cited = await _verified_citations(draft.supported_by, working, events)
    return await _write_ending(
        context, events, working, draft, cited, verdict=verdict, caveat=outcome.caveat
    )


def _may_re_enter(working: _Working) -> bool:
    """Whether the run has both permission and budget for one more pass.

    Two conditions, and they fail differently. **Once** is architecture M9's
    rule and is absolute — a critic that can keep sending a run back is a loop
    with no ceiling, wearing a different name. The budget is the ordinary one:
    a re-entry that cannot afford its own compose and critic would spend the
    run's last calls producing nothing, so it is not started.
    """
    if working.state.critic_passes >= MAX_CRITIC_PASSES:
        return False
    return working.budget.exhausted() is None


async def _validate(
    context: ToolContext,
    events: EventWriter,
    working: _Working,
    outcome: LoopOutcome,
    draft: FinalizeIn,
    bundle: ContextBundle,
    settings: Settings | None,
) -> CriticVerdict:
    """Both critic stages, and the verdict in the trace.

    Stage 1 is arithmetic and costs nothing. Stage 2 is one cheap call, and it is
    **skipped entirely when stage 1 already blocked** — paying a model to confirm
    what a rule established would be paying for a less reliable answer. It is
    also skipped when the budget is spent, and the trace says which of the two
    happened through `consulted_model`, because "the rules passed and a model
    agreed" and "the rules passed and nobody looked" are different claims.
    """
    from dataagent.llm import service as llm
    from dataagent.llm.base import CallLimits, Message

    state, budget = working.state, working.budget
    state.critic_passes += 1

    evidence = critic.Evidence(
        question=state.question,
        # The thread, for the model half only (D-029). A critic asked whether a
        # draft answers "check again", with no idea what was being checked, will
        # say it does not — and a false block on a correct answer is the failure
        # this critic is most prone to.
        history=bundle.history,
        as_of=date.fromisoformat(state.as_of) if state.as_of else datetime.now(UTC).date(),
        state=state,
        statements=await critic.statements_for(context.org_id, state.execution_ids()),
        previews=outcome.previews,
        dialect=(await source_policy_for(context)).dialect,
        # The definitions this question matched, so the deterministic half can
        # check that the statement honoured them (D-033). Loaded once, on the
        # bundle, rather than re-read here: the critic must judge the same
        # definitions the planner was shown.
        definitions=bundle.definitions_applied,
    )
    deterministic = critic.check(draft, evidence)

    model: critic.CriticOut | None = None
    blocked_already = any(finding.severity == critic.BLOCK for finding in deterministic)
    if not blocked_already and budget.exhausted() is None:
        completion = await llm.complete(
            role="critic",
            org_id=context.org_id,
            messages=[Message(role="user", content=critic.rubric(draft, evidence, deterministic))],
            schema=critic.CriticOut,
            run_id=context.run_id,
            actor_user_id=context.actor_user_id,
            settings=settings,
            # Small on purpose (B-052): the schema is three short reasons, and a
            # ceiling below what a schema can need is how a structured call gets
            # truncated mid-string.
            limits=CallLimits(max_output_tokens=CRITIC_OUTPUT_TOKENS),
        )
        budget.spend_llm(completion.usage.total_tokens)
        model = completion.parsed_as(critic.CriticOut)

    verdict = critic.combine(deterministic, model)
    await events.emit("critic_verdict", verdict.as_payload())
    return verdict


async def _second_pass(
    *,
    context: ToolContext,
    tools: ToolRegistry,
    events: EventWriter,
    working: _Working,
    bundle: ContextBundle,
    graph: JoinGraph,
    dialect: str,
    save: Callable[[], Awaitable[None]],
    keep: Callable[[StateFinding], Awaitable[None]],
    verdict: CriticVerdict,
    settings: Settings | None,
) -> tuple[LoopOutcome, FinalizeIn, CriticVerdict]:
    """The one bounded re-entry (arch M9), and its second verdict.

    The run moves to `validating` and back to `running` — the transition WP7.1
    put in `ALLOWED_TRANSITIONS` for exactly this and which nothing had used
    until now, so the status a person sees while it happens is the truthful one
    rather than a run that appears to have restarted.

    Whatever the second verdict says, this returns and the run finalizes.
    Architecture M9 allows *one* re-entry; a second failure is answered with the
    limitations rather than a third attempt.
    """
    state = working.state
    await runs.transition(org_id=context.org_id, run_id=context.run_id, status="validating")
    await runs.transition(org_id=context.org_id, run_id=context.run_id, status="running")

    # The critic's reasons become open questions, so the loop plans against them
    # rather than repeating the investigation it just finished.
    state.open_questions.extend(finding.detail for finding in verdict.blocking)
    state.phase = "planning"
    await save()

    outcome = await research(
        context=context,
        tools=tools,
        events=events,
        state=state,
        budget=working.budget,
        bundle=bundle,
        graph=graph,
        dialect=dialect,
        checkpoint=save,
        record_finding=keep,
        settings=settings,
    )
    draft = await _compose(
        context, events, working, outcome, bundle, settings, correction=verdict.reasons()
    )
    second = await _validate(context, events, working, outcome, draft, bundle, settings)
    return outcome, draft, second


async def source_policy_for(context: ToolContext) -> SourcePolicy:
    """The policy for this run's source, for whoever needs its dialect."""
    from dataagent.dal.policy import source_policy

    if context.data_source_id is None:  # pragma: no cover - execute_run requires one
        raise ValueError("a run cannot be validated without a data source")
    return await source_policy(context.org_id, context.data_source_id)


async def _compose(
    context: ToolContext,
    events: EventWriter,
    working: _Working,
    outcome: LoopOutcome,
    bundle: ContextBundle,
    settings: Settings | None,
    correction: str = "",
) -> FinalizeIn:
    """One call that turns what the loop found into an answer that cites it.

    Given **summaries and execution ids**, never rows: the loop kept them out of
    its own state for the same reason (4.4), and the rows are already durable and
    masked in `result_artifacts` where a citation can reach them.

    A caveat is *given* to the model rather than left to it. When a ceiling or the
    progress rule stopped the search, the answer has to say so — an answer that
    quietly presents partial evidence as complete is the failure architecture 4.4
    added budgets to make visible.

    Returns the **draft**, not the ending. WP9.1 put the critic between the two:
    a function that composed and recorded in one step left nowhere for a verdict
    to change the outcome. ``correction`` carries the critic's reasons into the
    second attempt, in the same shape as the caveat and for the same reason —
    what an answer must acknowledge is given to the model, not hoped for.
    """
    from dataagent.llm import service as llm
    from dataagent.llm.base import Message

    state, budget = working.state, working.budget
    evidence = (
        "\n".join(
            f"- {reference.execution_id}: {reference.summary}" for reference in state.executions
        )
        or "- (no query returned a result)"
    )
    concluded = "\n".join(f"- {finding.statement}" for finding in state.findings) or "- (none)"
    # The actual rows of the most recent queries. Bounded and already masked, and
    # handed to this one call rather than carried in the state: without them the
    # composer sees only one-line summaries and cannot answer anything whose
    # result has more than one row, which is most real questions.
    results = "\n\n".join(
        f"Result of {execution_id}:\n{rendered}" for execution_id, rendered in outcome.previews
    )

    revise = ""
    if correction:
        # The second attempt. The critic's reasons are the instruction, so the
        # answer either fixes what was wrong or says plainly that it could not.
        revise = (
            "\n\nA reviewer rejected your previous answer for these reasons:\n"
            f"{correction}\n"
            "Address each one. Where the evidence does not let you, say so in the "
            "answer as a stated limitation rather than leaving it out."
        )

    caveat = ""
    if outcome.caveat:
        caveat = (
            f"\n\nIMPORTANT: the investigation stopped early. {outcome.caveat} "
            "Say plainly in your answer that this is a partial result and why, "
            "and answer with what the evidence below does support."
        )

    # The thread, framed (**D-029**). This is the call that writes the words a
    # person reads, so a follow-up composed without it answers *"check again"*
    # instead of the thing being checked again.
    thread = history_block(bundle.history)
    prompt = (
        (f"{thread}\n\n" if thread else "") + f"The question was: {state.question}\n\n"
        f"Queries run, with what each returned:\n{evidence}\n\n"
        f"What you concluded along the way:\n{concluded}\n\n"
        f"{results}{caveat}{revise}\n\n"
        "Answer the question in plain words for the person who asked. Use only "
        "these numbers. Cite in supported_by the execution ids your answer rests "
        "on. If the evidence does not actually answer the question, set answered "
        "to false and say what is missing."
    )
    completion = await llm.complete(
        role="compose",
        org_id=context.org_id,
        messages=[Message(role="user", content=prompt)],
        schema=FinalizeIn,
        run_id=context.run_id,
        actor_user_id=context.actor_user_id,
        settings=settings,
    )
    budget.spend_llm(completion.usage.total_tokens)
    return completion.parsed_as(FinalizeIn)


async def _finalize_refusal(
    context: ToolContext, events: EventWriter, working: _Working, reason: str
) -> RunOutcome:
    """End without a further model call.

    Deliberately not another round trip: the reason is already known, and
    spending a call to have a model rephrase a refusal is money for prose.
    """
    final = FinalizeIn(answer=reason, answered=False, supported_by=[], confidence="high")
    return await _write_ending(context, events, working, final, ())


async def _write_ending(
    context: ToolContext,
    events: EventWriter,
    working: _Working,
    final: FinalizeIn,
    cited: tuple[str, ...],
    verdict: CriticVerdict | None = None,
    caveat: str = "",
) -> RunOutcome:
    """Record the answer, what backs it, what it does not establish, and the trace.

    Findings plural: a loop reaches several, and each is written with the
    executions that support it rather than all of them being folded into one
    sentence. The composed answer still gets a finding of its own when it is
    answered and cited, because that is what the answer card is built around.

    WP9.2 adds the other half of an answer — its **limitations**, assembled by
    `composer` from what the run knows rather than asked of a model, and the
    **cited** mark on the findings the answer rests on.
    """
    state = working.state
    composed = composer.assemble(final, state, verdict, citations=cited, caveat=caveat)
    await runs.record_answer(
        org_id=context.org_id,
        run_id=context.run_id,
        content=final.answer,
        limitations=list(composed.limitations),
    )

    # The loop already persisted each finding as it reached it, so only the
    # composed answer may still need one.
    already = {finding.statement.strip() for finding in state.findings}
    if final.answered and cited and final.answer.strip() not in already:
        # A finding only when there is something to stand behind, and only when
        # the loop did not already record this sentence — otherwise the answer
        # card shows the same claim twice, which the Phase 7 gate caught once
        # already.
        await runs.add_finding(
            org_id=context.org_id,
            run_id=context.run_id,
            statement=final.answer,
            support=cited,
            confidence=final.confidence
            if final.confidence in {"high", "medium", "low"}
            else "medium",
        )

    # Mark the findings this answer rests on. Matched by shared execution, not by
    # text: the composer rephrases, and a match on wording would lose the link
    # exactly when the answer was written well.
    await runs.mark_cited(org_id=context.org_id, run_id=context.run_id, executions=cited)

    state.phase = "finished"
    await _checkpoint(context, working)
    return RunOutcome(
        run_id=context.run_id,
        status="completed",
        answered=final.answered,
        answer=final.answer,
        execution_ids=cited,
        llm_calls=working.budget.llm_calls,
        iterations=state.iteration,
        stopped_by=state.stopped_by,
        limitations=composed.limitations,
    )


async def _verified_citations(
    claimed: list[str], working: _Working, events: EventWriter
) -> tuple[str, ...]:
    """Only ids this run really produced.

    A model that cites an execution it did not run is not lying on purpose — it
    is completing a pattern — but the result is the same: a citation that looks
    checkable and is not. Dropped, and the trace says how many.
    """
    produced = working.state.execution_ids()
    real = [item for item in claimed if item in produced]
    if len(real) != len(claimed):
        await events.emit(
            "error",
            {
                "category": "unverifiable_citation",
                "safe_message": (
                    f"{len(claimed) - len(real)} cited execution id(s) were not produced "
                    "by this run and were dropped"
                ),
            },
        )
    return tuple(real)


async def _question_of(org_id: uuid.UUID, run_id: uuid.UUID) -> str:
    view = await runs.get_run(org_id=org_id, run_id=run_id)
    return view.question


async def _history_of(org_id: uuid.UUID, run_id: uuid.UUID) -> tuple[HistoryTurn, ...]:
    """The thread this question follows (**D-029**, B-064).

    The translation from a database row to a prompt fragment happens here, in one
    line, and that is the whole of what the two dataclasses cost. What it buys is
    that `runs/service.py` never has to know how a thread is worded and
    `agent/context.py` never has to open a session.
    """
    prior = await runs.conversation_history(org_id=org_id, run_id=run_id, turns=HISTORY_TURNS)
    return tuple(HistoryTurn(question=turn.question, answer=turn.answer) for turn in prior)


async def _checkpoint(context: ToolContext, working: _Working) -> None:
    """Persist the working state at a step boundary (architecture 0.2.4).

    **Two columns, written together** (D-023): `state` is the agent's scratchpad
    and `budget` is the ceiling it is held to. Written directly rather than
    through `runs.service`, because this is the run's own working memory rather
    than a transition — and a transition is the one thing `runs.service` insists
    on owning.
    """
    from sqlalchemy import update

    from dataagent.db.models import AgentRun

    async with org_session(context.org_id) as session:
        await session.execute(
            update(AgentRun)
            .where(AgentRun.id == context.run_id)
            .values(state=working.state.as_json(), budget=working.budget.as_json())
        )


async def _record_failure(
    events: EventWriter, working: _Working, message: str, *, category: str
) -> None:
    working.state.phase = "finished"
    await events.emit("error", {"category": category, "safe_message": message[:500]})


def _failed(run_id: uuid.UUID, working: _Working) -> RunOutcome:
    return RunOutcome(
        run_id=run_id,
        status="failed",
        answered=False,
        answer="",
        execution_ids=working.state.execution_ids(),
        llm_calls=working.budget.llm_calls,
        iterations=working.state.iteration,
        stopped_by=working.state.stopped_by,
    )


async def _finish(
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    status: str,
    working: _Working,
    outcome: RunOutcome | None,
) -> None:
    """Move the run to its ending, once, whatever happened above.

    ``budget_exhausted`` carries no failure reason: it is an answer with caveats,
    and the caveat is in the answer itself where the person asking will read it.
    """
    await runs.transition(
        org_id=org_id,
        run_id=run_id,
        status=status,
        failure_reason=("The run could not be completed." if status == "failed" else None),
        totals={
            "llm_calls": working.budget.llm_calls,
            "queries": working.budget.queries,
            "iterations": working.state.iteration,
            "tokens": working.budget.tokens,
            "stopped_by": working.state.stopped_by,
            "answered": outcome.answered if outcome is not None else False,
        },
    )


def _registry_with_finalize() -> ToolRegistry:
    """The default tool set plus ``finalize``.

    ``finalize`` is registered so the model sees it in the tool list and knows
    finishing is an action with a shape — even though this runner calls the
    composing model with the same schema directly rather than waiting to be
    asked. Phase 8's loop dispatches it as a real tool call.
    """
    registry = default_registry()
    registry.register(FINALIZE)
    return registry
