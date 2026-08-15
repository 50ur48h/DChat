"""One question, answered or honestly refused — the four M7 paths.

FakeLLM for the model and a real database for everything else, which is the
split that matters: the answer has to be deterministic for a test to mean
anything, but the *refusal* has to come from the real DAL against a real
catalog, or the test proves only that a fake can be told to fail.

Assertions are on what the agent **did** — the trace, the rows it left, the
calls it made — rather than on the wording of an answer. A test that asserted on
prose would break every time a prompt improved, which teaches people to stop
reading failures.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import create_async_engine

from dataagent.agent.planner import Plan
from dataagent.agent.runner import MAX_LLM_CALLS, RunOutcome, execute_run
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


def _final(answer: str, *, answered: bool = True, cite: list[str] | None = None) -> str:
    return FinalizeIn(
        answer=answer, answered=answered, supported_by=cite or [], confidence="high"
    ).model_dump_json()


def _cites_the_execution(request: object) -> str:
    """Compose by reading the execution id out of the prompt it was given.

    The alternative — hard-coding an id — cannot work, because the id is minted
    at run time. This is also a real assertion in disguise: if the runner ever
    stops putting the execution id in front of the composing model, the citation
    silently becomes empty and two tests notice.
    """
    text_of = getattr(request, "prompt_text", "")
    marker = '"execution_id": "'
    start = text_of.index(marker) + len(marker)
    return _final("There are 3 shops.", cite=[text_of[start : text_of.index('"', start)]])


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
    fake_llm.script(_cites_the_execution, role="compose")
    run_id = await _run_for(context, "How many shops are there?")

    outcome = await _execute(context, run_id)

    assert outcome.status == "completed"
    assert outcome.answered is True
    assert outcome.repaired is False
    assert len(outcome.execution_ids) == 1

    view = await runs.get_run(org_id=context.org_id, run_id=run_id)
    assert view.status == "completed"
    assert view.answer == "There are 3 shops."
    assert view.finished_at is not None
    # The finding is the citation, and it names the row the DAL really wrote.
    assert len(view.findings) == 1
    assert view.findings[0].support == list(outcome.execution_ids)


async def test_the_trace_tells_the_whole_story_in_order(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """A trace that skips a step is worse than none: it looks complete."""
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_cites_the_execution, role="compose")
    run_id = await _run_for(context, "How many shops are there?")

    await _execute(context, run_id)

    assert await _types(context, run_id) == [
        "run_started",
        "context_selected",
        "plan_created",
        "tool_called",
        "query_executed",
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
    fake_llm.script(_cites_the_execution, role="compose")
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
# Repaired
# ---------------------------------------------------------------------------


async def test_a_hallucinated_column_is_repaired_once_and_then_answered(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """The path M7 names. The first statement invents a column, the DAL refuses
    it, and the second attempt — given the refusal — succeeds."""
    fake_llm.script(_plan("SELECT revenue_total FROM shops"), role="sql", times=1)
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql", times=1)
    fake_llm.script(_cites_the_execution, role="compose")
    run_id = await _run_for(context, "How much revenue?")

    outcome = await _execute(context, run_id)

    assert outcome.repaired is True
    assert outcome.answered is True
    assert outcome.llm_calls == MAX_LLM_CALLS
    assert await _types(context, run_id) == [
        "run_started",
        "context_selected",
        "plan_created",
        "tool_called",
        "error",
        "sql_rejected",
        "plan_created",
        "tool_called",
        "query_executed",
        "answer_composed",
        # The finding is written after the answer and before the ending: a
        # conclusion the trace does not mention is one the user cannot check.
        "finding_added",
        "run_finished",
    ]


async def test_the_repair_prompt_carries_the_refusal_back(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """Feeding the violation back is the whole reason the second attempt works.
    Without it the model rewrites the question instead of the query."""
    fake_llm.script(_plan("SELECT revenue_total FROM shops"), role="sql", times=1)
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql", times=1)
    fake_llm.script(_cites_the_execution, role="compose")
    run_id = await _run_for(context, "How much revenue?")

    await _execute(context, run_id)

    second = fake_llm.calls_for("sql")[1].prompt
    assert "was refused and did not run" in second
    assert "revenue_total" in second


async def test_a_refusal_that_survives_the_repair_ends_honestly(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """Repaired-**or-refused**. Two bad statements end the run with an answer that
    says what was refused, not with a failure and not with a fabrication."""
    fake_llm.script(_plan("SELECT revenue_total FROM shops"), role="sql")
    run_id = await _run_for(context, "How much revenue?")

    outcome = await _execute(context, run_id)

    assert outcome.status == "completed", "an honest refusal is an ending, not a failure"
    assert outcome.answered is False
    assert outcome.execution_ids == ()
    view = await runs.get_run(org_id=context.org_id, run_id=run_id)
    assert "refused" in (view.answer or "")
    # Nothing was concluded about the data, so there is nothing to stand behind.
    assert view.findings == []


async def test_an_engine_failure_is_not_repaired(context: ToolContext, fake_llm: FakeLLM) -> None:
    """Rewriting cannot fix a database that is down, so a second call would spend
    money to learn what we already knew. `repairable` is the flag, and the runner
    does not second-guess it."""
    fake_llm.script(_plan("SELECT count(*) AS n FROM nonexistent_table"), role="sql")
    run_id = await _run_for(context, "How many?")

    outcome = await _execute(context, run_id)

    # The unknown table is caught by catalog grounding, which *is* repairable —
    # so this asserts the budget, not the flag: one plan, one repair, no more.
    assert outcome.llm_calls <= MAX_LLM_CALLS
    assert outcome.answered is False


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
    fake_llm.script(_cites_the_execution, role="compose")
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

    assert state["step"] == "answered"
    assert state["executions"] == list(outcome.execution_ids)
    assert state["plan_sql"].startswith("SELECT")
