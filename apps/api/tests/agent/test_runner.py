"""The ends of a run: asked, investigated, answered or honestly refused.

These were M7's paths and they are still the contract — WP8.1b replaced the
middle with a bounded loop and promised the ends would not change, and this file
is where that promise is checked. What moved is *how* a bad query is corrected:
there is no longer a special repair step, because a second attempt is simply the
next iteration.

FakeLLM for the model and a real database for everything else, which is the
split that matters: the answer has to be deterministic for a test to mean
anything, but the *refusal* has to come from the real DAL against a real
catalog, or the test proves only that a fake can be told to fail.

An iteration now costs two scripted calls — `sql` to plan and `plan` to reflect —
so every test here scripts both. A missing script is not silent: the FakeLLM
raises and names the role it wanted.

Assertions are on what the agent **did** — the trace, the rows it left, the
calls it made — rather than on the wording of an answer. A test that asserted on
prose would break every time a prompt improved, which teaches people to stop
reading failures.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, date, datetime
from typing import cast

import pytest
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import create_async_engine

from dataagent.agent.critic import CriticOut
from dataagent.agent.loop import ReflectFinding, Reflection
from dataagent.agent.planner import Plan
from dataagent.agent.runner import RunOutcome, execute_run
from dataagent.agent.tools.base import ToolContext
from dataagent.agent.tools.finalize import FinalizeIn
from dataagent.llm.base import LLMError
from dataagent.llm.fake import FakeLLM
from dataagent.runs import service as runs
from dataagent.runs.events import read_events
from llm_fixture import build_settings


def _plan(sql: str, *, answerable: bool = True, reason: str = "") -> str:
    return Plan(
        sql=sql, purpose="answer the question", answerable=answerable, reason=reason
    ).model_dump_json()


def _reflect(
    *,
    done: bool = True,
    findings: list[ReflectFinding] | None = None,
    open_questions: list[str] | None = None,
) -> str:
    """One reflection. `done=True` makes the loop a single iteration, which is the
    single-shot shape most of these tests were written against."""
    return Reflection(
        findings=findings or [],
        open_questions=open_questions or [],
        next_purpose="",
        done=done,
        rationale="that answers it",
    ).model_dump_json()


def _final(answer: str, *, answered: bool = True, cite: list[str] | None = None) -> str:
    return FinalizeIn(
        answer=answer, answered=answered, supported_by=cite or [], confidence="high"
    ).model_dump_json()


def _passes() -> str:
    """A critic that finds nothing. Scripted explicitly in every test that gets
    as far as an answer, because a run now ends with a verdict and a fixture
    that supplied one silently would hide the critic having stopped running."""
    return CriticOut(verdict="pass", reasons=[]).model_dump_json()


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _reflects_citing_the_execution(request: object) -> str:
    """Reflect with a finding that cites the execution just run.

    The id is minted at run time, so it is read out of the prompt for the reason
    `_cites_the_execution` gives — and reading it is itself the assertion that
    the reflecting model is shown what it ran.
    """
    text_of = getattr(request, "prompt_text", "")
    found = _UUID.search(text_of)
    assert found is not None, f"the reflecting prompt named no execution: {text_of}"
    return _reflect(
        findings=[
            ReflectFinding(statement="Shop counts are available.", supported_by=[found.group(0)])
        ]
    )


def _cites_the_execution(request: object) -> str:
    """Compose by reading the execution id out of the prompt it was given.

    The alternative — hard-coding an id — cannot work, because the id is minted
    at run time. This is also a real assertion in disguise: if the runner ever
    stops putting the execution id in front of the composing model, there is no
    id to find and this raises rather than quietly citing nothing.
    """
    text_of = getattr(request, "prompt_text", "")
    found = _UUID.search(text_of)
    assert found is not None, f"the composing prompt named no execution: {text_of}"
    return _final("There are 3 shops.", cite=[found.group(0)])


async def _run_for(context: ToolContext, question: str) -> uuid.UUID:
    """A fresh queued run in the fixture's conversation."""
    view = await runs.get_run(org_id=context.org_id, run_id=context.run_id)
    asked = await runs.post_message(
        org_id=context.org_id,
        user_id=context.actor_user_id or uuid.uuid4(),
        conversation_id=view.conversation_id,
        content=question,
        idempotency_key=uuid.uuid4().hex,
    )
    return asked.run_id


