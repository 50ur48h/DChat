"""One question, answered or honestly refused (architecture M7, Part 4.4).

The single-shot path: context → plan → `run_sql` → **at most one** repair →
finalize. Phase 8 replaces the middle with a bounded loop; the shape of the ends
does not change, which is why they are built here.

Four rules hold this together, and each exists because of a specific way an
agent goes wrong.

**The repair happens once, and only for something rewriting can fix.** A
hallucinated column is repairable — the DAL says which identifier it refused, and
a second attempt with that fed back usually lands. A connection failure is not:
retrying the same statement against a database that is down spends a call to
learn what we already knew. `ToolResult.repairable` is the flag, set by the tool
layer, and the runner never second-guesses it.

**A refusal is an ending, not a failure.** A run that could not answer completes
with `answered=false` and a reason. `failed` is reserved for the platform
breaking. Getting this wrong would either hide real breakage among honest
refusals or dress a refusal up as an outage.

**Citations are verified before they are stored.** The model may only cite
execution ids this run actually produced. Anything else is dropped and the trace
says so — architecture 4.2 makes findings-cite-real-rows the spine of trust, and
a citation nobody can resolve looks like evidence while being none.

**Every exit ends the run exactly once.** The transition is in a `finally`, so a
crash anywhere above still moves the run out of `running` and writes its
`run_finished`. A dangling run is the one outcome with no user-visible symptom
until somebody wonders why a page has been spinning for an hour.

The runner takes a run id and never touches a request, a response or a session
from the web layer. That is what makes architecture 0.2.4's promotion path free:
the same code moves behind a worker in V1.5 without a rewrite.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from dataagent.agent.context import ContextBundle, build_context
from dataagent.agent.planner import Plan, plan_query
from dataagent.agent.tools.base import ToolContext, ToolResult
from dataagent.agent.tools.finalize import FINALIZE, FinalizeIn
from dataagent.agent.tools.registry import ToolRegistry, default_registry
from dataagent.agent.tools.sql import RunSqlOut
from dataagent.config import Settings
from dataagent.llm.base import LLMError
from dataagent.runs import service as runs
from dataagent.runs.events import EventWriter
from dataagent.tenancy.session import org_session

__all__ = ["RunOutcome", "execute_run"]

#: Architecture M7: plan, one repair, finalize. Enforced in the controller and
#: never in the prompt (4.4) — a budget a model is merely told about is a wish.
MAX_LLM_CALLS = 3


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """What happened, for a caller that wants to know without re-reading the run."""

    run_id: uuid.UUID
    status: str
    answered: bool
    answer: str
    execution_ids: tuple[str, ...] = ()
    llm_calls: int = 0
    repaired: bool = False


@dataclass
class _State:
    """The run's working state, checkpointed at every step boundary.

    Architecture 0.2.4 requires the checkpoint because a redeploy kills in-flight
    runs; this is what a resumable run will be rebuilt from in Phase 8, and what
    makes an interrupted one explicable now.
    """

    step: str = "starting"
    llm_calls: int = 0
    executions: list[str] = field(default_factory=list[str])
    repaired: bool = False
    plan_sql: str = ""

    def as_json(self) -> dict[str, object]:
        return {
            "step": self.step,
            "llm_calls": self.llm_calls,
            "executions": list(self.executions),
            "repaired": self.repaired,
            "plan_sql": self.plan_sql,
        }


async def execute_run(
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    data_source_id: uuid.UUID,
    actor_user_id: uuid.UUID | None = None,
    role: str = "reader",
    registry: ToolRegistry | None = None,
    settings: Settings | None = None,
) -> RunOutcome:
    """Drive one queued run to an ending. Never raises for the question's sake.

    Takes ids rather than objects so it can be called from anywhere — a request,
    a background task, a script, a test — which is the constraint that keeps the
    V1.5 move behind a worker free.
    """
    tools = registry if registry is not None else _registry_with_finalize()
    events = EventWriter(org_id=org_id, run_id=run_id)
    state = _State()
    context = ToolContext(
        org_id=org_id,
        run_id=run_id,
        role=role,
        actor_user_id=actor_user_id,
        data_source_id=data_source_id,
    )

    await runs.transition(org_id=org_id, run_id=run_id, status="running")
    ending, outcome = "failed", None
    try:
        outcome = await _investigate(
            context=context, tools=tools, events=events, state=state, settings=settings
        )
        ending = "completed"
        return outcome
    except LLMError as error:
        # The provider, not the question. Sanitized already, and distinct from a
        # refusal: the user should be told the platform failed, not that their
        # data could not answer them.
        await _record_failure(events, state, str(error), category="llm_error")
        return _failed(run_id, state)
    except Exception as error:
        await _record_failure(events, state, str(error), category="internal_error")
        return _failed(run_id, state)
    finally:
        # In `finally`, because a run that never ends is the one failure with no
        # symptom until somebody notices a page spinning.
        await _finish(org_id=org_id, run_id=run_id, status=ending, state=state, outcome=outcome)


async def _investigate(
    *,
    context: ToolContext,
    tools: ToolRegistry,
    events: EventWriter,
    state: _State,
    settings: Settings | None,
) -> RunOutcome:
    question = await _question_of(context.org_id, context.run_id)

    bundle = await build_context(
        org_id=context.org_id, question=question, data_source_id=context.data_source_id
    )
    state.step = "context"
    await _checkpoint(context, state)
    await events.emit(
        "context_selected",
        {"tables": list(bundle.table_names), "restrictions": len(bundle.restrictions)},
    )

    plan, calls = await _plan(context, bundle, tools, events, state, settings, repair_of=None)
    state.llm_calls += calls
    if not plan.answerable:
        # The model says the catalog cannot answer this. Believed, because it has
        # just been shown the catalog — and cheaper than a refusal from the DAL.
        return await _finalize_refusal(
            context, events, state, plan.reason or "The available data cannot answer that."
        )

    result = await _run_sql(context, tools, events, state, plan)

    if not result.ok and result.repairable and state.llm_calls < MAX_LLM_CALLS - 1:
        state.repaired = True
        state.step = "repairing"
        await _checkpoint(context, state)
        repaired, calls = await _plan(
            context, bundle, tools, events, state, settings, repair_of=_feedback(plan, result)
        )
        state.llm_calls += calls
        if repaired.answerable:
            plan = repaired
            result = await _run_sql(context, tools, events, state, plan)

    if not result.ok:
        # Repaired-or-refused: the second attempt failed too, so the run ends
        # honestly and says what was refused rather than apologising.
        return await _finalize_refusal(
            context,
            events,
            state,
            f"I could not answer that from this data. The query was refused: {result.error}",
        )

    return await _compose(context, tools, events, state, plan, result, settings)


async def _plan(
    context: ToolContext,
    bundle: ContextBundle,
    tools: ToolRegistry,
    events: EventWriter,
    state: _State,
    settings: Settings | None,
    *,
    repair_of: str | None,
) -> tuple[Plan, int]:
    plan, tokens = await plan_query(
        org_id=context.org_id,
        bundle=bundle,
        registry=tools,
        role=context.role,
        run_id=context.run_id,
        actor_user_id=context.actor_user_id,
        settings=settings,
        repair_of=repair_of,
    )
    state.step = "planned"
    state.plan_sql = plan.sql
    await _checkpoint(context, state)
    await events.emit(
        "plan_created",
        {
            "purpose": plan.purpose,
            "answerable": plan.answerable,
            "repair": repair_of is not None,
            "tokens": tokens,
        },
    )
    return plan, 1


async def _run_sql(
    context: ToolContext,
    tools: ToolRegistry,
    events: EventWriter,
    state: _State,
    plan: Plan,
) -> ToolResult:
    result = await tools.call(
        context, "run_sql", {"sql": plan.sql, "purpose": plan.purpose}, events=events
    )
    if result.ok and isinstance(result.data, RunSqlOut):
        state.executions.append(result.data.execution_id)
        await events.emit(
            "query_executed",
            {
                "execution_id": result.data.execution_id,
                "row_count": result.data.row_count,
                "duration_ms": result.data.duration_ms,
                "masked_columns": result.data.masked_columns,
            },
        )
    else:
        # `sql_rejected` rather than `error`: the registry has already recorded
        # the failure generically, and 10.3 has a type that says *this* was the
        # SQL policy refusing, which is what a trace reader wants to see.
        await events.emit(
            "sql_rejected", {"rule": result.code or "unknown", "repairable": result.repairable}
        )
    state.step = "executed"
    await _checkpoint(context, state)
    return result


async def _compose(
    context: ToolContext,
    tools: ToolRegistry,
    events: EventWriter,
    state: _State,
    plan: Plan,
    result: ToolResult,
    settings: Settings | None,
) -> RunOutcome:
    """The last call: turn rows into an answer that cites them."""
    from dataagent.llm import service as llm
    from dataagent.llm.base import Message

    prompt = (
        f"The question was: {await _question_of(context.org_id, context.run_id)}\n\n"
        f"You ran this query for: {plan.purpose}\n\n{plan.sql}\n\n"
        f"{result.render()}\n\n"
        "Answer the question in plain words for the person who asked. Use only "
        "these numbers. Cite the execution id above in supported_by. If the rows "
        "do not actually answer the question, set answered to false and say so."
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
    state.llm_calls += 1
    final = completion.parsed_as(FinalizeIn)

    cited = await _verified_citations(final.supported_by, state, events)
    return await _write_ending(context, events, state, final, cited)


async def _finalize_refusal(
    context: ToolContext, events: EventWriter, state: _State, reason: str
) -> RunOutcome:
    """End without a further model call.

    Deliberately not another round trip: the reason is already known, and
    spending a call to have a model rephrase a refusal is money for prose.
    """
    final = FinalizeIn(answer=reason, answered=False, supported_by=[], confidence="high")
    return await _write_ending(context, events, state, final, ())


async def _write_ending(
    context: ToolContext,
    events: EventWriter,
    state: _State,
    final: FinalizeIn,
    cited: tuple[str, ...],
) -> RunOutcome:
    """Record the answer, the finding behind it, and the trace entry."""
    await runs.record_answer(org_id=context.org_id, run_id=context.run_id, content=final.answer)
    if final.answered and cited:
        # A finding only when there is something to stand behind. A refusal has
        # no finding — it concluded nothing about the data.
        await runs.add_finding(
            org_id=context.org_id,
            run_id=context.run_id,
            statement=final.answer,
            support=cited,
            confidence=final.confidence
            if final.confidence in {"high", "medium", "low"}
            else "medium",
        )
    state.step = "answered"
    await _checkpoint(context, state)
    return RunOutcome(
        run_id=context.run_id,
        status="completed",
        answered=final.answered,
        answer=final.answer,
        execution_ids=cited,
        llm_calls=state.llm_calls,
        repaired=state.repaired,
    )


async def _verified_citations(
    claimed: list[str], state: _State, events: EventWriter
) -> tuple[str, ...]:
    """Only ids this run really produced.

    A model that cites an execution it did not run is not lying on purpose — it
    is completing a pattern — but the result is the same: a citation that looks
    checkable and is not. Dropped, and the trace says how many.
    """
    real = [item for item in claimed if item in state.executions]
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


def _feedback(plan: Plan, result: ToolResult) -> str:
    """What the one repair attempt is told.

    The refused statement and the reason, and an instruction to fix rather than
    to try something else — a model told only "that failed" tends to rewrite the
    question instead of the query.
    """
    return (
        "Your previous statement was refused and did not run.\n\n"
        f"Statement:\n{plan.sql}\n\n"
        f"Reason ({result.code}): {result.error}\n\n"
        "Write a corrected statement that fixes exactly that problem, using only "
        "tables and columns from the reference data. If the reference data cannot "
        "answer the question, set answerable to false instead of guessing again."
    )


async def _question_of(org_id: uuid.UUID, run_id: uuid.UUID) -> str:
    view = await runs.get_run(org_id=org_id, run_id=run_id)
    return view.question


async def _checkpoint(context: ToolContext, state: _State) -> None:
    """Persist the working state at a step boundary (architecture 0.2.4).

    Written directly rather than through `runs.service`, because this is the
    run's own scratch space rather than a transition — and a transition is the
    one thing `runs.service` insists on owning.
    """
    from sqlalchemy import update

    from dataagent.db.models import AgentRun

    async with org_session(context.org_id) as session:
        await session.execute(
            update(AgentRun).where(AgentRun.id == context.run_id).values(state=state.as_json())
        )


async def _record_failure(
    events: EventWriter, state: _State, message: str, *, category: str
) -> None:
    state.step = "failed"
    await events.emit("error", {"category": category, "safe_message": message[:500]})


def _failed(run_id: uuid.UUID, state: _State) -> RunOutcome:
    return RunOutcome(
        run_id=run_id,
        status="failed",
        answered=False,
        answer="",
        execution_ids=tuple(state.executions),
        llm_calls=state.llm_calls,
        repaired=state.repaired,
    )


async def _finish(
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    status: str,
    state: _State,
    outcome: RunOutcome | None,
) -> None:
    """Move the run to its ending, once, whatever happened above."""
    await runs.transition(
        org_id=org_id,
        run_id=run_id,
        status=status,
        failure_reason=None if status == "completed" else "The run could not be completed.",
        totals={
            "llm_calls": state.llm_calls,
            "queries": len(state.executions),
            "repaired": state.repaired,
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
