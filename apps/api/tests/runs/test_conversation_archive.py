"""Renaming a thread, and putting one away without destroying it (WP11.2a, D-039).

Plan WP11.2 says "rename/**delete**". This suite is written against an
**archive**, and the test that carries the decision is
`test_archiving_destroys_nothing_underneath` — a conversation is the root of its
runs, their events, their findings and their query executions, and architecture
0.2.4 makes that trace durable. A delete on a list screen would remove the
evidence behind answers somebody may already have acted on, at the surface where
a slip is cheapest to make.

Against a real platform database, like the rest of this suite: three of these
properties are things the query has to get right and one of them is ownership,
which is row-level security plus `_owned_conversation`.
"""

from __future__ import annotations

import uuid

import pytest

from conftest import Tenant
from dataagent.runs import service
from dataagent.runs.events import read_events


async def _thread(tenant: Tenant, title: str | None = None) -> uuid.UUID:
    view = await service.create_conversation(
        org_id=tenant.org_id, user_id=tenant.user_id, title=title
    )
    return view.id


async def _titles(tenant: Tenant, *, archived: bool = False) -> list[str | None]:
    return [
        view.title
        for view in await service.list_conversations(
            org_id=tenant.org_id, user_id=tenant.user_id, archived=archived
        )
    ]


# ---------------------------------------------------------------------------
# Renaming
# ---------------------------------------------------------------------------


async def test_a_thread_can_be_given_a_name_its_owner_will_recognise(tenant: Tenant) -> None:
    conversation_id = await _thread(tenant, "Untitled")

    renamed = await service.rename_conversation(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        conversation_id=conversation_id,
        title="Revenue by month",
    )

    assert renamed.title == "Revenue by month"
    assert await _titles(tenant) == ["Revenue by month"]


async def test_a_blank_title_clears_it_rather_than_storing_emptiness(tenant: Tenant) -> None:
    """The list falls back to the thread's first question when there is no
    title. A row of empty space would read as a rendering fault instead of as a
    thread nobody named."""
    conversation_id = await _thread(tenant, "Some name")

    renamed = await service.rename_conversation(
        org_id=tenant.org_id, user_id=tenant.user_id, conversation_id=conversation_id, title="   "
    )

    assert renamed.title is None


async def test_renaming_someone_elses_thread_is_a_404(tenant: Tenant) -> None:
    """**B-037.** A colleague's conversation does not exist as far as you are
    concerned, and that is true of writing to it as much as of reading it."""
    conversation_id = await _thread(tenant)

    with pytest.raises(service.NotFoundError):
        await service.rename_conversation(
            org_id=tenant.org_id,
            user_id=tenant.other_user_id,
            conversation_id=conversation_id,
            title="mine now",
        )


# ---------------------------------------------------------------------------
# Archiving
# ---------------------------------------------------------------------------


async def test_an_archived_thread_leaves_the_list_and_is_found_in_the_other_one(
    tenant: Tenant,
) -> None:
    """One list or the other, never both. An archived thread still showing in the
    default list would make the button look broken."""
    kept = await _thread(tenant, "Kept")
    put_away = await _thread(tenant, "Put away")

    await service.set_conversation_archived(
        org_id=tenant.org_id, user_id=tenant.user_id, conversation_id=put_away, archived=True
    )

    assert await _titles(tenant) == ["Kept"]
    assert await _titles(tenant, archived=True) == ["Put away"]
    assert kept != put_away


async def test_an_archived_thread_is_still_readable_by_its_own_address(tenant: Tenant) -> None:
    """Hidden from a list is not gone. A link somebody saved still works, which
    is the difference this whole decision is about."""
    conversation_id = await _thread(tenant, "Put away")

    await service.set_conversation_archived(
        org_id=tenant.org_id, user_id=tenant.user_id, conversation_id=conversation_id, archived=True
    )

    view = await service.get_conversation(
        org_id=tenant.org_id, user_id=tenant.user_id, conversation_id=conversation_id
    )
    assert view.archived_at is not None


async def test_a_thread_comes_back(tenant: Tenant) -> None:
    conversation_id = await _thread(tenant, "Back again")
    await service.set_conversation_archived(
        org_id=tenant.org_id, user_id=tenant.user_id, conversation_id=conversation_id, archived=True
    )

    restored = await service.set_conversation_archived(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        conversation_id=conversation_id,
        archived=False,
    )

    assert restored.archived_at is None
    assert await _titles(tenant) == ["Back again"]


async def test_archiving_twice_keeps_the_first_timestamp(tenant: Tenant) -> None:
    """*When* it was put away is the question the column exists to answer, and a
    repeated call is not a new event."""
    conversation_id = await _thread(tenant)
    first = await service.set_conversation_archived(
        org_id=tenant.org_id, user_id=tenant.user_id, conversation_id=conversation_id, archived=True
    )

    again = await service.set_conversation_archived(
        org_id=tenant.org_id, user_id=tenant.user_id, conversation_id=conversation_id, archived=True
    )

    assert again.archived_at == first.archived_at


async def test_archiving_destroys_nothing_underneath(tenant: Tenant) -> None:
    """**The test that carries D-039.** A conversation is the root of its runs,
    their events, their findings and their executions. Architecture 0.2.4 makes
    that trace durable and `agent_events` is append-only by grant — so the one
    thing "put this thread away" must never mean is that the evidence behind an
    answer somebody already acted on has gone.

    Written against the record rather than against the column: asserting
    `archived_at is not None` would pass just as happily on an implementation
    that had cascaded the runs away.
    """
    conversation_id = await _thread(tenant, "Has a history")
    asked = await service.post_message(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        conversation_id=conversation_id,
        content="How many orders?",
        idempotency_key=uuid.uuid4().hex,
    )
    await service.record_answer(
        org_id=tenant.org_id, run_id=asked.run_id, content="1,204.", method="1 query."
    )

    await service.set_conversation_archived(
        org_id=tenant.org_id, user_id=tenant.user_id, conversation_id=conversation_id, archived=True
    )

    run = await service.get_run(org_id=tenant.org_id, run_id=asked.run_id)
    assert run.answer == "1,204.", "the answer survives the archive"
    assert run.method == "1 query."
    assert await read_events(org_id=tenant.org_id, run_id=asked.run_id), "the trace survives too"
    messages = await service.list_messages(
        org_id=tenant.org_id, user_id=tenant.user_id, conversation_id=conversation_id
    )
    assert messages, "and so does the thread itself"


async def test_archiving_someone_elses_thread_is_a_404(tenant: Tenant) -> None:
    conversation_id = await _thread(tenant)

    with pytest.raises(service.NotFoundError):
        await service.set_conversation_archived(
            org_id=tenant.org_id,
            user_id=tenant.other_user_id,
            conversation_id=conversation_id,
            archived=True,
        )