async def _execute(context: ToolContext, run_id: uuid.UUID) -> RunOutcome:
    return await execute_run(
        org_id=context.org_id,
        run_id=run_id,
        data_source_id=context.data_source_id or uuid.uuid4(),
        actor_user_id=context.actor_user_id,
        settings=build_settings(),
    )


async def _types(context: ToolContext, run_id: uuid.UUID) -> list[str]:
    return [e.type for e in await read_events(org_id=context.org_id, run_id=run_id)]


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


async def test_a_question_becomes_sql_rows_and_a_cited_answer(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """The M7 criterion end to end: asked, answered, and the answer cites a real
    execution row."""
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_cites_the_execution, role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "How many shops are there?")

    outcome = await _execute(context, run_id)

    assert outcome.status == "completed"
    assert outcome.answered is True
    assert outcome.iterations == 1, "one step was enough, so one step is what it took"
    assert outcome.stopped_by is None, "nothing cut this short"
    assert len(outcome.execution_ids) == 1

    view = await runs.get_run(org_id=context.org_id, run_id=run_id)
    assert view.status == "completed"
    assert view.answer == "There are 3 shops."
    assert view.finished_at is not None
    # The finding is the citation, and it names the row the DAL really wrote.
    assert len(view.findings) == 1
    assert view.findings[0].support == list(outcome.execution_ids)


async def test_a_rephrased_answer_is_not_a_second_finding(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """**B-096.** The Phase 7 rule is *one claim once*, and the guard that
    enforced it compared characters — so the composer doing its job, rephrasing
    a finding into an answer, defeated it.

    A live run showed an answer card with two "high confidence" badges and two
    "show the query" controls over a single query: *"Monthly order counts are
    available for all 18 months…"* beside *"Orders by month: February 2025:
    3,624; …"*, both citing the same execution.

    Two claims resting on exactly the same executions are one claim, whatever
    words they use — which is the rule `mark_cited` already followed, one line
    below the one that did not.
    """
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    # The loop reaches a finding of its own, in its own words, **citing the same
    # execution the answer will cite** — which is the live case: two phrasings
    # of one claim resting on one query.
    fake_llm.script(_reflects_citing_the_execution, role="plan")
    fake_llm.script(_cites_the_execution, role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "How many shops are there?")

    await _execute(context, run_id)

    view = await runs.get_run(org_id=context.org_id, run_id=run_id)
    statements = [finding.statement for finding in view.findings]
    assert len(view.findings) == 1, (
        f"the same claim was recorded twice, in two phrasings: {statements}"
    )


async def test_a_genuinely_new_claim_still_becomes_a_finding(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """The other side of B-096, so the fix cannot be "record nothing".

    A run whose loop reached no finding still has an answer to stand behind, and
    the card is built around a claim that points at the row supporting it.
    """
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_cites_the_execution, role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "How many shops are there?")

    outcome = await _execute(context, run_id)

    view = await runs.get_run(org_id=context.org_id, run_id=run_id)
    assert len(view.findings) == 1
    assert view.findings[0].support == list(outcome.execution_ids)


async def test_the_trace_tells_the_whole_story_in_order(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """A trace that skips a step is worse than none: it looks complete."""
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_cites_the_execution, role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "How many shops are there?")

    await _execute(context, run_id)

    assert await _types(context, run_id) == [
        "run_started",
        "context_selected",
        # The join graph is checked once, up front, and the verdict is in the
        # trace whether or not it found anything (4.3).
        "capability_checked",
        # One iteration, and every stage of it is visible: the loop's steps are
        # what WP8.3's timeline will render, so a stage missing here is a stage
        # missing from the product's account of itself.
        "step_started",
        "plan_created",
        "tool_called",
        "query_executed",
        "result_summarized",
        "reflection",
        # The draft is judged before it becomes the answer (WP9.1): a verdict
        # recorded after publication would be a review of something shipped.
        "critic_verdict",
        "answer_composed",
        # The finding is written after the answer and before the ending: a
        # conclusion the trace does not mention is one the user cannot check.
        "finding_added",
        "run_finished",
    ]


async def test_the_execution_row_is_attributed_to_this_run(
    context: ToolContext, fake_llm: FakeLLM, wired: URL
) -> None:
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_cites_the_execution, role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "How many shops are there?")

    outcome = await _execute(context, run_id)

    engine = create_async_engine(wired)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, false)"),
                {"org": str(context.org_id)},
            )
            rows = (
                await connection.execute(
                    text("SELECT id, status FROM query_executions WHERE run_id = :run"),
                    {"run": run_id},
                )
            ).all()
    finally:
        await engine.dispose()

    assert [str(row.id) for row in rows] == list(outcome.execution_ids)
    assert rows[0].status == "ok"


