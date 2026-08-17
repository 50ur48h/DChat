"""What a run is told about the turns it follows (**D-029**, B-064).

Against a real platform database, like the rest of this suite, because three of
the four properties are things the query has to get right and one of them is
row-level security. A fake would prove the shape of the function and none of
what it is for.

The property that took the longest to see is the third one. A conversation can
hold a run created *after* the one being executed — somebody asks a second
question while the first is still running — and that run is not history, it is
the future. Rendering it would show a model a question nobody had asked yet.
"""

from __future__ import annotations

import uuid

from conftest import Tenant
from dataagent.runs import service


async def _thread(tenant: Tenant) -> uuid.UUID:
    view = await service.create_conversation(org_id=tenant.org_id, user_id=tenant.user_id)
    return view.id


async def _ask(tenant: Tenant, conversation_id: uuid.UUID, question: str) -> uuid.UUID:
    result = await service.post_message(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        conversation_id=conversation_id,
        content=question,
        idempotency_key=uuid.uuid4().hex,
    )
    return result.run_id


async def _answer(tenant: Tenant, run_id: uuid.UUID, text: str) -> None:
    await service.record_answer(org_id=tenant.org_id, run_id=run_id, content=text)


async def _history(tenant: Tenant, run_id: uuid.UUID, turns: int = 3) -> list[service.PriorTurn]:
    return await service.conversation_history(org_id=tenant.org_id, run_id=run_id, turns=turns)


async def test_a_follow_up_is_told_what_was_asked_and_what_it_was_answered(
    tenant: Tenant,
) -> None:
    conversation_id = await _thread(tenant)
    first = await _ask(tenant, conversation_id, "How many orders were placed in July 2026?")
    await _answer(tenant, first, "3,718 orders were placed in July 2026.")
    second = await _ask(tenant, conversation_id, "check again")

    history = await _history(tenant, second)

    assert [turn.question for turn in history] == ["How many orders were placed in July 2026?"]
    assert history[0].answer == "3,718 orders were placed in July 2026."


async def test_the_first_question_in_a_thread_has_no_history(tenant: Tenant) -> None:
    """And this is the common case, so it must cost nothing and change
    nothing."""
    conversation_id = await _thread(tenant)
    run_id = await _ask(tenant, conversation_id, "How many orders in July?")

    assert await _history(tenant, run_id) == []


async def test_a_run_never_sees_its_own_question_as_history(tenant: Tenant) -> None:
    """``post_message`` writes the message and the run in one transaction, so by
    the time the runner reads the thread the current question is already in
    ``messages``. Showing it back would present a model its own question twice,
    once framed as something already said."""
    conversation_id = await _thread(tenant)
    first = await _ask(tenant, conversation_id, "How many orders in July?")
    await _answer(tenant, first, "3,718.")
    second = await _ask(tenant, conversation_id, "and in June?")

    assert [turn.run_id for turn in await _history(tenant, second)] == [first]


async def test_a_question_asked_after_this_one_is_not_history(tenant: Tenant) -> None:
    """Two questions in flight at once. The second is not context for the first —
    it had not been asked when the first was."""
    conversation_id = await _thread(tenant)
    first = await _ask(tenant, conversation_id, "How many orders in July?")
    await _ask(tenant, conversation_id, "and in June?")

    assert await _history(tenant, first) == []


async def test_only_this_conversation_is_history(tenant: Tenant) -> None:
    """A thread is the unit. Another conversation of the same person's, in the
    same organization, is a different subject and often a different database
    (D-022)."""
    elsewhere = await _thread(tenant)
    other = await _ask(tenant, elsewhere, "What is our best selling item?")
    await _answer(tenant, other, "Pepperoni.")

    conversation_id = await _thread(tenant)
    first = await _ask(tenant, conversation_id, "How many orders in July?")
    await _answer(tenant, first, "3,718.")
    second = await _ask(tenant, conversation_id, "check again")

    assert [turn.question for turn in await _history(tenant, second)] == [
        "How many orders in July?"
    ]


async def test_the_thread_arrives_oldest_first_and_capped(tenant: Tenant) -> None:
    """Ordered as it happened, and no longer than the cap — the prompt must not
    grow with the length of a conversation any more than with the length of an
    investigation (4.4)."""
    conversation_id = await _thread(tenant)
    for n in range(5):
        run_id = await _ask(tenant, conversation_id, f"question {n}")
        await _answer(tenant, run_id, f"answer {n}")
    latest = await _ask(tenant, conversation_id, "check again")

    history = await _history(tenant, latest, turns=3)

    assert [turn.question for turn in history] == ["question 2", "question 3", "question 4"]


async def test_a_turn_still_running_is_carried_with_no_answer(tenant: Tenant) -> None:
    """Rather than dropped. That the previous question has not been answered is
    itself context, and it is the state a person is in when they get impatient
    and type something else."""
    conversation_id = await _thread(tenant)
    await _ask(tenant, conversation_id, "How many orders in July?")
    second = await _ask(tenant, conversation_id, "actually, make that June")

    history = await _history(tenant, second)

    assert [turn.answer for turn in history] == [None]


async def test_a_recomposed_answer_is_the_one_the_person_was_shown(tenant: Tenant) -> None:
    """The critic's one re-entry (arch M9) writes a second assistant message. The
    later one is what the answer card renders, so it is what the thread carries.
    """
    conversation_id = await _thread(tenant)
    first = await _ask(tenant, conversation_id, "How many orders in July?")
    await _answer(tenant, first, "About 3,700 orders.")
    await _answer(tenant, first, "3,718 orders were placed in July 2026.")
    second = await _ask(tenant, conversation_id, "check again")

    assert (await _history(tenant, second))[0].answer == "3,718 orders were placed in July 2026."


async def test_asking_for_no_turns_reads_nothing_at_all(tenant: Tenant) -> None:
    """A caller that has turned the thread off should not pay for a query."""
    conversation_id = await _thread(tenant)
    first = await _ask(tenant, conversation_id, "How many orders in July?")
    await _answer(tenant, first, "3,718.")
    second = await _ask(tenant, conversation_id, "check again")

    assert await _history(tenant, second, turns=0) == []


async def test_a_run_in_another_organization_is_not_found(tenant: Tenant) -> None:
    """The organization comes from the caller, not from the run id, and
    row-level security is what makes that true. A run id leaked into the wrong
    tenant reads as absent rather than as somebody else's thread."""
    conversation_id = await _thread(tenant)
    run_id = await _ask(tenant, conversation_id, "How many orders in July?")

    import pytest

    with pytest.raises(service.NotFoundError):
        await service.conversation_history(org_id=uuid.uuid4(), run_id=run_id, turns=3)
