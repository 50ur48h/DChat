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
import logging
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


def _final(answer: str, *, unanswered: str = "", cite: list[str] | None = None) -> str:
    return FinalizeIn(
        answer=answer, unanswered=unanswered, supported_by=cite or [], confidence="high"
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
    assert outcome.state == "answered"
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
    # **B-100.** Architecture 4.2's fourth part of an answer. Asserted on the
    # *view* rather than on the composer, because the composer has built this
    # sentence correctly since Phase 9 and the run threw it away — the defect was
    # never in the words, it was that nothing carried them this far.
    assert view.method.startswith("1 query over one step"), view.method
    # **The control for B-133.** Without it `answered is False` on the refusal
    # test passes against a column nothing ever sets to True.
    assert view.state == "answered"


async def test_the_composer_is_told_to_write_something_a_person_can_read(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """**B-172, and the half that is easy to get wrong.**

    The instruction is prompt text, not enforcement — a model can ignore it and
    nothing here will stop it. What this asserts is the one thing that *is*
    ours: that the rule reaches the model at all. A constant nothing sends is
    the defect this repository files most, and `READABLE_ANSWER_RULE` has
    exactly the shape it comes in — a module-level string, referenced once.

    It also has to reach the **composer** and no one else. The planner choosing
    SQL has no business being told to use bullet points, and a rule that leaked
    into every role would be spending tokens on all of them.
    """
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_cites_the_execution, role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "How many shops are there?")

    await _execute(context, run_id)

    composing = " ".join(fake_llm.prompts(role="compose"))
    assert composing, "no composing call was made, so this proves nothing"
    assert "Write the answer as Markdown" in composing
    assert "Never run a ranked list through a sentence with commas" in composing
    assert "Use everyday words and short sentences" in composing
    # Simple is not vague: the rule must not read as licence to drop numbers.
    assert "keep every number, every name and every limit" in composing

    planning = " ".join(fake_llm.prompts(role="sql")) + " ".join(fake_llm.prompts(role="plan"))
    assert "Write the answer as Markdown" not in planning, (
        "the formatting rule belongs to the composer; a planner told to use "
        "bullet points is paying for advice about a job it does not do"
    )


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

    assert outcome.state == "answered"
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
    fake_llm.script(_final("I could not answer that.", unanswered="all of it"), role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "How much revenue?")

    outcome = await _execute(context, run_id)

    assert outcome.status == "completed", "an honest refusal is an ending, not a failure"
    assert outcome.state in {"refused", "partly"}
    assert outcome.execution_ids == ()
    view = await runs.get_run(org_id=context.org_id, run_id=run_id)
    # Nothing was concluded about the data, so there is nothing to stand behind.
    assert view.findings == []
    # **And the run says so where a screen can read it** (**B-133**). This was the
    # gap: the outcome already knew this above, and stopped there — it went
    # into the `run_finished` event's totals and onto no column, so `RunView` could
    # not carry it and the card rendered `completed` as the word "answered". The
    # assertion is on the *view* rather than the outcome for exactly that reason.
    assert view.state in {"refused", "partly"}, (
        "a refusal that the API reports as indistinguishable from an answer is a "
        "refusal the screen will label 'answered'"
    )


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
    assert outcome.state in {"refused", "partly"}
    assert outcome.iterations == 1, "it stopped rather than trying again"
    # One plan call and nothing else: no reflection, no compose. There is nothing
    # to reflect on and nothing to compose from.
    assert outcome.llm_calls == 1


async def test_a_run_whose_every_query_failed_refuses_instead_of_describing_the_absence(
    context: ToolContext, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**B-095**, and the owner's decision (**D-038**).

    The run above stops the *loop*; this one is about what the runner then does
    with it. A failed execution is still an execution, so the "nothing ran" test
    did not catch it and the run went on to compose — handing a model a list of
    refusals, no results, and an instruction to answer. A model given no
    evidence does not decline; it describes the absence as a finding, and the
    live run read *"no data was returned from the queries"*: a claim about the
    customer's data when the truth was about the platform.

    The assertions that carry the fix are the **status** and the **call count**.
    No `compose` is scripted here, so a run that composes raises inside the
    FakeLLM and ends `failed` — which is exactly how this path behaved before,
    invisibly, because the older test asserted `answered` and never `status`.
    """
    from dataagent.agent.tools import registry as registry_module

    async def unreachable(self: object, *args: object, **kwargs: object) -> object:
        from dataagent.agent.tools.base import ToolResult

        return ToolResult(
            tool="run_sql",
            ok=False,
            error="could not connect to the data source",
            code="engine_error",
            repairable=False,
        )

    monkeypatch.setattr(registry_module.ToolRegistry, "call", unreachable)
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    run_id = await _run_for(context, "How many shops are there?")

    outcome = await _execute(context, run_id)

    assert outcome.status == "completed", "an honest refusal is an ending, not a failure"
    assert outcome.state in {"refused", "partly"}, (
        "nothing reached the database, so nothing was answered"
    )
    assert outcome.llm_calls == 1, "no composing call: there was nothing to compose from"

    view = await runs.get_run(org_id=context.org_id, run_id=run_id)
    assert view.answer is not None
    assert "could not connect to the data source" in view.answer, (
        "the reader is told what actually failed, in the connector's own words"
    )
    assert any("failed to run" in note for note in view.limitations), (
        "the run knew every query had failed; before B-095 it said nothing"
    )
    assert view.findings == [], "there is no evidence, so there is nothing to stand behind"


# ---------------------------------------------------------------------------
# Refused before anything ran
# ---------------------------------------------------------------------------


async def test_a_question_the_catalog_cannot_answer_refuses_without_querying(
    context: ToolContext, fake_llm: FakeLLM, wired: URL
) -> None:
    """`answerable=false` is believed **on the second telling**, and it still costs
    no query and no composing call.

    **D-055 changed the first half of this test's premise and not the second.**
    A model-judgement refusal now gets one more look from a standing start, so
    the planner is asked twice — the cost the owner accepted explicitly on
    2026-08-27 — and a model that refuses twice is believed. What has not changed
    is the thing this test is actually about: no statement reaches the DAL and
    the composer is never called.

    `llm_calls` used to carry that claim as `== 1`, which was a **proxy** — it
    conflated *how many planner calls* with *whether a composing call happened*,
    and the retry pulled those apart. Asserted separately now, so the next change
    to either one cannot quietly satisfy the other.
    """
    fake_llm.script(
        _plan("SELECT 1", answerable=False, reason="There is no revenue column anywhere."),
        role="sql",
    )
    run_id = await _run_for(context, "What was our profit margin?")

    outcome = await _execute(context, run_id)

    assert outcome.state in {"refused", "partly"}
    assert outcome.status == "completed"
    # Two planner calls: the judgement and its one permitted second look (D-055).
    assert outcome.llm_calls == 2
    assert not fake_llm.prompts(role="compose"), "a refusal paid for a composing call"
    assert outcome.iterations == 2
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
    # **B-173, on the live path.** `rows_shown` is what lets the platform state
    # a truncation in `limitations` instead of leaving the model to narrate it
    # in the prose. A field the loop never fills is the defect this repository
    # files most, and the composer tests hand it in directly — only this one
    # proves the loop writes it.
    assert state["executions"][0]["rows_shown"] is not None
    assert state["executions"][0]["rows_shown"] <= state["executions"][0]["row_count"]
    assert state["plan"][0]["sql"].startswith("SELECT")
    # Rows are never in the checkpoint — only a summary and the reference (4.4).
    #
    # Asserted on the *keys* rather than on the serialized text. The substring
    # form was the original, and it failed the moment an execution gained a
    # `rows_shown` count — flagging the word while the property it guards, that
    # no customer value is in the checkpoint, was never in question. A count of
    # rows is not a row.
    executions = cast(list[dict[str, object]], state["executions"])
    assert all("rows" not in execution for execution in executions)
    for execution in executions:
        for value in execution.values():
            if not isinstance(value, list):
                continue
            nested = cast(list[object], value)
            assert not any(isinstance(item, list) for item in nested), (
                f"a checkpointed execution is carrying something row-shaped: {execution}"
            )

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
    assert outcome.state == "answered"


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


# ---------------------------------------------------------------------------
# The failure an operator has to be able to read (B-126)
# ---------------------------------------------------------------------------


async def test_a_deployment_with_no_model_configuration_fails_every_run(
    context: ToolContext, fake_llm: FakeLLM, caplog: pytest.LogCaptureFixture
) -> None:
    """**The configuration that shipped to dev, driven the way the product does.**

    `apps.bicep` set `OPENAI_API_KEY` and none of the `LLM_*` variables, so the
    deployed API had a credential and an empty `llm_models`. Every question asked
    in the browser ended `failed` with *"The run could not be completed."* — and
    the API logs held no error at all, so there was no way to tell a broken
    platform from a bad question without the platform DSN and a SQL client.

    This drives `execute_run` with exactly that configuration rather than calling
    `registry.resolve` directly, because the defect was never in `resolve` — it
    raised precisely as designed. What was missing was anything that carried its
    sentence to somebody who could act on it.
    """
    run_id = await _run_for(context, "How many shops?")

    with caplog.at_level(logging.ERROR, logger="dataagent.agent.runner"):
        outcome = await execute_run(
            org_id=context.org_id,
            run_id=run_id,
            data_source_id=context.data_source_id or uuid.uuid4(),
            actor_user_id=context.actor_user_id,
            settings=build_settings(llm_models={}),
        )

    assert outcome.status == "failed"

    # The operator's half: the log names the run and the variable to set.
    assert str(run_id) in caplog.text
    assert "LLM_MODELS" in caplog.text

    # The stored half, unchanged: the trace still carries the reason, and the
    # person asking still gets the generic message rather than a provider error.
    events = await read_events(org_id=context.org_id, run_id=run_id)
    errors = [event for event in events if event.type == "error"]
    assert errors, "a failed run recorded no error event"
    assert "LLM_MODELS" in json.dumps(errors[-1].payload)

    view = await runs.get_run(org_id=context.org_id, run_id=run_id)
    assert view.status == "failed"
    assert view.finished_at is not None


async def test_an_internal_failure_is_logged_too_and_not_only_the_llm_kind(
    context: ToolContext,
    fake_llm: FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The other call site. Two `except` blocks record a failure and both had to
    learn to say so — a fix applied to one of them would leave the silent case
    that is hardest to diagnose, since an internal error has no provider to
    blame."""
    from dataagent.agent import runner as runner_module

    async def explode(**kwargs: object) -> object:
        raise RuntimeError("something nobody predicted")

    monkeypatch.setattr(runner_module, "research", explode)
    run_id = await _run_for(context, "How many shops?")

    with caplog.at_level(logging.ERROR, logger="dataagent.agent.runner"):
        outcome = await _execute(context, run_id)

    assert outcome.status == "failed"
    assert str(run_id) in caplog.text
    assert "something nobody predicted" in caplog.text


async def test_a_run_that_succeeds_logs_no_error(
    context: ToolContext, fake_llm: FakeLLM, caplog: pytest.LogCaptureFixture
) -> None:
    """**The control.** Without it both tests above are satisfied by a runner that
    logs an error on every run, which would be its own kind of unreadable log."""
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_cites_the_execution, role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "How many shops are there?")

    with caplog.at_level(logging.ERROR, logger="dataagent.agent.runner"):
        outcome = await _execute(context, run_id)

    assert outcome.status == "completed"
    assert caplog.text == ""


# ---------------------------------------------------------------------------
# What periods this database can speak about (B-157, D-058)
# ---------------------------------------------------------------------------


async def _payload_of(context: ToolContext, run_id: uuid.UUID, kind: str) -> dict[str, object]:
    events = await read_events(org_id=context.org_id, run_id=run_id)
    return next(event.payload for event in events if event.type == kind)


async def test_the_measured_period_reaches_the_model_that_writes_the_sql(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """**Reached, not merely computed** (B-157).

    B-157's refusal declared three months of 2025 missing while `dim_calendar`
    held every one of them. The capability note is not enforcement — the
    composer's limitation is — but it is the half that stops the wrong sentence
    being written at all, and it is worth nothing unless the planner is actually
    shown it. So the assertion is on the **prompt**, not on the bundle: if the
    note ever stops being rendered, this goes red rather than staying green over
    a string nobody reads.

    The fixture's `shops.opened_on` runs 2020-01-01 to 2024-05-05, and the check
    reports months rather than days because the two sides it compares are not
    always the same precision.
    """
    from dataagent.catalog import profiler

    await profiler.profile(
        org_id=context.org_id,
        actor_user_id=context.actor_user_id,
        data_source_id=context.data_source_id or uuid.uuid4(),
    )
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_cites_the_execution, role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "How many shops are there?")

    await _execute(context, run_id)

    payload = await _payload_of(context, run_id, "capability_checked")
    assert payload["available_period"] == "2020-01 to 2024-05"

    planning = " ".join(fake_llm.prompts(role="sql"))
    assert "2020-01 to 2024-05" in planning, (
        "the measured period was computed and never shown to the model that "
        "decides whether a period is missing"
    )
    assert "not a limit on what you may ask" in planning


async def test_an_unprofiled_source_abstains_where_a_reader_can_see_it(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """The owner's requirement: a run where the check could not fire must be
    distinguishable from one where it fired and passed.

    The fixture discovers without profiling, which is the ordinary state of a
    source nobody has profiled yet — so there is no measured range, the note is
    not written, and the trace carries `available_period: null` **as a key that is
    present**. An absent key would make this run and a covered one look identical
    to anything reading the trace.
    """
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_cites_the_execution, role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "How many shops are there?")

    await _execute(context, run_id)

    payload = await _payload_of(context, run_id, "capability_checked")
    assert "available_period" in payload, "the trace cannot tell an abstention from a pass"
    assert payload["available_period"] is None

    planning = " ".join(fake_llm.prompts(role="sql"))
    assert "not a limit on what you may ask" not in planning, (
        "a range was stated for a source whose columns nobody measured"
    )


async def test_the_answer_records_which_period_it_was_about(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """The second half of the check, asserted on what the trace actually carries.

    `answer_composed` carries `coverage` **whatever it says**, including that it
    could not look. The fixture's answer counts shops and returns no period at
    all, so this run abstains — and the assertion is that the abstention arrives
    with a reason rather than as an absent key, because an absent key would make
    this run and a checked one indistinguishable to anything reading the trace.
    """
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_cites_the_execution, role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "How many shops are there?")

    await _execute(context, run_id)

    payload = await _payload_of(context, run_id, "answer_composed")
    assert "coverage" in payload, "the trace cannot say whether the period was checked"
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)
    assert coverage["status"] == "abstained"
    assert coverage["reason"], "an abstention with no reason is a silence with a label on it"