# ---------------------------------------------------------------------------
# Corrected — what used to be "repaired"
# ---------------------------------------------------------------------------


async def test_a_hallucinated_column_is_corrected_on_the_next_iteration(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """The path M7 named, now served by the loop rather than a special case.

    The first statement invents a column, the DAL refuses it, and the next
    iteration — given the refusal — succeeds. WP7.2b spent a dedicated repair
    step on this; a loop gets it for free, which is why `repaired` stopped being
    a thing a run has.
    """
    fake_llm.script(_plan("SELECT revenue_total FROM shops"), role="sql", times=1)
    fake_llm.script(_reflect(done=False), role="plan", times=1)
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql", times=1)
    fake_llm.script(_reflect(), role="plan", times=1)
    fake_llm.script(_cites_the_execution, role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "How much revenue?")

    outcome = await _execute(context, run_id)

    assert outcome.answered is True
    assert outcome.iterations == 2, "one iteration to be refused, one to succeed"
    assert outcome.stopped_by is None
    assert await _types(context, run_id) == [
        "run_started",
        "context_selected",
        "capability_checked",
        "step_started",
        "plan_created",
        "tool_called",
        "error",
        "sql_rejected",
        "reflection",
        "step_started",
        "plan_created",
        "tool_called",
        "query_executed",
        "result_summarized",
        "reflection",
        # The draft is judged before it becomes the answer (WP9.1): a verdict
        # recorded after publication would be a review of something shipped.
        "critic_verdict",
        "answer_composed",
        "finding_added",
        "run_finished",
    ]


async def test_the_next_plan_is_told_what_was_refused(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """Feeding the violation back is the whole reason the second attempt works.
    Without it the model rewrites the question instead of the query."""
    fake_llm.script(_plan("SELECT revenue_total FROM shops"), role="sql", times=1)
    fake_llm.script(_reflect(done=False), role="plan", times=1)
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql", times=1)
    fake_llm.script(_reflect(), role="plan", times=1)
    fake_llm.script(_cites_the_execution, role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "How much revenue?")

    await _execute(context, run_id)

    second = fake_llm.calls_for("sql")[1].prompt
    assert "refused" in second
    assert "revenue_total" in second


async def test_the_same_statement_is_never_sent_twice(
    context: ToolContext, fake_llm: FakeLLM, wired: URL
) -> None:
    """4.4's duplicate rule, end to end. A model that keeps proposing the query it
    already ran must not keep paying for it — the second proposal never reaches
    the database at all."""
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_reflect(done=False), role="plan")
    fake_llm.script(_cites_the_execution, role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "How many shops?")

    await _execute(context, run_id)

    engine = create_async_engine(wired)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, false)"),
                {"org": str(context.org_id)},
            )
            count = (
                await connection.execute(
                    text("SELECT count(*) FROM query_executions WHERE run_id = :run"),
                    {"run": run_id},
                )
            ).scalar_one()
    finally:
        await engine.dispose()

    assert count == 1, "the repeat was refused before it was sent"


