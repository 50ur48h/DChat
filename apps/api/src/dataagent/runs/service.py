"""Conversations, messages and the life of a run (architecture Part 10.1 and 10.2).

This is layer 2 of the three the architecture describes in 6.2: the route guard
has already said *this caller is a member of this organization*, row-level
security will refuse anything belonging to another one, and what is left for this
module is the object-ownership check between them.

**A conversation belongs to the person who started it.** The role matrix in 6.2
grants every role "ask questions, view own conversations & traces" — *own*, and
there is no row in it that grants anyone else's. So a conversation is read by its
creator and by nobody else, Admins included, and the refusal is a 404 rather than
a 403: a member who is told "403" has learned that a conversation with that id
exists in their organization, which is the one bit this check is meant to
withhold. Widening this later — an Admin oversight view, a shared conversation —
is a product decision with its own audit consequences, not a default to drift
into.

**A run is created, not started.** ``POST …/messages`` writes the question, the
run and the user's message in one transaction and answers 202 with the run id.
Nothing executes it yet: WP7.2 brings the planner that moves it out of ``queued``.
That is why every transition here is a function with its own rules rather than a
setter — the state machine is the part that has to be right before there is
anything driving it.

**A retried POST is the same question.** The body carries an idempotency key
(10.2), stored on the message and unique per conversation, so a double-tapped
send button or a client retry after a timeout returns the run that already exists
instead of starting a second one. With D-019's per-run spend ceiling behind a real
provider key, a duplicate run is duplicate money.

**A conversation names the database it is about** (DECISIONS **D-022**). The
source is chosen once, when the thread starts, and every question in it is
answered against that one — because a follow-up drawn from a different database
than the question it follows would be incomparable with it, and nothing in the
answer would say so. Naming it is optional and null is not an error: the
scheduler still resolves an organization's single source, and still refuses
rather than guesses when there is more than one. What this module owes the
choice is the check the foreign key cannot make — a constraint check does not
consult row-level security, so an id belonging to *another* organization would
satisfy the database and nothing else. It is looked up through the org session
here and refused as a 404 if it is not this organization's.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dataagent.db.models import (
    AgentRun,
    Conversation,
    DataSource,
    Finding,
    Message,
    QueryExecution,
    ResultArtifact,
)
from dataagent.runs.events import EventType, RecordedEvent, read_events, write_event
from dataagent.tenancy.session import org_session

__all__ = [
    "ConversationView",
    "ExecutionView",
    "InvalidTransitionError",
    "MessageView",
    "NotFoundError",
    "RunView",
    "add_finding",
    "create_conversation",
    "get_conversation",
    "get_execution",
    "get_run",
    "list_conversations",
    "list_events",
    "list_messages",
    "post_message",
    "record_answer",
    "transition",
]

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_VALIDATING = "validating"
STATUS_COMPLETED = "completed"
STATUS_INTERRUPTED = "interrupted"
STATUS_FAILED = "failed"
STATUS_BUDGET_EXHAUSTED = "budget_exhausted"

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"

#: Where a run may go from where it is. Terminal statuses are absent as keys,
#: which is how "a finished run cannot move again" is stated: there is no entry
#: to look up rather than an empty one to forget to check.
#:
#: ``queued -> failed`` is here because a run can die before it starts — no
#: reachable data source, a budget already spent — and a run that cannot fail
#: from its first state is a run that hangs in ``queued`` forever.
ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    STATUS_QUEUED: (STATUS_RUNNING, STATUS_INTERRUPTED, STATUS_FAILED),
    STATUS_RUNNING: (
        STATUS_VALIDATING,
        STATUS_COMPLETED,
        STATUS_INTERRUPTED,
        STATUS_FAILED,
        STATUS_BUDGET_EXHAUSTED,
    ),
    # Back to running is the critic's bounded re-entry (arch M9, Phase 9).
    STATUS_VALIDATING: (
        STATUS_RUNNING,
        STATUS_COMPLETED,
        STATUS_INTERRUPTED,
        STATUS_FAILED,
        STATUS_BUDGET_EXHAUSTED,
    ),
}

TERMINAL_STATUSES: frozenset[str] = frozenset(
    {STATUS_COMPLETED, STATUS_INTERRUPTED, STATUS_FAILED, STATUS_BUDGET_EXHAUSTED}
)

#: How much of a question becomes a conversation's title when the client did not
#: give one. Long enough to recognise, short enough for a sidebar.
TITLE_LENGTH = 120


class NotFoundError(Exception):
    """No such object for this caller.

    Deliberately undifferentiated: "does not exist", "belongs to another
    organization" and "belongs to another member" are one answer, because telling
    them apart is exactly what would leak.
    """


class InvalidTransitionError(Exception):
    """A run cannot go from where it is to where it was asked to go."""


@dataclass(frozen=True, slots=True)
class ConversationView:
    id: uuid.UUID
    title: str | None
    user_id: uuid.UUID | None
    created_at: datetime
    message_count: int = 0
    last_run_id: uuid.UUID | None = None
    #: The data source every question here is answered against, if one was
    #: named. Null is a thread that named none — legal, and resolved (or
    #: refused) per question by the scheduler.
    data_source_id: uuid.UUID | None = None
    #: That source's display name, so a client can say which database it is
    #: talking to without a second call. Null when the source was removed.
    data_source_name: str | None = None


@dataclass(frozen=True, slots=True)
class MessageView:
    id: uuid.UUID
    role: str
    content: str
    run_id: uuid.UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FindingView:
    id: uuid.UUID
    statement: str
    #: ``query_executions.id`` values — the citation an answer is walked back
    #: through.
    support: list[str]
    confidence: str


@dataclass(frozen=True, slots=True)
class RunView:
    id: uuid.UUID
    conversation_id: uuid.UUID
    status: str
    question: str
    #: The assistant's message for this run, once there is one. Kept on the
    #: message rather than duplicated onto the run: the answer *is* the reply.
    answer: str | None
    findings: list[FindingView] = field(default_factory=list["FindingView"])
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure_reason: str | None = None
    cost_estimate: Decimal | None = None
    model_usage: dict[str, object] = field(default_factory=dict[str, object])


@dataclass(frozen=True, slots=True)
class ExecutionView:
    """One query a run ran, as a person may see it (architecture 10.2).

    ``sample_rows`` is capped at what ``result_artifacts`` keeps inline (50) and
    is already masked. ``row_count`` is what the query actually returned, so a
    reader can tell "50 rows" from "50 shown of 71,798".
    """

    id: uuid.UUID
    run_id: uuid.UUID
    #: ok | error | refused. A refusal never reached an engine, and this row is
    #: the only place it is visible at all (WP5.2b).
    status: str
    sql: str
    tables: list[str]
    columns: list[str]
    row_count: int | None
    duration_ms: int | None
    violation_code: str | None
    #: Sanitized already — connectors raise nothing else, and a violation
    #: message names the identifier it refused, never a value.
    error: str | None
    sensitive_accessed: bool
    masked_columns: list[str]
    sample_rows: list[list[object]]
    truncated: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AskResult:
    """What ``POST …/messages`` answers with."""

    run_id: uuid.UUID
    message_id: uuid.UUID
    #: False when an idempotency key matched and this is the run that already
    #: existed. The route still answers 202 — the client's question *is* being
    #: dealt with — but a caller that cares can tell.
    created: bool
    #: The conversation's data source, carried out so the route can schedule the
    #: run without asking the database a second time for something it just read.
    data_source_id: uuid.UUID | None = None


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


async def _owned_conversation(
    session: AsyncSession, *, conversation_id: uuid.UUID, user_id: uuid.UUID
) -> Conversation:
    """The caller's own conversation, or ``NotFoundError``.

    A conversation whose ``user_id`` is null — its creator's user row was
    removed — is readable by nobody. That is the fail-closed direction, and it is
    the same trade the rest of the codebase makes with a missing owner.
    """
    row = (
        (await session.execute(select(Conversation).where(Conversation.id == conversation_id)))
        .scalars()
        .one_or_none()
    )
    if row is None or row.user_id is None or row.user_id != user_id:
        raise NotFoundError("No such conversation")
    return row


async def _named_data_source(
    session: AsyncSession, data_source_id: uuid.UUID | None
) -> DataSource | None:
    """The organization's own data source with that id, or ``NotFoundError``.

    The foreign key is not this check. A constraint check does not consult
    row-level security, so an id belonging to another organization satisfies the
    database perfectly well; only a read through the org session can tell the
    difference. Refused as "no such data source" for the same reason an
    unowned conversation is — the caller learns nothing about what exists
    elsewhere.
    """
    if data_source_id is None:
        return None
    row = (
        (await session.execute(select(DataSource).where(DataSource.id == data_source_id)))
        .scalars()
        .one_or_none()
    )
    if row is None:
        raise NotFoundError("No such data source")
    return row


async def create_conversation(
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    title: str | None = None,
    data_source_id: uuid.UUID | None = None,
) -> ConversationView:
    async with org_session(org_id) as session:
        source = await _named_data_source(session, data_source_id)
        row = Conversation(
            org_id=org_id, user_id=user_id, title=title, data_source_id=data_source_id
        )
        session.add(row)
        await session.flush()
        return ConversationView(
            id=row.id,
            title=row.title,
            user_id=row.user_id,
            created_at=row.created_at,
            data_source_id=row.data_source_id,
            data_source_name=source.name if source is not None else None,
        )


async def list_conversations(*, org_id: uuid.UUID, user_id: uuid.UUID) -> list[ConversationView]:
    """This caller's conversations, newest first, with enough to render a list."""
    async with org_session(org_id) as session:
        counts = (
            select(Message.conversation_id, func.count(Message.id).label("message_count"))
            .group_by(Message.conversation_id)
            .subquery()
        )
        rows = await session.execute(
            select(Conversation, func.coalesce(counts.c.message_count, 0), DataSource.name)
            .outerjoin(counts, counts.c.conversation_id == Conversation.id)
            .outerjoin(DataSource, DataSource.id == Conversation.data_source_id)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.created_at.desc())
        )
        return [
            ConversationView(
                id=conversation.id,
                title=conversation.title,
                user_id=conversation.user_id,
                created_at=conversation.created_at,
                message_count=count,
                data_source_id=conversation.data_source_id,
                data_source_name=source_name,
            )
            for conversation, count, source_name in rows.all()
        ]


