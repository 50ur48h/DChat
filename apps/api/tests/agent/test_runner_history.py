"""A second question in a thread is answered knowing the first (**D-029**, B-064).

The unit tests next door prove the prompt renders a thread correctly and the
runs suite proves the right turns are loaded. This file is the join between
them, and it exists because B-064 was never a wrong *value* anywhere — every
component did exactly what it was written to do, and the wiring between them
carried nothing. A test on either half alone would still be green today.

So the assertions here are on what actually reached a model, read out of the
FakeLLM's own prompt text, and on the trace, which is the only place a person
could see that a run had context at all.
"""

from __future__ import annotations

import uuid
from typing import cast

from dataagent.agent.context import HISTORY_FRAME
from dataagent.agent.critic import CriticOut
from dataagent.agent.loop import ReflectFinding, Reflection
from dataagent.agent.planner import Plan
from dataagent.agent.runner import execute_run
from dataagent.agent.tools.base import ToolContext
from dataagent.agent.tools.finalize import FinalizeIn
from dataagent.llm.base import Role
from dataagent.llm.fake import FakeLLM
from dataagent.runs import service as runs
from dataagent.runs.events import read_events
from llm_fixture import build_settings


def _plan(sql: str) -> str:
    return Plan(
        sql=sql, purpose="answer the question", answerable=True, reason=""
    ).model_dump_json()


def _reflect() -> str:
    return Reflection(
        findings=[ReflectFinding(statement="counted", supported_by=[], confidence="high")],
        open_questions=[],
        next_purpose="",
        done=True,
        rationale="that answers it",
    ).model_dump_json()


def _final(answer: str) -> str:
    return FinalizeIn(answer=answer, supported_by=[], confidence="high").model_dump_json()


def _passes() -> str:
    return CriticOut(verdict="pass", reasons=[]).model_dump_json()


async def _ask_and_answer(context: ToolContext, question: str, answer: str) -> uuid.UUID:
    """A completed earlier turn, made the way the product makes one."""
    view = await runs.get_run(org_id=context.org_id, run_id=context.run_id)
    asked = await runs.post_message(
        org_id=context.org_id,
        user_id=context.actor_user_id or uuid.uuid4(),
        conversation_id=view.conversation_id,
        content=question,
        idempotency_key=uuid.uuid4().hex,
    )
    await runs.transition(org_id=context.org_id, run_id=asked.run_id, status="running")
    await runs.record_answer(org_id=context.org_id, run_id=asked.run_id, content=answer)
    await runs.transition(org_id=context.org_id, run_id=asked.run_id, status="completed")
    return asked.run_id


async def _follow_up(context: ToolContext, question: str) -> uuid.UUID:
    view = await runs.get_run(org_id=context.org_id, run_id=context.run_id)
    asked = await runs.post_message(
        org_id=context.org_id,
        user_id=context.actor_user_id or uuid.uuid4(),
        conversation_id=view.conversation_id,
        content=question,
        idempotency_key=uuid.uuid4().hex,
    )
    return asked.run_id


async def _execute(context: ToolContext, run_id: uuid.UUID) -> None:
    await execute_run(
        org_id=context.org_id,
        run_id=run_id,
        data_source_id=context.data_source_id or uuid.uuid4(),
        actor_user_id=context.actor_user_id,
        settings=build_settings(),
    )


def _prompts(fake_llm: FakeLLM, role: str) -> tuple[str, ...]:
    """Exactly the text a real provider would have received, for the one role."""
    return fake_llm.prompts(cast(Role, role))


async def test_the_planner_is_shown_the_question_this_one_follows(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """The defect exactly as the owner hit it: a question, then *"check again"*,
    answered as though nothing had been asked."""
    await _ask_and_answer(context, "How many shops are there?", "There are 3 shops.")
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_final("Still 3 shops."), role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _follow_up(context, "check again")

    await _execute(context, run_id)

    planning = _prompts(fake_llm, "sql")[0]
    assert "How many shops are there?" in planning
    assert "There are 3 shops." in planning
    assert HISTORY_FRAME in planning


async def test_the_critic_judges_a_follow_up_in_its_thread(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """A critic asked whether a draft answers *"check again"*, with no idea what
    was being checked, will say it does not — and blocking a correct answer is
    this component's characteristic failure (standing note 5)."""
    await _ask_and_answer(context, "How many shops are there?", "There are 3 shops.")
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_final("Still 3 shops."), role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _follow_up(context, "check again")

    await _execute(context, run_id)

    assert "How many shops are there?" in _prompts(fake_llm, "critic")[0]


async def test_the_reflection_is_shown_the_thread_too(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """ "Is the question now answered" cannot be judged from *"check again"*
    alone."""
    await _ask_and_answer(context, "How many shops are there?", "There are 3 shops.")
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_final("Still 3 shops."), role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _follow_up(context, "check again")

    await _execute(context, run_id)

    assert "How many shops are there?" in _prompts(fake_llm, "plan")[0]


async def test_the_composer_writes_its_answer_knowing_the_thread(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """The call that produces the words a person reads. Without the thread it
    answers *"check again"* rather than the thing being checked again — and
    every prompt in this run that carries the question now carries the thread
    with it, so there is no fourth place for one to be missing."""
    await _ask_and_answer(context, "How many shops are there?", "There are 3 shops.")
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_final("Still 3 shops."), role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _follow_up(context, "check again")

    await _execute(context, run_id)

    assert "How many shops are there?" in _prompts(fake_llm, "compose")[0]


async def test_the_trace_records_how_much_context_the_run_was_given(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """A follow-up answered from a thread is a different act from one answered
    cold, and nothing else in the record would say which happened."""
    await _ask_and_answer(context, "How many shops are there?", "There are 3 shops.")
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_final("Still 3 shops."), role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _follow_up(context, "check again")

    await _execute(context, run_id)

    events = await read_events(org_id=context.org_id, run_id=run_id)
    selected = next(event for event in events if event.type == "context_selected")
    assert int(str(selected.payload["history_turns"])) >= 1


async def test_a_follow_up_with_no_nouns_still_finds_the_tables(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """*"check again"* names no table, so searching it alone returns nothing and
    the planner is asked to write SQL against an empty catalog — which is
    **B-041**, the defect that cost the M7 gate. The thread is the fallback, and
    the trace says when it was used."""
    await _ask_and_answer(context, "How many shops are there?", "There are 3 shops.")
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_final("Still 3 shops."), role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _follow_up(context, "check again")

    await _execute(context, run_id)

    events = await read_events(org_id=context.org_id, run_id=run_id)
    selected = next(event for event in events if event.type == "context_selected")
    assert selected.payload["tables"], "a follow-up must not plan against an empty catalog"
    assert selected.payload["tables_found_via"] == "thread"


async def test_a_question_with_words_of_its_own_is_not_pulled_back_to_the_last_one(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """The other half of the fallback, and the one that keeps it honest: the
    strict search keeps every promise it made, and the thread is consulted only
    when it matched nothing."""
    await _ask_and_answer(context, "How many shops are there?", "There are 3 shops.")
    fake_llm.script(_plan("SELECT count(*) AS n FROM shops"), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_final("Counted."), role="compose")
    fake_llm.script(_passes(), role="critic")
    run_id = await _follow_up(context, "how many shops are there now?")

    await _execute(context, run_id)

    events = await read_events(org_id=context.org_id, run_id=run_id)
    selected = next(event for event in events if event.type == "context_selected")
    assert selected.payload["tables_found_via"] == "question"
