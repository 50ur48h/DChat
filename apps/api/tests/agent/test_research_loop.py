"""The bounded loop: how it goes on, and every way it stops (architecture 4.4).

Driven through `execute_run` rather than by calling `research` directly, because
the properties worth holding are about a *run* — what it spent, what status it
ended in, what a person is told, what survives in the checkpoint. A test that
called the loop in isolation could assert its return value and miss all of that.

The budget is passed in small. Waiting for a real eight-iteration run to exhaust
twenty model calls would make these slow and would test patience rather than the
ceiling; a budget of two proves the same rule and proves it in a second.
"""

from __future__ import annotations

import uuid

from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import create_async_engine

from dataagent.agent.budget import Budget
from dataagent.agent.critic import CriticOut
from dataagent.agent.loop import ReflectFinding, Reflection
from dataagent.agent.planner import Plan
from dataagent.agent.runner import RunOutcome, execute_run
from dataagent.agent.tools.base import ToolContext
from dataagent.agent.tools.finalize import FinalizeIn
from dataagent.llm.fake import FakeLLM
from dataagent.runs import service as runs
from dataagent.runs.events import read_events
from llm_fixture import build_settings

#: Distinct statements, so the duplicate rule does not stop a run that is meant
#: to keep going. Each is legitimate against the fixture's catalog.
STEPS = (
    "SELECT count(*) AS n FROM shops",
    "SELECT count(*) AS n FROM regions",
    "SELECT count(*) AS n FROM products",
    "SELECT count(*) AS n FROM people",
    "SELECT count(*) AS n FROM busy_shops",
)


def _plan(sql: str, *, answerable: bool = True, reason: str = "") -> str:
    return Plan(
        sql=sql, purpose=f"count via {sql[-12:]}", answerable=answerable, reason=reason
    ).model_dump_json()


def _reflect(*, done: bool, statement: str = "") -> str:
    findings = (
        [ReflectFinding(statement=statement, supported_by=[], confidence="medium")]
        if statement
        else []
    )
    return Reflection(
        findings=findings, open_questions=[], next_purpose="", done=done, rationale="carrying on"
    ).model_dump_json()


def _final(answer: str = "Here is what I found.") -> str:
    return FinalizeIn(answer=answer, supported_by=[], confidence="medium").model_dump_json()


async def _run_for(context: ToolContext, question: str) -> uuid.UUID:
    view = await runs.get_run(org_id=context.org_id, run_id=context.run_id)
    asked = await runs.post_message(
        org_id=context.org_id,
        user_id=context.actor_user_id or uuid.uuid4(),
        conversation_id=view.conversation_id,
        content=question,
        idempotency_key=uuid.uuid4().hex,
    )
    return asked.run_id


async def _execute(
    context: ToolContext, run_id: uuid.UUID, budget: Budget | None = None
) -> RunOutcome:
    return await execute_run(
        org_id=context.org_id,
        run_id=run_id,
        data_source_id=context.data_source_id or uuid.uuid4(),
        actor_user_id=context.actor_user_id,
        settings=build_settings(),
        budget=budget,
    )


async def _queries_run(context: ToolContext, run_id: uuid.UUID, wired: URL) -> int:
    """How many statements actually reached the customer's database.

    Asked of `query_executions` rather than of the outcome, because
    `RunOutcome.execution_ids` means *what the answer cited* — a different and
    equally load-bearing question. Conflating them would let a run that queried
    ten times and cited once look like a run that queried once.
    """
    engine = create_async_engine(wired)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, false)"), {"org": str(context.org_id)}
            )
            return (
                await connection.execute(
                    text("SELECT count(*) FROM query_executions WHERE run_id = :run"),
                    {"run": run_id},
                )
            ).scalar_one()
    finally:
        await engine.dispose()