async def get_conversation(
    *, org_id: uuid.UUID, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> ConversationView:
    async with org_session(org_id) as session:
        row = await _owned_conversation(session, conversation_id=conversation_id, user_id=user_id)
        latest = (
            await session.execute(
                select(AgentRun.id)
                .where(AgentRun.conversation_id == conversation_id)
                .order_by(AgentRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        count = (
            await session.execute(
                select(func.count(Message.id)).where(Message.conversation_id == conversation_id)
            )
        ).scalar_one()
        source_name = (
            await session.execute(
                select(DataSource.name).where(DataSource.id == row.data_source_id)
            )
        ).scalar_one_or_none()
        return ConversationView(
            id=row.id,
            title=row.title,
            user_id=row.user_id,
            created_at=row.created_at,
            message_count=count,
            last_run_id=latest,
            data_source_id=row.data_source_id,
            data_source_name=source_name,
        )


async def list_messages(
    *, org_id: uuid.UUID, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> list[MessageView]:
    async with org_session(org_id) as session:
        await _owned_conversation(session, conversation_id=conversation_id, user_id=user_id)
        rows = (
            (
                await session.execute(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.created_at, Message.id)
                )
            )
            .scalars()
            .all()
        )
        return [
            MessageView(
                id=row.id,
                role=row.role,
                content=row.content,
                run_id=row.run_id,
                created_at=row.created_at,
            )
            for row in rows
        ]


# ---------------------------------------------------------------------------
# Asking a question
# ---------------------------------------------------------------------------


async def post_message(
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    content: str,
    idempotency_key: str,
) -> AskResult:
    """Record a question and queue the run that will answer it.

    The message and the run are written in one transaction: a question with no
    run would never be answered, and a run with no question is one nobody can
    show. Nothing executes here — the run is left ``queued`` for WP7.2's planner.
    """
    try:
        return await _create_question(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            content=content,
            idempotency_key=idempotency_key,
        )
    except IntegrityError:
        # Two identical POSTs in flight at once: the unique index refused the
        # second. The first one's run is the right answer to both, and it has to
        # be read on a new session because this one's transaction is now dead.
        existing = await _find_by_key(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
        )
        if existing is None:  # pragma: no cover - a different constraint, not ours
            raise
        return existing


async def _create_question(
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    content: str,
    idempotency_key: str,
) -> AskResult:
    async with org_session(org_id) as session:
        conversation = await _owned_conversation(
            session, conversation_id=conversation_id, user_id=user_id
        )

        replayed = (
            (
                await session.execute(
                    select(Message).where(
                        Message.conversation_id == conversation_id,
                        Message.idempotency_key == idempotency_key,
                    )
                )
            )
            .scalars()
            .one_or_none()
        )
        if replayed is not None:
            # The run this message already started. Not null in practice — the
            # two are written together below — but the column allows it, so the
            # replay says so rather than asserting.
            if replayed.run_id is None:  # pragma: no cover - only after a run is deleted
                raise NotFoundError("That message's run no longer exists")
            return AskResult(
                run_id=replayed.run_id,
                message_id=replayed.id,
                created=False,
                data_source_id=conversation.data_source_id,
            )

        run = AgentRun(
            org_id=org_id,
            conversation_id=conversation_id,
            user_id=user_id,
            status=STATUS_QUEUED,
            question=content,
        )
        session.add(run)
        await session.flush()

        message = Message(
            org_id=org_id,
            conversation_id=conversation_id,
            role=ROLE_USER,
            content=content,
            run_id=run.id,
            idempotency_key=idempotency_key,
        )
        session.add(message)

        # A conversation nobody titled is a blank line in a list. The first
        # question is the best title anyone has, and it is only ever a default:
        # a title the client set is never overwritten.
        if conversation.title is None:
            conversation.title = _title_from(content)

        await session.flush()
        return AskResult(
            run_id=run.id,
            message_id=message.id,
            created=True,
            data_source_id=conversation.data_source_id,
        )


async def _find_by_key(
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    idempotency_key: str,
) -> AskResult | None:
    async with org_session(org_id) as session:
        conversation = await _owned_conversation(
            session, conversation_id=conversation_id, user_id=user_id
        )
        row = (
            (
                await session.execute(
                    select(Message).where(
                        Message.conversation_id == conversation_id,
                        Message.idempotency_key == idempotency_key,
                    )
                )
            )
            .scalars()
            .one_or_none()
        )
        if row is None or row.run_id is None:
            return None
        return AskResult(
            run_id=row.run_id,
            message_id=row.id,
            created=False,
            data_source_id=conversation.data_source_id,
        )


def _title_from(question: str) -> str:
    collapsed = " ".join(question.split())
    if len(collapsed) <= TITLE_LENGTH:
        return collapsed
    return collapsed[: TITLE_LENGTH - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# The life of a run
# ---------------------------------------------------------------------------


async def _owned_run(
    session: AsyncSession, *, run_id: uuid.UUID, user_id: uuid.UUID | None
) -> AgentRun:
    """The run, and — when a user is given — a check that it is theirs.

    ``user_id=None`` is for the agent itself, which moves a run it is executing
    and is not a member of anything. Row-level security still scopes it to the
    organization; what is skipped is the ownership check, which has no meaning
    for a caller that is not a person.
    """
    row = (
        (await session.execute(select(AgentRun).where(AgentRun.id == run_id)))
        .scalars()
        .one_or_none()
    )
    if row is None:
        raise NotFoundError("No such run")
    if user_id is not None and row.user_id != user_id:
        raise NotFoundError("No such run")
    return row


async def _run_view(session: AsyncSession, run: AgentRun) -> RunView:
    """A run with its answer and its findings — what 10.2 promises this route returns."""
    answer = (
        await session.execute(
            select(Message.content)
            .where(Message.run_id == run.id, Message.role == ROLE_ASSISTANT)
            .order_by(Message.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    findings = (
        (
            await session.execute(
                select(Finding)
                .where(Finding.run_id == run.id)
                .order_by(Finding.created_at, Finding.id)
            )
        )
        .scalars()
        .all()
    )
    return RunView(
        id=run.id,
        conversation_id=run.conversation_id,
        status=run.status,
        question=run.question,
        answer=answer,
        findings=[
            FindingView(
                id=finding.id,
                statement=finding.statement,
                support=[str(item) for item in finding.support],
                confidence=finding.confidence,
            )
            for finding in findings
        ],
        started_at=run.started_at,
        finished_at=run.finished_at,
        failure_reason=run.failure_reason,
        cost_estimate=run.cost_estimate,
        model_usage=dict(run.model_usage),
    )


async def get_run(
    *, org_id: uuid.UUID, run_id: uuid.UUID, user_id: uuid.UUID | None = None
) -> RunView:
    async with org_session(org_id) as session:
        return await _run_view(session, await _owned_run(session, run_id=run_id, user_id=user_id))


async def transition(
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    status: str,
    failure_reason: str | None = None,
    totals: dict[str, object] | None = None,
) -> RunView:
    """Move a run, and write the event that says so.

    The legal moves are in ``ALLOWED_TRANSITIONS`` and nothing else may set
    ``agent_runs.status``. Two consequences are load-bearing: a finished run can
    never move again — so a late tool result cannot resurrect one and quietly
    change an answer somebody has already read — and every ending stamps
    ``finished_at``, which the database's own CHECK constraint insists on.

    ``run_started`` and ``run_finished`` are emitted here rather than by the
    caller, because a status that changed without an event in the trace is
    precisely the gap the trace exists to close.
    """
    async with org_session(org_id) as session:
        run = await _owned_run(session, run_id=run_id, user_id=None)
        allowed = ALLOWED_TRANSITIONS.get(run.status, ())
        if status not in allowed:
            raise InvalidTransitionError(
                f"A run that is {run.status} cannot become {status}"
                + (f"; it may become {' or '.join(allowed)}" if allowed else " — it has finished")
            )

        # Read before the write, because "is this the first time it started"
        # cannot be asked once `started_at` has been stamped — and a re-entry
        # from `validating` must not emit a second `run_started`.
        first_start = status == STATUS_RUNNING and run.started_at is None

        now = datetime.now(UTC)
        run.status = status
        if first_start:
            run.started_at = now
        if status in TERMINAL_STATUSES:
            run.finished_at = now
            run.failure_reason = failure_reason
        await session.flush()

        event, payload = _event_for(status, run=run, totals=totals, first_start=first_start)
        if event is not None:
            await write_event(
                session, org_id=org_id, run_id=run_id, event_type=event, payload=payload
            )

        return await _run_view(session, run)


def _event_for(
    status: str, *, run: AgentRun, totals: dict[str, object] | None, first_start: bool
) -> tuple[EventType | None, dict[str, object]]:
    """The trace entry a status change earns, per architecture 10.3's type list.

    ``validating``, and a re-entry from it back to ``running``, get none: 10.3 has
    no type for either, and the events that *do* describe that part of a run — the
    critic's verdict, the next step — are written by the code that does the work
    and knows what it found. Inventing a type here would put a word in the trace
    that no UI was designed to render.
    """
    if first_start:
        return ("run_started", {"question": run.question})
    if status in TERMINAL_STATUSES:
        payload: dict[str, object] = {"status": status, "totals": totals or {}}
        if run.failure_reason is not None:
            payload["reason"] = run.failure_reason
        return ("run_finished", payload)
    return (None, {})


async def record_answer(*, org_id: uuid.UUID, run_id: uuid.UUID, content: str) -> MessageView:
    """Write the assistant's reply for a run.

    Separate from ``transition`` on purpose: composing an answer and declaring the
    run over are two different claims, and WP9's critic runs between them.
    """
    async with org_session(org_id) as session:
        run = await _owned_run(session, run_id=run_id, user_id=None)
        message = Message(
            org_id=org_id,
            conversation_id=run.conversation_id,
            role=ROLE_ASSISTANT,
            content=content,
            run_id=run_id,
        )
        session.add(message)
        await session.flush()
        await write_event(
            session,
            org_id=org_id,
            run_id=run_id,
            event_type="answer_composed",
            payload={"message_id": str(message.id), "length": len(content)},
        )
        return MessageView(
            id=message.id,
            role=message.role,
            content=message.content,
            run_id=run_id,
            created_at=message.created_at,
        )


async def add_finding(
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    statement: str,
    support: Sequence[uuid.UUID | str] = (),
    confidence: str = "medium",
) -> FindingView:
    """Record something the run concluded, and say so in the trace.

    The finding row and its ``finding_added`` event are written together, for the
    reason every pairing in this codebase is: a conclusion the trace does not
    mention is one the user cannot check.
    """
    async with org_session(org_id) as session:
        await _owned_run(session, run_id=run_id, user_id=None)
        row = Finding(
            org_id=org_id,
            run_id=run_id,
            statement=statement,
            support=[str(item) for item in support],
            confidence=confidence,
        )
        session.add(row)
        await session.flush()
        await write_event(
            session,
            org_id=org_id,
            run_id=run_id,
            event_type="finding_added",
            payload={
                "statement": statement,
                "support": [str(item) for item in support],
                "confidence": confidence,
            },
        )
        return FindingView(
            id=row.id,
            statement=row.statement,
            support=[str(item) for item in row.support],
            confidence=row.confidence,
        )


async def list_events(
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    after: int = 0,
) -> list[RecordedEvent]:
    """This run's trace after ``seq``, once the caller is shown to own the run."""
    async with org_session(org_id) as session:
        await _owned_run(session, run_id=run_id, user_id=user_id)
    return await read_events(org_id=org_id, run_id=run_id, after=after)


# ---------------------------------------------------------------------------
# Resolving a citation (architecture 10.2, B-034)
# ---------------------------------------------------------------------------


async def get_execution(
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    execution_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> ExecutionView:
    """One query this run ran: its statement, its shape, and its masked rows.

    What turns a citation into something a person can check. A finding carries
    ``query_executions.id`` values in ``support`` and, until this existed, there
    was no way to resolve one over HTTP — a reference to evidence nobody could
    open.

    **The execution is read through the run, not beside it.** ``run_id`` is in the
    path and in the WHERE clause, so an execution belonging to a different run —
    including one of somebody else's, in this same organization — is not found
    rather than refused. That composes the ownership check the run already
    carries instead of inventing a second one that could disagree with it.

    Everything returned is safe to show, and each part for its own reason. The
    SQL is this service's own canonical text, not the model's. The rows come
    from ``result_artifacts.sample_rows``, which was **masked on the way in**
    (WP5.2b) — there is no unmasked copy in the platform database to leak. And a
    refused execution has no artifact at all, so what a reader sees for one is
    the violation code and the statement that earned it, which is the honest
    answer to "why is there no result here".
    """
    async with org_session(org_id) as session:
        await _owned_run(session, run_id=run_id, user_id=user_id)
        row = (
            (
                await session.execute(
                    select(QueryExecution).where(
                        QueryExecution.id == execution_id,
                        QueryExecution.run_id == run_id,
                    )
                )
            )
            .scalars()
            .one_or_none()
        )
        if row is None:
            raise NotFoundError("No such execution on this run")

        artifact = (
            (
                await session.execute(
                    select(ResultArtifact).where(ResultArtifact.query_execution_id == row.id)
                )
            )
            .scalars()
            .one_or_none()
        )
        summary: dict[str, object] = dict(artifact.summary) if artifact is not None else {}
        return ExecutionView(
            id=row.id,
            run_id=run_id,
            status=row.status,
            sql=row.sql_text,
            tables=[str(table) for table in row.tables],
            columns=_string_list(summary.get("columns")),
            row_count=row.row_count,
            duration_ms=row.duration_ms,
            violation_code=row.violation_code,
            error=row.error,
            sensitive_accessed=row.sensitive_accessed,
            masked_columns=_string_list(summary.get("masked_columns")),
            sample_rows=[list(sample) for sample in artifact.sample_rows]
            if artifact is not None
            else [],
            truncated=bool(artifact.truncated) if artifact is not None else False,
            created_at=row.created_at,
        )


def _string_list(value: object) -> list[str]:
    """A JSONB list, as strings — and anything else as nothing.

    ``summary`` is written by the DAL and read here, so its keys are known but
    its value types are whatever survived a round trip through JSONB. An
    artifact written by an older revision, or none at all, must leave a reader
    with an empty list rather than an exception.
    """
    if not isinstance(value, list):
        return []
    return [str(item) for item in cast("list[object]", value)]
