"""The one door every trace event goes through (architecture Part 10.3).

``agent_events`` is the single source of truth for what a run did; SSE in Phase 8
is only its live tail, and the trace UI, the replay after a refresh and the eval
harness all read the same rows. So there is one writer, from day one, and
everything the user will ever be shown passes through it — the same shape
``dal.run`` holds for customer data and ``llm.complete`` for model calls.

Three rules it enforces, none of which a caller can opt out of.

**The type is from a closed list.** Architecture 10.3 names twenty event types and
a trace UI has to render each one, so an unrecognised type is a bug rather than an
extension point. ``EventType`` is a ``Literal`` over exactly those names, the
database has the same list as a CHECK constraint, and a test asserts the two
copies agree.

**``seq`` is gap-free within a run.** ``?after=seq`` means "everything I have not
seen"; a gap would make a reconnecting client wait forever for a number that will
never arrive, and a duplicate would make it skip a step. The next value is taken
under ``SELECT … FOR UPDATE`` on the run's own row, so two writers on one run
serialise on the run rather than racing for a position — and the unique
constraint underneath turns any mistake here into an error instead of a silently
mangled trace.

**The payload is JSON and is checked here.** A payload that cannot be serialised
fails at flush time otherwise, deep inside a driver, with a message about a
parameter rather than about an event. What this module cannot check is the rule
10.3 actually cares about — that payloads are *built for eyes*, short public
strings out of structured tool output and never raw model reasoning. That stays a
review question, and it is why the payload types live beside the emitting code.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, get_args

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dataagent.db.models import AgentEvent, AgentRun
from dataagent.tenancy.session import org_session

__all__ = [
    "EventType",
    "EventWriter",
    "RecordedEvent",
    "UnknownRunError",
    "read_events",
    "write_event",
]

#: Architecture 10.3's vocabulary, as a type. ``dataagent.db.models.EVENT_TYPES``
#: is the copy the schema is built from; ``test_event_types_match_the_schema``
#: asserts they are the same list in the same order.
EventType = Literal[
    "run_started",
    "intent_classified",
    "context_selected",
    "capability_checked",
    "plan_created",
    "step_started",
    "tool_called",
    # The result of a `tool_called` for `search_knowledge` — what the agent
    # asked its documents and what they said (**B-075**, D-032). Twenty-first
    # in a list architecture 10.3 fixed at twenty, and it earns the place: the
    # Phase 10 gate turns on a person being able to *see* a document being
    # consulted mid-run, and `tool_called` records the asking without the
    # answer.
    "knowledge_consulted",
    "sql_validated",
    "sql_rejected",
    "query_executed",
    "result_summarized",
    "finding_added",
    "hypothesis_updated",
    "reflection",
    "critic_verdict",
    "budget_warning",
    "budget_exhausted",
    "answer_composed",
    "run_finished",
    "error",
]

EVENT_TYPES: tuple[str, ...] = get_args(EventType)


class UnknownRunError(Exception):
    """No run with that id in this organization.

    One error for "never existed" and "belongs to somebody else", because under
    row-level security this code cannot tell them apart — and should not be able
    to, since telling them apart is what would leak the existence of another
    tenant's run.
    """


@dataclass(frozen=True, slots=True)
class RecordedEvent:
    """One written event, as a reader of the trace sees it."""

    seq: int
    type: str
    payload: dict[str, object]
    ts: datetime


def _payload(payload: Mapping[str, object] | None) -> dict[str, object]:
    """Reject anything that will not survive the trip to JSONB, here and loudly."""
    if payload is None:
        return {}
    materialised = dict(payload)
    try:
        json.dumps(materialised)
    except (TypeError, ValueError) as error:
        raise TypeError(f"Event payload is not JSON-serialisable: {error}") from error
    return materialised


async def write_event(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    event_type: EventType,
    payload: Mapping[str, object] | None = None,
) -> RecordedEvent:
    """Append one event on an already org-scoped session.

    Takes the session rather than opening its own so an event can commit in the
    same transaction as the thing it describes — a run that started without a
    ``run_started``, or a ``run_started`` for a run that did not, are both worse
    than either alone. It is the same reason ``orgs.service.audit`` is shaped
    this way.
    """
    locked = (
        await session.execute(select(AgentRun.id).where(AgentRun.id == run_id).with_for_update())
    ).scalar_one_or_none()
    if locked is None:
        raise UnknownRunError(f"No run {run_id} in this organization")

    # MAX + 1 rather than a sequence: a sequence is global and would leave gaps
    # in every run's numbering as soon as two ran at once. The run row is locked
    # above, so this read cannot race another writer on the same run.
    highest = (
        await session.execute(
            select(func.coalesce(func.max(AgentEvent.seq), 0)).where(AgentEvent.run_id == run_id)
        )
    ).scalar_one()

    row = AgentEvent(
        org_id=org_id,
        run_id=run_id,
        seq=highest + 1,
        type=event_type,
        payload=_payload(payload),
    )
    session.add(row)
    await session.flush()
    return RecordedEvent(seq=row.seq, type=row.type, payload=row.payload, ts=row.ts)


class EventWriter:
    """A trace writer bound to one run.

    What the agent is handed in Phase 7 and 8, so that emitting a step is one call
    that cannot name the wrong run. Each ``emit`` is its own transaction, which is
    what makes a trace visible *while* a run is going rather than only when it
    commits — the whole point of a live trace.
    """

    __slots__ = ("_org_id", "_run_id")

    def __init__(self, *, org_id: uuid.UUID, run_id: uuid.UUID) -> None:
        self._org_id = org_id
        self._run_id = run_id

    @property
    def run_id(self) -> uuid.UUID:
        return self._run_id

    async def emit(
        self, event_type: EventType, payload: Mapping[str, object] | None = None
    ) -> RecordedEvent:
        async with org_session(self._org_id) as session:
            return await write_event(
                session,
                org_id=self._org_id,
                run_id=self._run_id,
                event_type=event_type,
                payload=payload,
            )


async def read_events(
    *, org_id: uuid.UUID, run_id: uuid.UUID, after: int = 0, limit: int = 500
) -> list[RecordedEvent]:
    """This run's events after ``seq``, oldest first — the poll and the replay.

    Phase 8's SSE endpoint reads the same rows through the same filter; streaming
    changes when they are delivered, not what they are.
    """
    async with org_session(org_id) as session:
        rows = (
            (
                await session.execute(
                    select(AgentEvent)
                    .where(AgentEvent.run_id == run_id, AgentEvent.seq > after)
                    .order_by(AgentEvent.seq)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [
            RecordedEvent(seq=row.seq, type=row.type, payload=row.payload, ts=row.ts)
            for row in rows
        ]
