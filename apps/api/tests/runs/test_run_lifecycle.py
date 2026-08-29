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
from decimal import Decimal
from typing import cast

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


async def test_the_answer_carries_the_method_that_reached_it(tenant: Tenant) -> None:
    """**B-100.** Architecture 4.2 makes an answer four things, and until this
    column the method was the one built on every run and stored on none — so the
    single line written for a reader who will not open the SQL was the one that
    never reached them."""
    asked = await _ask(tenant)
    await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status="running")

    await service.record_answer(
        org_id=tenant.org_id,
        run_id=asked.run_id,
        content="1,204 orders were placed in July 2026.",
        method="1 query over one step, against orders.",
    )

    run = await service.get_run(org_id=tenant.org_id, run_id=asked.run_id)
    assert run.method == "1 query over one step, against orders."


async def test_an_answer_that_says_nothing_about_its_method_stores_nothing(
    tenant: Tenant,
) -> None:
    """Empty rather than a sentence nobody wrote. A refusal composed before this
    column existed has no method, and inventing one would be the platform
    asserting something it cannot know."""
    asked = await _ask(tenant)
    await service.record_answer(
        org_id=tenant.org_id, run_id=asked.run_id, content="I could not answer that."
    )

    run = await service.get_run(org_id=tenant.org_id, run_id=asked.run_id)
    assert run.method == ""


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


# ---------------------------------------------------------------------------
# What the run cost, from the ledger that already knew (B-153)
# ---------------------------------------------------------------------------


#: One priced model, so a test can prove a total rather than only its absence.
PRICES = {"m-priced": {"input": 10.0, "output": 30.0}}


async def _charge(
    tenant: Tenant,
    run_id: uuid.UUID,
    *,
    model: str,
    role: str,
    input_tokens: int,
    output_tokens: int,
    priced: bool = False,
    cached: int | None = None,
    estimated: bool = False,
) -> None:
    """One provider call, through the meter the product uses.

    Through `meter.record` rather than by inserting a row, so the test exercises
    the same path a real call takes — including `estimate_cost`, which is what
    decides whether the run ends up with a number or a NULL.
    """
    from dataagent.llm.base import Usage
    from dataagent.llm.meter import record
    from llm_fixture import build_settings

    await record(
        org_id=tenant.org_id,
        run_id=run_id,
        role=role,  # type: ignore[arg-type]
        tier="strong",
        provider="openai",
        model=model,
        usage=Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached,
            estimated=estimated,
        ),
        latency_ms=12,
        settings=build_settings(llm_prices=PRICES) if priced else None,
    )


async def test_a_finished_run_says_what_it_cost(tenant: Tenant) -> None:
    """**Both columns existed since revision 0012 and nothing wrote them.**

    `model_usage`'s own comment calls it "a rollup for the trace UI"; the rollup
    was never built, so every run carried NULL and `{}` while `usage_ledger` held
    the answer and `budget.spent_on_run` already summed it to enforce the per-run
    ceiling. Asserted on the **view the route returns**, not on the ledger query,
    because a value that is right in flight and absent from the response is the
    defect this repository keeps filing (B-133).
    """
    asked = await _ask(tenant)
    await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status="running")
    await _charge(
        tenant,
        asked.run_id,
        model="m-1",
        role="sql",
        input_tokens=1000,
        output_tokens=100,
    )
    await _charge(
        tenant,
        asked.run_id,
        model="m-1",
        role="sql",
        input_tokens=500,
        output_tokens=50,
    )
    await _charge(
        tenant,
        asked.run_id,
        model="m-2",
        role="compose",
        input_tokens=200,
        output_tokens=20,
    )

    run = await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status="completed")

    usage = run.model_usage
    assert usage["calls"] == 3
    assert usage["input_tokens"] == 1700
    assert usage["output_tokens"] == 170
    assert usage["unpriced_calls"] == 3, "no price table in tests, so every call is unpriced"

    by_model = cast(list[dict[str, object]], usage["by_model"])
    # Grouped by model **and role**, because "what did this cost" and "where did
    # it go" are the same question asked at two widths.
    assert [(row["model"], row["role"], row["calls"]) for row in by_model] == [
        ("m-1", "sql", 2),
        ("m-2", "compose", 1),
    ]