async def test_a_refusal_that_is_never_corrected_ends_honestly(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """Corrected-**or-refused**. A statement that stays bad ends the run with an
    answer that says what was refused, not with a failure and not with a
    fabrication."""
    fake_llm.script(_plan("SELECT revenue_total FROM shops"), role="sql")
    fake_llm.script(_reflect(done=False), role="plan")
    fake_llm.script(_final("I could not answer that.", answered=False), role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "How much revenue?")

    outcome = await _execute(context, run_id)

    assert outcome.status == "completed", "an honest refusal is an ending, not a failure"
    assert outcome.answered is False
    assert outcome.execution_ids == ()
    view = await runs.get_run(org_id=context.org_id, run_id=run_id)
    # Nothing was concluded about the data, so there is nothing to stand behind.
    assert view.findings == []


async def test_a_failure_rewriting_cannot_fix_stops_the_loop(
    context: ToolContext, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rewriting cannot fix a database that is down, so another iteration would
    spend the whole budget learning what this one already knows. `repairable` is
    the flag, set by the tool layer, and the loop does not second-guess it."""
    from dataagent.agent.tools import registry as registry_module

    real_call = registry_module.ToolRegistry.call

    async def unreachable(self: object, *args: object, **kwargs: object) -> object:
        from dataagent.agent.tools.base import ToolResult

        return ToolResult(
            tool="run_sql",
            ok=False,
            error="the database is unreachable",
            code="connection_failed",
            repairable=False,
        )

    monkeypatch.setattr(registry_module.ToolRegistry, "call", unreachable)
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    run_id = await _run_for(context, "How many?")

    outcome = await _execute(context, run_id)

    assert real_call is not None
    assert outcome.answered is False
    assert outcome.iterations == 1, "it stopped rather than trying again"
    # One plan call and nothing else: no reflection, no compose. There is nothing
    # to reflect on and nothing to compose from.
    assert outcome.llm_calls == 1


# ---------------------------------------------------------------------------
# Refused before anything ran
# ---------------------------------------------------------------------------


async def test_a_question_the_catalog_cannot_answer_refuses_without_querying(
    context: ToolContext, fake_llm: FakeLLM, wired: URL
) -> None:
    """`answerable=false` is believed, because the model has just been shown the
    catalog — and it saves a refusal round trip through the DAL."""
    fake_llm.script(
        _plan("SELECT 1", answerable=False, reason="There is no revenue column anywhere."),
        role="sql",
    )
    run_id = await _run_for(context, "What was our profit margin?")

    outcome = await _execute(context, run_id)

    assert outcome.answered is False
    assert outcome.status == "completed"
    assert outcome.llm_calls == 1, "a refusal should not pay for a composing call"
    assert outcome.iterations == 1
    view = await runs.get_run(org_id=context.org_id, run_id=run_id)
    assert view.answer == "There is no revenue column anywhere."

    engine = create_async_engine(wired)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, false)"),
                {"org": str(context.org_id)},
            )
            count = (
                await connection.execute(
                    text("SELECT count(*) FROM query_executions WHERE run_id = :run"),
                    {"run": run_id},
                )
            ).scalar_one()
    finally:
        await engine.dispose()

    assert count == 0, "nothing should have been sent to the customer's database"


# ---------------------------------------------------------------------------
# Citations, and what is not allowed to be one
# ---------------------------------------------------------------------------


async def test_a_cited_execution_the_run_never_produced_is_dropped(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """Architecture 4.2: findings may only cite real rows. A model completing a
    pattern is not lying on purpose, and the result is the same — a citation that
    looks checkable and is not."""
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_final("There are 3 shops.", cite=[str(uuid.uuid4())]), role="compose")
    run_id = await _run_for(context, "How many shops?")

    outcome = await _execute(context, run_id)

    assert outcome.execution_ids == ()
    assert "unverifiable_citation" in json.dumps(
        [e.payload for e in await read_events(org_id=context.org_id, run_id=run_id)]
    )
    view = await runs.get_run(org_id=context.org_id, run_id=run_id)
    assert view.findings == [], "an answer with no verifiable support has no finding"


# ---------------------------------------------------------------------------
# When the platform breaks
# ---------------------------------------------------------------------------


async def test_a_provider_failure_fails_the_run_rather_than_refusing_it(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """A refusal says the *data* could not answer. A provider outage is ours, and
    telling a user their data was inadequate would be a lie."""
    fake_llm.script(raises=LLMError("the provider is unreachable"), role="sql")
    run_id = await _run_for(context, "How many shops?")

    outcome = await _execute(context, run_id)

    assert outcome.status == "failed"
    view = await runs.get_run(org_id=context.org_id, run_id=run_id)
    assert view.status == "failed"
    assert view.finished_at is not None, "a failed run must still end"
    assert "error" in await _types(context, run_id)


async def test_a_run_always_ends_even_when_the_agent_crashes(
    context: ToolContext, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `finally`. A dangling run is the one failure with no symptom until
    somebody wonders why a page has been spinning for an hour."""
    from dataagent.agent import runner as runner_module

    async def explode(**kwargs: object) -> object:
        raise RuntimeError("something nobody predicted")

    monkeypatch.setattr(runner_module, "build_context", explode)
    run_id = await _run_for(context, "How many shops?")

    outcome = await _execute(context, run_id)

    assert outcome.status == "failed"
    view = await runs.get_run(org_id=context.org_id, run_id=run_id)
    assert view.status == "failed"
    assert view.finished_at is not None
    assert (await _types(context, run_id))[-1] == "run_finished"


# ---------------------------------------------------------------------------
# The checkpoint
# ---------------------------------------------------------------------------


async def test_the_run_state_is_checkpointed_as_it_goes(
    context: ToolContext, fake_llm: FakeLLM, wired: URL
) -> None:
    """Architecture 0.2.4: a redeploy kills in-flight runs, so state is persisted
    at every step boundary. This is what a resumable run will be rebuilt from."""
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_cites_the_execution, role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "How many shops?")

    outcome = await _execute(context, run_id)

    engine = create_async_engine(wired)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, false)"),
                {"org": str(context.org_id)},
            )
            state = (
                await connection.execute(
                    text("SELECT state FROM agent_runs WHERE id = :run"), {"run": run_id}
                )
            ).scalar_one()
    finally:
        await engine.dispose()

    assert state["phase"] == "finished"
    assert [e["execution_id"] for e in state["executions"]] == list(outcome.execution_ids)
    assert state["plan"][0]["sql"].startswith("SELECT")
    # Rows are never in the checkpoint — only a summary and the reference (4.4).
    assert "rows" not in json.dumps(state["executions"])

    # And the budget is in its own column, not inside the state (D-023): a limit
    # that travels inside the thing it limits is one bad deserialization away
    # from being editable.
    assert "budget" not in state
    engine = create_async_engine(wired)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, false)"),
                {"org": str(context.org_id)},
            )
            spent = (
                await connection.execute(
                    text("SELECT budget FROM agent_runs WHERE id = :run"), {"run": run_id}
                )
            ).scalar_one()
    finally:
        await engine.dispose()
    assert spent["iterations"] == 1
    assert spent["queries"] == 1
    assert spent["limits"]["iterations"] == 8, "the allowance this run was given"


