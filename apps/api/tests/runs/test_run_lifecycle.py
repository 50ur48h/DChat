"""A question, the run it starts, and every way that run can end.

The state machine is the part of WP7.1 that has to be right before there is
anything driving it, because WP7.2's planner will be written against it and
Phase 8's loop against that. Two properties carry most of the weight:

* **a finished run never moves again** — so a late tool result cannot resurrect
  one and quietly change an answer somebody has already read;
* **every status change leaves a trace event** — a run whose status changed
  without the trace saying so is precisely the gap the trace exists to close.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from conftest import Tenant
from dataagent.runs import service
from dataagent.runs.events import read_events


async def _conversation(tenant: Tenant, title: str | None = None) -> uuid.UUID:
    view = await service.create_conversation(
        org_id=tenant.org_id, user_id=tenant.user_id, title=title
    )
    return view.id


async def _ask(tenant: Tenant, question: str = "How many orders in July?") -> service.AskResult:
    conversation_id = await _conversation(tenant)
    return await service.post_message(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        conversation_id=conversation_id,
        content=question,
        idempotency_key=uuid.uuid4().hex,
    )


async def _send(
    tenant: Tenant,
    conversation_id: uuid.UUID,
    *,
    key: str,
    content: str = "How many orders in July?",
) -> service.AskResult:
    return await service.post_message(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        conversation_id=conversation_id,
        content=content,
        idempotency_key=key,
    )


async def _types(tenant: Tenant, run_id: uuid.UUID) -> list[str]:
    return [event.type for event in await read_events(org_id=tenant.org_id, run_id=run_id)]


# ---------------------------------------------------------------------------
# Asking
# ---------------------------------------------------------------------------


async def test_a_question_creates_a_queued_run_and_the_message_that_asked_it(
    tenant: Tenant,
) -> None:
    conversation_id = await _conversation(tenant)

    result = await service.post_message(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        conversation_id=conversation_id,
        content="How many orders were placed in July 2026?",
        idempotency_key="send-1",
    )

    assert result.created is True
    run = await service.get_run(org_id=tenant.org_id, run_id=result.run_id)
    assert run.status == "queued"
    assert run.question == "How many orders were placed in July 2026?"
    assert run.started_at is None and run.finished_at is None
    # Nothing has run, so the trace is empty rather than optimistic.
    assert await _types(tenant, result.run_id) == []

    messages = await service.list_messages(
        org_id=tenant.org_id, user_id=tenant.user_id, conversation_id=conversation_id
    )
    assert [(m.role, m.run_id) for m in messages] == [("user", result.run_id)]


async def test_an_untitled_conversation_takes_its_title_from_the_first_question(
    tenant: Tenant,
) -> None:
    conversation_id = await _conversation(tenant)

    await service.post_message(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        conversation_id=conversation_id,
        content="  Which\nstore  sold most?  ",
        idempotency_key="send-1",
    )

    view = await service.get_conversation(
        org_id=tenant.org_id, user_id=tenant.user_id, conversation_id=conversation_id
    )
    # Whitespace collapsed: the title is a line in a sidebar, not a transcript.
    assert view.title == "Which store sold most?"


async def test_a_title_the_client_set_is_never_overwritten(tenant: Tenant) -> None:
    conversation_id = await _conversation(tenant, title="July review")

    await service.post_message(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        conversation_id=conversation_id,
        content="How many orders?",
        idempotency_key="send-1",
    )

    view = await service.get_conversation(
        org_id=tenant.org_id, user_id=tenant.user_id, conversation_id=conversation_id
    )
    assert view.title == "July review"


async def test_a_long_question_is_truncated_rather_than_stored_whole_as_a_title(
    tenant: Tenant,
) -> None:
    conversation_id = await _conversation(tenant)
    question = "why " * 200

    await service.post_message(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        conversation_id=conversation_id,
        content=question,
        idempotency_key="send-1",
    )

    view = await service.get_conversation(
        org_id=tenant.org_id, user_id=tenant.user_id, conversation_id=conversation_id
    )
    assert view.title is not None
    assert len(view.title) == service.TITLE_LENGTH
    assert view.title.endswith("…")


async def test_a_repeated_send_returns_the_same_run_and_starts_no_second_one(
    tenant: Tenant,
) -> None:
    """The property that makes a double-tapped send button free rather than billed."""
    conversation_id = await _conversation(tenant)

    first = await _send(tenant, conversation_id, key="the-same-send")
    second = await _send(tenant, conversation_id, key="the-same-send")

    assert first.created is True
    assert second.created is False
    assert second.run_id == first.run_id
    assert second.message_id == first.message_id

    messages = await service.list_messages(
        org_id=tenant.org_id, user_id=tenant.user_id, conversation_id=conversation_id
    )
    assert len(messages) == 1, "a retry wrote a second copy of the same question"


async def test_two_simultaneous_sends_of_one_question_still_produce_one_run(
    tenant: Tenant,
) -> None:
    """The half of idempotency a read-then-write cannot cover on its own.

    A double tap is two requests in flight at once, so both look up the key,
    both find nothing, and both insert. The unique index refuses the second, and
    what the caller gets back is the run the first one started — not an error,
    because from the client's side this was one send.
    """
    conversation_id = await _conversation(tenant)

    first, second = await asyncio.gather(
        _send(tenant, conversation_id, key="double-tap"),
        _send(tenant, conversation_id, key="double-tap"),
    )

    assert first.run_id == second.run_id
    assert {first.created, second.created} == {True, False}, (
        "both sends claimed to have created the run"
    )
    messages = await service.list_messages(
        org_id=tenant.org_id, user_id=tenant.user_id, conversation_id=conversation_id
    )
    assert len(messages) == 1


async def test_a_different_key_is_a_different_question(tenant: Tenant) -> None:
    conversation_id = await _conversation(tenant)

    first = await service.post_message(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        conversation_id=conversation_id,
        content="How many orders in July?",
        idempotency_key="send-1",
    )
    second = await service.post_message(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        conversation_id=conversation_id,
        content="How many orders in July?",
        idempotency_key="send-2",
    )

    assert second.run_id != first.run_id


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------


async def test_starting_a_run_stamps_the_time_and_says_so_in_the_trace(tenant: Tenant) -> None:
    asked = await _ask(tenant)

    run = await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status="running")

    assert run.status == "running"
    assert run.started_at is not None
    assert run.finished_at is None
    assert await _types(tenant, asked.run_id) == ["run_started"]


async def test_completing_a_run_stamps_a_finish_time_and_closes_the_trace(tenant: Tenant) -> None:
    asked = await _ask(tenant)
    await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status="running")

    run = await service.transition(
        org_id=tenant.org_id,
        run_id=asked.run_id,
        status="completed",
        totals={"queries": 1, "llm_calls": 2},
    )

    assert run.finished_at is not None
    events = await read_events(org_id=tenant.org_id, run_id=asked.run_id)
    assert [event.type for event in events] == ["run_started", "run_finished"]
    assert events[-1].payload == {"status": "completed", "totals": {"queries": 1, "llm_calls": 2}}


async def test_a_finished_run_can_never_move_again(tenant: Tenant) -> None:
    """The property that stops a late result from rewriting an answer already read."""
    asked = await _ask(tenant)
    await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status="running")
    await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status="completed")

    for status in ("running", "failed", "completed", "validating"):
        with pytest.raises(service.InvalidTransitionError, match="has finished"):
            await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status=status)


async def test_a_queued_run_cannot_jump_straight_to_completed(tenant: Tenant) -> None:
    asked = await _ask(tenant)

    with pytest.raises(service.InvalidTransitionError) as raised:
        await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status="completed")

    # The message names what *would* work, because the caller is code being
    # written against this machine.
    assert "running" in str(raised.value)


async def test_a_run_can_fail_before_it_ever_starts(tenant: Tenant) -> None:
    """No reachable data source, no budget left: a run that cannot fail from its
    first state is a run that hangs in ``queued`` forever."""
    asked = await _ask(tenant)

    run = await service.transition(
        org_id=tenant.org_id,
        run_id=asked.run_id,
        status="failed",
        failure_reason="No data source is registered",
    )

    assert run.status == "failed"
    assert run.started_at is None
    assert run.finished_at is not None
    assert run.failure_reason == "No data source is registered"
    events = await read_events(org_id=tenant.org_id, run_id=asked.run_id)
    assert events[-1].payload["reason"] == "No data source is registered"


async def test_budget_exhaustion_is_an_ending_of_its_own(tenant: Tenant) -> None:
    """Not a failure (arch 4.4): a run that spent its allowance still owes an answer."""
    asked = await _ask(tenant)
    await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status="running")

    run = await service.transition(
        org_id=tenant.org_id, run_id=asked.run_id, status="budget_exhausted"
    )

    assert run.status == "budget_exhausted"
    assert run.failure_reason is None


async def test_the_critics_re_entry_does_not_start_the_run_a_second_time(tenant: Tenant) -> None:
    """validating -> running is Phase 9's bounded re-entry, not a new run."""
    asked = await _ask(tenant)
    await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status="running")
    first_started = (await service.get_run(org_id=tenant.org_id, run_id=asked.run_id)).started_at

    await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status="validating")
    run = await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status="running")

    assert run.started_at == first_started
    assert await _types(tenant, asked.run_id) == ["run_started"], "re-entry emitted a second start"