async def test_an_answer_whose_rows_are_a_period_is_compared_against_the_catalogue(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """**B-157's shape, end to end.**

    `shops.opened_on` runs 2020-01-01 to 2024-05-05, so a profiled source records
    that period — and an answer that selects those dates covers exactly it. The
    verdict is `contained`, which is the quiet outcome and the one that must stay
    quiet: a caveat here would be the padding that teaches people to skip
    caveats.

    Asserted on the trace payload rather than on the `Coverage` object, because
    the object is a value in flight and the payload is what a person receives
    (B-133's rule).
    """
    from dataagent.catalog import profiler

    await profiler.profile(
        org_id=context.org_id,
        actor_user_id=context.actor_user_id,
        data_source_id=context.data_source_id or uuid.uuid4(),
    )
    fake_llm.script(_plan("SELECT opened_on FROM shops"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_cites_the_execution, role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "When did the shops open?")

    outcome = await _execute(context, run_id)

    payload = await _payload_of(context, run_id, "answer_composed")
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)
    assert coverage["status"] == "contained", coverage
    assert coverage["answered"] == "2020-01 to 2024-05"
    assert coverage["available"] == "2020-01 to 2024-05"
    assert not any("outside the period" in note for note in outcome.limitations), (
        "an answer inside the catalogue's range was caveated anyway"
    )


async def test_sql_the_engine_rejects_is_corrected_rather_than_giving_up(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """**The class of failure that ended a live run after one query.**

    On the MiseQ source a run died on `function round(double precision, integer)
    does not exist`, an error whose own HINT reads *"You might need to add
    explicit type casts."* Every `ConnectorError` was reported to the loop as
    unfixable by rewriting, so the one thing that would have worked was the one
    thing it was told not to do.

    The statement here is a different member of the same class — comparing a
    date to a number, which the catalog check passes because both identifiers
    exist and the engine then rejects on types. That substitution is deliberate:
    the *exact* `round` failure is asserted in
    `tests/connectors/test_postgres_connector.py`, against a real engine and
    without the validator in the way, because **the validator rewrites some
    `round` shapes and not others** — `ROUND(CAST(x AS DOUBLE PRECISION), 2)` it
    fixes, `ROUND(SUM(money_column), 2)` it cannot, having no column types to
    reason from. Reproducing that here would test sqlglot's inference rather
    than the loop's decision.

    What this asserts is the loop's decision: given an error the engine
    explained, does the run try again or give up?
    """
    # `times=1`, or the first script matches every call and the loop re-proposes
    # the same statement — which its own duplicate-query rule then skips, and
    # the test would be measuring that instead of the repair.
    fake_llm.script(
        _plan("SELECT count(*) AS n FROM shops WHERE opened_on > 2021"), role="sql", times=1
    )
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    # The loop reflects after a refusal too, so the first reflection must not
    # end the run — otherwise this would be testing  rather than the
    # repair.
    fake_llm.script(_reflect(done=False), role="plan", times=1)
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_cites_the_execution, role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "How many shops are there?")

    outcome = await _execute(context, run_id)

    types = await _types(context, run_id)
    assert types.count("sql_rejected") == 1, (
        f"the engine did not reject the first statement, so this test is not "
        f"exercising the repair path: {types}"
    )
    assert "query_executed" in types, "the corrected attempt never ran"
    assert outcome.state == "answered", (
        f"the run gave up on an error the engine explained how to fix: {outcome.answer}"
    )


# ---------------------------------------------------------------------------
# A model's judgement is not a platform fact (D-055)
# ---------------------------------------------------------------------------


async def test_a_judged_refusal_gets_one_more_attempt_before_it_stands(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """**The second screenshot's failure.**

    Asked *"can food waste be reduced to increase revenue?"* the deployed app
    refused with no query run — and the refusal's first clause, *"the reference
    data contains no food-waste records"*, was false: the turn before had just
    answered from `fact_waste`. `plan.answerable == false` ended the run on the
    spot, giving a model's opinion about a question the same finality as the join
    graph's verdict about a schema. Only one of those is a fact the model cannot
    argue with.

    One retry, from a standing start only. Asserted on the run's ending and on
    the trace carrying `retrying`, because a retry nobody can see in the trace is
    a cost with no account of itself.
    """
    fake_llm.script(
        _plan("SELECT 1", answerable=False, reason="no link between waste and revenue"),
        role="sql",
        times=1,
    )
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_cites_the_execution, role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "can waste be reduced to increase revenue?")

    outcome = await _execute(context, run_id)

    assert outcome.state == "answered", f"the judged refusal was still terminal: {outcome.answer}"
    events = await read_events(org_id=context.org_id, run_id=run_id)
    retried = [e for e in events if e.type == "plan_created" and e.payload.get("retrying")]
    assert len(retried) == 1, "the retry left no trace of itself"
    assert retried[0].payload.get("reason") == "no link between waste and revenue", (
        "the first refusal's reason was not recorded, so nothing can check that the "
        "retried answer still names the gap"
    )


async def test_the_second_attempt_is_asked_something_different(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """**The property the first version of this test could not see** (B-167).

    That test scripted the fake to refuse once and then succeed, and passed —
    proving the loop *continues* after a judged refusal. It said nothing about
    whether the retry *changes* anything, because the harness supplied from
    outside the very thing the product was failing to supply. It did not: the
    retry's precondition is that nothing has executed, `_progress_so_far` returns
    `""` under exactly that condition, and the second planner call received
    byte-identical input to the first.

    So this fake **refuses both times** and never changes its mind. What is
    asserted is a property of the product — that the second prompt differs from
    the first and carries the reason the model gave for refusing — which no
    script can fake on its behalf.
    """
    fake_llm.script(
        _plan("SELECT 1", answerable=False, reason="no causal model links waste to revenue"),
        role="sql",
    )
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "can waste be reduced to increase revenue?")

    outcome = await _execute(context, run_id)

    planning = fake_llm.prompts(role="sql")
    assert len(planning) == 2, f"the planner was asked {len(planning)} times, not twice"
    assert planning[0] != planning[1], (
        "the second attempt was given byte-identical input to the first, so the "
        "retry cannot change anything — B-167 exactly"
    )
    assert "no causal model links waste to revenue" in planning[1], (
        "the model's own reason for refusing was not carried into the second ask"
    )
    assert "nearest question" in planning[1], "the instruction that makes a retry worth it"
    # And a model that refuses twice is still believed: this is prompt text, not
    # enforcement, and D-044's derivation decides the ending.
    assert outcome.state == "refused"


async def test_the_retry_instruction_is_gone_once_a_query_has_run(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """It applies to the attempt that follows the refusal and does not trail
    through the rest of an investigation that got going."""
    fake_llm.script(
        _plan("SELECT 1", answerable=False, reason="cannot be established"), role="sql", times=1
    )
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql", times=1)
    fake_llm.script(_plan("SELECT count(*) AS n FROM regions"), role="sql")
    fake_llm.script(_reflect(done=False), role="plan", times=1)
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_cites_the_execution, role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "how many shops are there?")

    await _execute(context, run_id)

    planning = fake_llm.prompts(role="sql")
    assert len(planning) >= 3
    assert "nearest question" in planning[1], "the retry's own attempt lost the instruction"
    assert "nearest question" not in planning[2], (
        "the instruction trailed into an iteration that had already run a query"
    )


async def test_a_judged_refusal_is_retried_once_and_not_twice(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """The bound, and the reason it is on the state rather than in a local: an
    interrupted run that comes back must not quietly buy a second retry."""
    fake_llm.script(_plan("SELECT 1", answerable=False, reason="cannot be established"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_passes(), role="critic")
    run_id = await _run_for(context, "can waste be reduced to increase revenue?")

    outcome = await _execute(context, run_id)

    assert outcome.state == "refused", "a model that refuses twice must be believed"
    events = await read_events(org_id=context.org_id, run_id=run_id)
    assert len([e for e in events if e.type == "plan_created" and e.payload.get("retrying")]) == 1