def _script_investigation(fake_llm: FakeLLM, steps: int, *, finish: bool = True) -> None:
    """A distinct query and a distinct finding per iteration.

    Distinct because both loop-safety rules would otherwise fire: the same
    statement twice is a duplicate, and the same finding twice is not progress.
    """
    for index in range(steps):
        fake_llm.script(_plan(STEPS[index]), role="sql", times=1)
        last = finish and index == steps - 1
        fake_llm.script(_reflect(done=last, statement=f"finding {index}"), role="plan", times=1)
    fake_llm.script(_final(), role="compose")
    # A run now ends with a critic pass (WP9.1). Scripted explicitly rather
    # than defaulted, so a critic that stopped running would fail here.
    fake_llm.script(CriticOut(verdict="pass", reasons=[]).model_dump_json(), role="critic")


# ---------------------------------------------------------------------------
# Going on
# ---------------------------------------------------------------------------


async def test_a_four_step_investigation_runs_every_step(
    context: ToolContext, fake_llm: FakeLLM, wired: URL
) -> None:
    """The plan's own criterion: a scripted multi-iteration run is deterministic.

    Four queries, four findings, one answer — and the run ends `completed`
    because nothing cut it short.
    """
    _script_investigation(fake_llm, 4)
    run_id = await _run_for(context, "Tell me about this database")

    outcome = await _execute(context, run_id)

    assert outcome.iterations == 4
    assert outcome.stopped_by is None
    assert await _queries_run(context, run_id, wired) == 4
    assert outcome.llm_calls == 10, "four plans, four reflections, one compose, one critic (WP9.1)"

    view = await runs.get_run(org_id=context.org_id, run_id=run_id)
    assert view.status == "completed"
    assert len(view.findings) == 4, "each finding was written when it was reached"


async def test_the_checkpoint_can_be_read_back_as_the_run_that_wrote_it(
    context: ToolContext, fake_llm: FakeLLM, wired: URL
) -> None:
    """0.2.4's requirement, and what Phase 8's resume path will rebuild from.

    Asserted as a **round-trip through the real column**, not through an
    in-memory object: what matters is that what Postgres holds is still a valid
    `ResearchState`.
    """
    from dataagent.agent.state import ResearchState

    _script_investigation(fake_llm, 3)
    run_id = await _run_for(context, "Tell me about this database")

    await _execute(context, run_id)

    engine = create_async_engine(wired)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, false)"), {"org": str(context.org_id)}
            )
            stored = (
                await connection.execute(
                    text("SELECT state FROM agent_runs WHERE id = :run"), {"run": run_id}
                )
            ).scalar_one()
    finally:
        await engine.dispose()

    restored = ResearchState.restore(stored)

    assert restored is not None, "the checkpoint must read back as a state"
    assert restored.iteration == 3
    assert len(restored.executions) == 3
    assert len(restored.findings) == 3
    assert restored.phase == "finished"


# ---------------------------------------------------------------------------
# Stopping
# ---------------------------------------------------------------------------