async def test_a_run_that_does_not_exist_is_not_found(tenant: Tenant) -> None:
    with pytest.raises(service.NotFoundError):
        await service.transition(org_id=tenant.org_id, run_id=uuid.uuid4(), status="running")


# ---------------------------------------------------------------------------
# Answers and findings
# ---------------------------------------------------------------------------


async def test_the_answer_is_the_assistants_reply_and_the_trace_says_when_it_arrived(
    tenant: Tenant,
) -> None:
    asked = await _ask(tenant)
    await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status="running")

    await service.record_answer(
        org_id=tenant.org_id, run_id=asked.run_id, content="1,204 orders were placed in July 2026."
    )

    run = await service.get_run(org_id=tenant.org_id, run_id=asked.run_id)
    assert run.answer == "1,204 orders were placed in July 2026."
    assert await _types(tenant, asked.run_id) == ["run_started", "answer_composed"]


async def test_a_finding_carries_the_executions_that_back_it_up(tenant: Tenant) -> None:
    """The citation trail the M7 gate is about: a claim walks back to its SQL."""
    asked = await _ask(tenant)
    execution_id = uuid.uuid4()

    await service.add_finding(
        org_id=tenant.org_id,
        run_id=asked.run_id,
        statement="July took 1,204 orders",
        support=[execution_id],
        confidence="high",
    )

    run = await service.get_run(org_id=tenant.org_id, run_id=asked.run_id)
    assert len(run.findings) == 1
    assert run.findings[0].statement == "July took 1,204 orders"
    assert run.findings[0].support == [str(execution_id)]
    assert run.findings[0].confidence == "high"

    events = await read_events(org_id=tenant.org_id, run_id=asked.run_id)
    assert [event.type for event in events] == ["finding_added"]
    assert events[0].payload["statement"] == "July took 1,204 orders"