# ---------------------------------------------------------------------------
# What this run called today (B-005, D-027)
# ---------------------------------------------------------------------------


async def test_the_trace_records_the_date_relative_periods_were_resolved_against(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """A person reading an answer about "last month" is owed the date that phrase
    meant. It is also the only way to tell a stale answer from a wrong one."""
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_cites_the_execution, role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "How many shops did we have last month?")

    await execute_run(
        org_id=context.org_id,
        run_id=run_id,
        data_source_id=context.data_source_id or uuid.uuid4(),
        actor_user_id=context.actor_user_id,
        settings=build_settings(),
        as_of=date(2026, 7, 15),
    )

    events = await read_events(org_id=context.org_id, run_id=run_id)
    selected = next(e for e in events if e.type == "context_selected")
    assert selected.payload["as_of"] == "2026-07-15"


async def test_a_pinned_date_reaches_the_prompt_the_model_is_given(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """The mechanism the eval harness depends on. Asserted against the prompt
    itself rather than the trace, because the trace could be right while the
    model was told something else — which is the exact shape of B-051."""
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_cites_the_execution, role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "How many shops did we have last month?")

    await execute_run(
        org_id=context.org_id,
        run_id=run_id,
        data_source_id=context.data_source_id or uuid.uuid4(),
        actor_user_id=context.actor_user_id,
        settings=build_settings(),
        as_of=date(2026, 7, 15),
    )

    planning = fake_llm.calls_for("sql")[0]
    system = " ".join(m.content for m in planning.request.messages if m.role == "system")
    assert "Today is 2026-07-15" in system
    assert "CURRENT_DATE" in system, "the escape is named, not merely the date"