async def test_the_iteration_ceiling_ends_the_run_with_caveats(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """A model that never says `done` is stopped by the `for` loop's own range.

    And the ending is `budget_exhausted`, not `failed`: 4.4's guaranteed
    finalize-with-caveats means the person still gets an answer, with the
    limitation stated.
    """
    _script_investigation(fake_llm, 5, finish=False)
    run_id = await _run_for(context, "Tell me everything")

    outcome = await _execute(context, run_id, Budget(iterations=2))

    assert outcome.iterations == 2
    assert outcome.stopped_by == "iterations"

    view = await runs.get_run(org_id=context.org_id, run_id=run_id)
    assert view.status == "budget_exhausted"
    assert view.answer, "a stopped run still answers — that is what 'with caveats' means"
    assert view.failure_reason is None, "a ceiling is not a failure"
    assert "budget_exhausted" in [
        e.type for e in await read_events(org_id=context.org_id, run_id=run_id)
    ]


async def test_the_query_ceiling_ends_the_run(
    context: ToolContext, fake_llm: FakeLLM, wired: URL
) -> None:
    """Queries are bounded separately from iterations because they are what
    touches a customer's database."""
    _script_investigation(fake_llm, 5, finish=False)
    run_id = await _run_for(context, "Tell me everything")

    outcome = await _execute(context, run_id, Budget(queries=2))

    assert outcome.stopped_by == "queries"
    assert await _queries_run(context, run_id, wired) == 2, "it stopped at the ceiling, not past it"


async def test_the_model_call_ceiling_ends_the_run(context: ToolContext, fake_llm: FakeLLM) -> None:
    """The ceiling that bounds spending, checked before a call rather than after."""
    _script_investigation(fake_llm, 5, finish=False)
    run_id = await _run_for(context, "Tell me everything")

    outcome = await _execute(context, run_id, Budget(llm_calls=4))

    assert outcome.stopped_by == "llm_calls"
    # Four inside the loop, then one to compose the answer it is required to
    # give: the compose is the finalize-with-caveats and is not optional.
    assert outcome.llm_calls == 5


async def test_a_run_with_no_time_stops_before_touching_anything(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """Wall clock is checked first, and before the first iteration spends anything.

    A zero allowance is the sharpest version: nothing is planned, nothing is
    queried, and the person is still answered.
    """
    _script_investigation(fake_llm, 1)
    run_id = await _run_for(context, "Tell me everything")

    outcome = await _execute(context, run_id, Budget(wall_seconds=0))

    assert outcome.stopped_by == "wall_seconds"
    assert outcome.iterations == 0
    view = await runs.get_run(org_id=context.org_id, run_id=run_id)
    assert view.status == "budget_exhausted"
    assert view.answer


async def test_two_barren_iterations_stop_the_loop(context: ToolContext, fake_llm: FakeLLM) -> None:
    """4.4's monotone-progress rule. A model that keeps saying "one more thing"
    without concluding anything would otherwise spend the whole budget arriving
    where it started."""
    for index in range(4):
        fake_llm.script(_plan(STEPS[index]), role="sql", times=1)
        # No finding, no open question: nothing moved.
        fake_llm.script(_reflect(done=False), role="plan", times=1)
    fake_llm.script(_final(), role="compose")
    # A run now ends with a critic pass (WP9.1). Scripted explicitly rather
    # than defaulted, so a critic that stopped running would fail here.
    fake_llm.script(CriticOut(verdict="pass", reasons=[]).model_dump_json(), role="critic")
    run_id = await _run_for(context, "Tell me everything")

    outcome = await _execute(context, run_id, Budget(iterations=8))

    assert outcome.iterations == 2, "stopped after two iterations that added nothing"
    assert outcome.stopped_by == "no_progress"

    view = await runs.get_run(org_id=context.org_id, run_id=run_id)
    # Not `budget_exhausted`: nothing was overspent. The run simply had nothing
    # further worth doing, which is an ordinary completion.
    assert view.status == "completed"


async def test_a_repeated_query_counts_as_no_progress_and_is_never_sent(
    context: ToolContext, fake_llm: FakeLLM, wired: URL
) -> None:
    """The duplicate rule and the progress rule meeting, which is the real shape
    of a loop going in circles: it proposes what it already ran, twice."""
    fake_llm.script(_plan(STEPS[0]), role="sql", times=1)
    fake_llm.script(_reflect(done=False, statement="first"), role="plan", times=1)
    # Now it repeats itself, and keeps repeating.
    fake_llm.script(_plan(STEPS[0]), role="sql")
    fake_llm.script(_reflect(done=False), role="plan")
    fake_llm.script(_final(), role="compose")
    # A run now ends with a critic pass (WP9.1). Scripted explicitly rather
    # than defaulted, so a critic that stopped running would fail here.
    fake_llm.script(CriticOut(verdict="pass", reasons=[]).model_dump_json(), role="critic")
    run_id = await _run_for(context, "Tell me everything")

    outcome = await _execute(context, run_id, Budget(iterations=8))

    engine = create_async_engine(wired)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, false)"), {"org": str(context.org_id)}
            )
            sent = (
                await connection.execute(
                    text("SELECT count(*) FROM query_executions WHERE run_id = :run"),
                    {"run": run_id},
                )
            ).scalar_one()
    finally:
        await engine.dispose()

    assert sent == 1, "the repeats never reached the database"
    assert outcome.stopped_by == "no_progress"
    assert outcome.iterations < 8, "it stopped well short of the ceiling"