async def test_the_rollup_carries_the_cached_share_and_the_estimated_count(
    tenant: Tenant,
) -> None:
    """**Revision 0034, and the reason it exists.**

    `cost_estimate` prices the whole input at the full rate while the provider
    bills the cached part at less, so the total overstates every cache hit. The
    size of that was unmeasurable because the number was never stored. Both this
    and `estimated_calls` sit *beside* the total rather than inside it — nothing
    here changes a price.

    Asserted on the run the route returns, not on the ledger query: a value
    that is right in flight and absent from the response is the defect this
    repository keeps filing.
    """
    asked = await _ask(tenant)
    await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status="running")
    await _charge(
        tenant,
        asked.run_id,
        model="m-1",
        role="sql",
        input_tokens=1000,
        output_tokens=100,
        cached=900,
    )
    await _charge(
        tenant,
        asked.run_id,
        model="m-1",
        role="sql",
        input_tokens=500,
        output_tokens=50,
        cached=100,
    )
    # A provider that reports no usage: we counted these, and a total that mixes
    # measured with guessed and does not say so is the silent-mixing shape.
    await _charge(
        tenant,
        asked.run_id,
        model="m-2",
        role="compose",
        input_tokens=200,
        output_tokens=20,
        estimated=True,
    )

    run = await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status="completed")

    usage = run.model_usage
    assert usage["cached_input_tokens"] == 1000
    assert usage["input_tokens"] == 1700, "cached is a subset of input, never an addition"
    assert usage["estimated_calls"] == 1

    by_model = cast(list[dict[str, object]], usage["by_model"])
    rows = {(row["model"], row["role"]): row for row in by_model}
    assert rows[("m-1", "sql")]["cached_input_tokens"] == 1000
    # None rather than 0 on the model whose calls said nothing about caching.
    assert rows[("m-2", "compose")]["cached_input_tokens"] is None
    assert rows[("m-2", "compose")]["estimated_calls"] == 1


async def test_an_unpriced_call_leaves_the_total_null_rather_than_understating_it(
    tenant: Tenant,
) -> None:
    """**Null means unpriced, never free** — the rule both columns were born with.

    A run holding a call the price table does not cover gets NULL, not a total
    that silently omits it. An understated number is worse than an absent one,
    because it reads as the answer and nothing about it looks wrong.
    """
    asked = await _ask(tenant)
    await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status="running")
    await _charge(
        tenant, asked.run_id, model="unpriced", role="sql", input_tokens=10, output_tokens=1
    )

    run = await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status="completed")

    assert run.cost_estimate is None
    # And the breakdown survives, so NULL never means "we know nothing".
    assert run.model_usage["unpriced_calls"] == 1
    assert run.model_usage["calls"] == 1


async def test_a_run_that_called_no_model_claims_no_price(tenant: Tenant) -> None:
    """Nothing recorded is not the same claim as nothing spent.

    From here the two are indistinguishable — a run that made no call and a run
    whose ledger write failed both present as zero rows — so the honest answer is
    that there is no priced total, with an empty breakdown beside it saying why.
    """
    asked = await _ask(tenant)
    await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status="running")

    run = await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status="completed")

    assert run.cost_estimate is None
    assert run.model_usage == {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        # None, not 0: no call reported a cached share, which is not a claim
        # that none of the input was cached (revision 0034).
        "cached_input_tokens": None,
        "unpriced_calls": 0,
        "estimated_calls": 0,
        "by_model": [],
    }


async def test_a_priced_run_reports_the_total_the_ledger_adds_up_to(tenant: Tenant) -> None:
    """The positive case, and the one a person actually reads.

    Two calls on a model priced at $10/M in and $30/M out: 1,000 + 500 input and
    100 + 50 output, which is 0.015 + 0.0045. Asserted as an exact Decimal
    because a cost shown to a person must not have drifted through a float.
    """
    asked = await _ask(tenant)
    await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status="running")
    await _charge(
        tenant,
        asked.run_id,
        model="m-priced",
        role="sql",
        input_tokens=1000,
        output_tokens=100,
        priced=True,
    )
    await _charge(
        tenant,
        asked.run_id,
        model="m-priced",
        role="sql",
        input_tokens=500,
        output_tokens=50,
        priced=True,
    )

    run = await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status="completed")

    assert run.cost_estimate == Decimal("0.0195")
    assert run.model_usage["unpriced_calls"] == 0
    by_model = cast(list[dict[str, object]], run.model_usage["by_model"])
    # The value, not its spelling: `usage_ledger.cost_usd` is scale 6, so the
    # string carries the ledger's own precision (`0.019500`) rather than a
    # prettier one. A string because JSONB has no decimal and a float would round
    # a price; the reader formats it, and nothing does arithmetic on it again.
    assert Decimal(str(by_model[0]["cost_usd"])) == Decimal("0.0195")


async def test_one_unpriced_call_among_priced_ones_still_nulls_the_total(tenant: Tenant) -> None:
    """The case the rule exists for, and the one an average would get wrong.

    Four fifths of a run being priced is exactly when a total is most tempting
    and most misleading: it looks complete. The breakdown keeps both halves, so
    the reader can see what was missed rather than being handed a smaller number.
    """
    asked = await _ask(tenant)
    await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status="running")
    await _charge(
        tenant,
        asked.run_id,
        model="m-priced",
        role="sql",
        input_tokens=1000,
        output_tokens=100,
        priced=True,
    )
    await _charge(
        tenant,
        asked.run_id,
        model="m-unknown",
        role="compose",
        input_tokens=10,
        output_tokens=1,
        priced=True,
    )

    run = await service.transition(org_id=tenant.org_id, run_id=asked.run_id, status="completed")

    assert run.cost_estimate is None
    assert run.model_usage["unpriced_calls"] == 1
    assert run.model_usage["calls"] == 2