async def test_a_run_with_no_date_given_anchors_on_today(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """The default is the wall clock: what a person asking in a browser means."""
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_cites_the_execution, role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "How many shops are there?")

    await _execute(context, run_id)

    events = await read_events(org_id=context.org_id, run_id=run_id)
    selected = next(e for e in events if e.type == "context_selected")
    assert selected.payload["as_of"] == datetime.now(UTC).date().isoformat()


# ---------------------------------------------------------------------------
# The critic, wired (WP9.1, architecture M9)
# ---------------------------------------------------------------------------


async def test_a_wrong_date_range_is_caught_without_asking_a_model(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """M9's acceptance line, end to end, and the half of it that costs nothing.

    The question names July; the statement filters June. Stage 1 blocks, and
    **no `critic` call is ever made** — paying a model to confirm arithmetic
    would be paying for a less reliable version of an answer already in hand.
    """
    fake_llm.script(
        _plan(
            "SELECT count(*) AS n FROM shops WHERE opened_on >= CAST('2026-06-01' AS DATE) "
            "AND opened_on < CAST('2026-07-01' AS DATE)"
        ),
        role="sql",
        times=1,
    )
    fake_llm.script(_reflect(), role="plan", times=1)
    fake_llm.script(_cites_the_execution, role="compose", times=1)
    # The second pass: the loop replans, composes again, and this time the
    # critic's LLM half is reached.
    fake_llm.script(
        _plan(
            "SELECT count(*) AS n FROM shops WHERE opened_on >= CAST('2026-07-01' AS DATE) "
            "AND opened_on < CAST('2026-08-01' AS DATE)"
        ),
        role="sql",
        times=1,
    )
    fake_llm.script(_reflect(), role="plan", times=1)
    fake_llm.script(_cites_the_execution, role="compose", times=1)
    fake_llm.script(CriticOut(verdict="pass", reasons=[]).model_dump_json(), role="critic", times=1)

    run_id = await _run_for(context, "How many shops opened in July 2026?")
    await execute_run(
        org_id=context.org_id,
        run_id=run_id,
        data_source_id=context.data_source_id or uuid.uuid4(),
        actor_user_id=context.actor_user_id,
        settings=build_settings(),
        as_of=date(2026, 8, 16),
    )

    events = await read_events(org_id=context.org_id, run_id=run_id)
    verdicts = [event for event in events if event.type == "critic_verdict"]
    assert len(verdicts) == 2, "one verdict per composed draft"

    first = verdicts[0].payload
    assert first["verdict"] == "revise"
    assert first["consulted_model"] is False, "arithmetic decided; no model was asked"
    rules = [finding["rule"] for finding in cast("list[dict[str, str]]", first["findings"])]
    assert rules == ["range_matches"]

    # And the proof that the saving is real: exactly one critic call, for the
    # second draft, not the first.
    assert len(fake_llm.calls_for("critic")) == 1
    assert len(fake_llm.calls_for("critic")) == 1


async def test_the_re_entry_happens_once_and_only_once(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """Architecture M9 allows one bounded re-entry. A critic that can keep
    sending a run back is a loop with no ceiling wearing a different name.

    Both drafts are rejected here — the second by the model half — and the run
    still finalizes rather than going round a third time.
    """
    for _ in range(2):
        fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql", times=1)
        fake_llm.script(_reflect(), role="plan", times=1)
        fake_llm.script(_cites_the_execution, role="compose", times=1)
        fake_llm.script(
            CriticOut(
                verdict="revise", reasons=["Still overstates the evidence."]
            ).model_dump_json(),
            role="critic",
        )

    run_id = await _run_for(context, "How many shops are there?")
    outcome = await execute_run(
        org_id=context.org_id,
        run_id=run_id,
        data_source_id=context.data_source_id or uuid.uuid4(),
        actor_user_id=context.actor_user_id,
        settings=build_settings(),
    )

    events = await read_events(org_id=context.org_id, run_id=run_id)
    assert len([e for e in events if e.type == "critic_verdict"]) == 2
    assert len(fake_llm.calls_for("critic")) == 2
    assert len(fake_llm.calls_for("compose")) == 2, "two drafts, not three"
    assert outcome.status == "completed", "a rejected second draft is still an ending"


async def test_the_second_attempt_is_told_what_was_wrong_with_the_first(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """The critic's reasons are given to the composer, not hoped for — the same
    shape the budget caveat already uses, and for the same reason."""
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql", times=1)
    fake_llm.script(_reflect(), role="plan", times=1)
    fake_llm.script(_cites_the_execution, role="compose", times=1)
    fake_llm.script(
        CriticOut(
            verdict="revise", reasons=["The answer treats a correlation as a cause."]
        ).model_dump_json(),
        role="critic",
    )
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql", times=1)
    fake_llm.script(_reflect(), role="plan", times=1)
    fake_llm.script(_cites_the_execution, role="compose", times=1)
    fake_llm.script(CriticOut(verdict="pass", reasons=[]).model_dump_json(), role="critic", times=1)

    run_id = await _run_for(context, "How many shops are there?")
    await execute_run(
        org_id=context.org_id,
        run_id=run_id,
        data_source_id=context.data_source_id or uuid.uuid4(),
        actor_user_id=context.actor_user_id,
        settings=build_settings(),
    )

    second = fake_llm.calls_for("compose")[1]
    prompt = " ".join(m.content for m in second.request.messages)
    assert "A reviewer rejected your previous answer" in prompt
    assert "treats a correlation as a cause" in prompt


async def test_a_run_that_passes_is_criticised_once_and_left_alone(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """The common path: one draft, one verdict, no second pass."""
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql", times=1)
    fake_llm.script(_reflect(), role="plan", times=1)
    fake_llm.script(_cites_the_execution, role="compose", times=1)
    fake_llm.script(CriticOut(verdict="pass", reasons=[]).model_dump_json(), role="critic", times=1)

    run_id = await _run_for(context, "How many shops are there?")
    outcome = await execute_run(
        org_id=context.org_id,
        run_id=run_id,
        data_source_id=context.data_source_id or uuid.uuid4(),
        actor_user_id=context.actor_user_id,
        settings=build_settings(),
    )

    events = await read_events(org_id=context.org_id, run_id=run_id)
    verdicts = [e for e in events if e.type == "critic_verdict"]
    assert len(verdicts) == 1
    assert verdicts[0].payload["verdict"] == "pass"
    assert verdicts[0].payload["consulted_model"] is True
    assert len(fake_llm.calls_for("compose")) == 1
    assert outcome.answered is True


async def test_the_verdict_schema_is_enforced(context: ToolContext, fake_llm: FakeLLM) -> None:
    """A verdict outside the three architecture 4.5 names is not a verdict.

    Scripted as a plausible-looking synonym, because that is what a real model
    produces when a schema is a suggestion rather than a constraint.
    """
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql", times=1)
    fake_llm.script(_reflect(), role="plan", times=1)
    fake_llm.script(_cites_the_execution, role="compose", times=1)
    fake_llm.script('{"verdict": "looks_fine", "reasons": []}', role="critic", times=1)

    run_id = await _run_for(context, "How many shops are there?")
    outcome = await execute_run(
        org_id=context.org_id,
        run_id=run_id,
        data_source_id=context.data_source_id or uuid.uuid4(),
        actor_user_id=context.actor_user_id,
        settings=build_settings(),
    )

    # The run does not silently accept it. It fails as a platform error rather
    # than passing an unvalidated verdict off as a review.
    assert outcome.status == "failed"