# ---------------------------------------------------------------------------
# Whose conversation it is
# ---------------------------------------------------------------------------


async def test_a_colleague_in_the_same_organization_cannot_read_the_conversation(
    tenant: Tenant,
) -> None:
    """Row-level security cannot help here: both people are in the same tenant.

    Architecture 6.2 grants every role "view *own* conversations & traces", so
    this is the layer-2 ownership check, and it answers *not found* rather than
    *forbidden* — a member told "forbidden" has learned the conversation exists.
    """
    asked = await _ask(tenant)
    conversation_id = (
        await service.get_run(org_id=tenant.org_id, run_id=asked.run_id)
    ).conversation_id

    for call in (
        service.get_conversation(
            org_id=tenant.org_id, user_id=tenant.other_user_id, conversation_id=conversation_id
        ),
        service.list_messages(
            org_id=tenant.org_id, user_id=tenant.other_user_id, conversation_id=conversation_id
        ),
        service.get_run(org_id=tenant.org_id, run_id=asked.run_id, user_id=tenant.other_user_id),
        service.list_events(
            org_id=tenant.org_id, run_id=asked.run_id, user_id=tenant.other_user_id
        ),
        service.post_message(
            org_id=tenant.org_id,
            user_id=tenant.other_user_id,
            conversation_id=conversation_id,
            content="let me in",
            idempotency_key="intruder",
        ),
    ):
        with pytest.raises(service.NotFoundError):
            await call


async def test_the_conversation_list_shows_only_your_own(tenant: Tenant) -> None:
    await _ask(tenant)
    await service.create_conversation(
        org_id=tenant.org_id, user_id=tenant.other_user_id, title="Theirs"
    )

    mine = await service.list_conversations(org_id=tenant.org_id, user_id=tenant.user_id)
    theirs = await service.list_conversations(org_id=tenant.org_id, user_id=tenant.other_user_id)

    assert [view.title for view in theirs] == ["Theirs"]
    assert len(mine) == 1
    assert mine[0].message_count == 1
