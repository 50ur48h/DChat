"""The trace: one writer, a closed vocabulary, and a gap-free sequence.

``agent_events`` is the single source of truth for what a run did (architecture
10.3) and SSE in Phase 8 is only its live tail, so the contract these tests hold
is the one a reconnecting browser depends on: **``?after=seq`` means everything I
have not seen**. A gap would make a client wait forever for a number that never
arrives; a duplicate would make it skip a step. Both are silent failures in a
feature whose entire job is to be checkable.

The concurrency test is the one that earns its place. ``UNIQUE (run_id, seq)``
turns a race into an error rather than a mangled trace, but an error is still a
lost event — so ``write_event`` takes the run's own row lock before choosing a
number, and two writers on one run come out consecutive rather than one of them
failing.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import DBAPIError

from conftest import Tenant
from dataagent.db import models
from dataagent.runs import service
from dataagent.runs.events import EVENT_TYPES, EventWriter, UnknownRunError, read_events


async def _run(tenant: Tenant) -> uuid.UUID:
    conversation = await service.create_conversation(
        org_id=tenant.org_id, user_id=tenant.user_id, title="Trace"
    )
    asked = await service.post_message(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        conversation_id=conversation.id,
        content="What happened?",
        idempotency_key=uuid.uuid4().hex,
    )
    return asked.run_id


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------


def test_the_event_types_match_the_schema() -> None:
    """Three copies of architecture 10.3's list; a test rather than a convention.

    ``runs/events.py`` has it as a ``Literal`` so a bad type is a type error,
    ``db/models.py`` has it as the CHECK constraint the database enforces, and the
    migration has its own copy because a migration must mean the same thing in a
    year. Copies that nothing compares are copies that drift.
    """
    assert EVENT_TYPES == models.EVENT_TYPES


async def test_a_type_outside_the_list_is_refused_by_the_database(tenant: Tenant) -> None:
    """The CHECK constraint, tested through the writer that is meant to satisfy it.

    A trace UI has to render every type, so an unrecognised one is a bug rather
    than an extension point — and this proves the refusal is the database's, not
    a Python assertion somebody could remove.
    """
    writer = EventWriter(org_id=tenant.org_id, run_id=await _run(tenant))

    with pytest.raises(DBAPIError, match="type_valid"):
        await writer.emit("thinking_out_loud")  # pyright: ignore[reportArgumentType]


# ---------------------------------------------------------------------------
# The sequence
# ---------------------------------------------------------------------------


async def test_the_sequence_starts_at_one_and_has_no_gaps(tenant: Tenant) -> None:
    writer = EventWriter(org_id=tenant.org_id, run_id=await _run(tenant))

    for event_type in ("run_started", "plan_created", "tool_called", "run_finished"):
        await writer.emit(event_type)  # pyright: ignore[reportArgumentType]

    events = await read_events(org_id=tenant.org_id, run_id=writer.run_id)
    assert [event.seq for event in events] == [1, 2, 3, 4]


async def test_two_runs_number_their_events_independently(tenant: Tenant) -> None:
    """Per run, not per table — otherwise every concurrent run leaves gaps in the
    others, and ``?after=`` becomes meaningless the moment two people ask at once."""
    first = EventWriter(org_id=tenant.org_id, run_id=await _run(tenant))
    second = EventWriter(org_id=tenant.org_id, run_id=await _run(tenant))

    await first.emit("run_started")
    await second.emit("run_started")
    await first.emit("plan_created")

    assert [
        event.seq for event in await read_events(org_id=tenant.org_id, run_id=first.run_id)
    ] == [
        1,
        2,
    ]
    assert [
        event.seq for event in await read_events(org_id=tenant.org_id, run_id=second.run_id)
    ] == [1]


async def test_concurrent_writers_on_one_run_come_out_consecutive(tenant: Tenant) -> None:
    """What the run's row lock is for.

    Without it both writers read the same ``MAX(seq)``, and the unique constraint
    turns the loser into an exception — an event that happened and was not
    recorded. With it they queue, and the trace is complete.
    """
    writer = EventWriter(org_id=tenant.org_id, run_id=await _run(tenant))

    written = await asyncio.gather(
        writer.emit("tool_called", {"tool": "search_tables"}),
        writer.emit("tool_called", {"tool": "run_sql"}),
        writer.emit("tool_called", {"tool": "describe_table"}),
    )

    assert sorted(event.seq for event in written) == [1, 2, 3]
    stored = await read_events(org_id=tenant.org_id, run_id=writer.run_id)
    assert [event.seq for event in stored] == [1, 2, 3]


async def test_after_returns_only_what_the_caller_has_not_seen(tenant: Tenant) -> None:
    """The replay contract, which is also the SSE reconnect in Phase 8."""
    writer = EventWriter(org_id=tenant.org_id, run_id=await _run(tenant))
    for event_type in ("run_started", "plan_created", "query_executed", "run_finished"):
        await writer.emit(event_type)  # pyright: ignore[reportArgumentType]

    caught_up = await read_events(org_id=tenant.org_id, run_id=writer.run_id, after=2)

    assert [event.type for event in caught_up] == ["query_executed", "run_finished"]
    assert await read_events(org_id=tenant.org_id, run_id=writer.run_id, after=4) == []


# ---------------------------------------------------------------------------
# What a payload may be
# ---------------------------------------------------------------------------


async def test_a_payload_is_stored_as_given(tenant: Tenant) -> None:
    writer = EventWriter(org_id=tenant.org_id, run_id=await _run(tenant))

    written = await writer.emit(
        "finding_added",
        {"statement": "Australia is 81% of the decline", "support": ["qx_3"], "confidence": "high"},
    )

    assert written.payload["statement"] == "Australia is 81% of the decline"
    stored = await read_events(org_id=tenant.org_id, run_id=writer.run_id)
    assert stored[0].payload == written.payload


async def test_an_omitted_payload_is_an_empty_object_not_a_null(tenant: Tenant) -> None:
    writer = EventWriter(org_id=tenant.org_id, run_id=await _run(tenant))

    await writer.emit("run_started")

    assert (await read_events(org_id=tenant.org_id, run_id=writer.run_id))[0].payload == {}


async def test_an_unserialisable_payload_fails_here_rather_than_inside_the_driver(
    tenant: Tenant,
) -> None:
    """A datetime in a payload is a mistake worth a readable message.

    Left to the driver it surfaces at flush time as a complaint about a bind
    parameter, three layers below the code that wrote the event.
    """
    writer = EventWriter(org_id=tenant.org_id, run_id=await _run(tenant))

    with pytest.raises(TypeError, match="not JSON-serialisable"):
        await writer.emit("step_started", {"at": datetime.now(UTC)})


# ---------------------------------------------------------------------------
# Which run it is
# ---------------------------------------------------------------------------


async def test_writing_to_a_run_that_does_not_exist_is_refused(tenant: Tenant) -> None:
    writer = EventWriter(org_id=tenant.org_id, run_id=uuid.uuid4())

    with pytest.raises(UnknownRunError):
        await writer.emit("run_started")


async def test_a_run_in_another_organization_is_indistinguishable_from_one_that_never_existed(
    tenant: Tenant, platform: object
) -> None:
    """Row-level security makes the two cases identical, and that is the point:
    telling them apart would confirm that another tenant's run id is real."""
    other_org = uuid.uuid4()
    writer = EventWriter(org_id=other_org, run_id=await _run(tenant))

    with pytest.raises(UnknownRunError):
        await writer.emit("run_started")
