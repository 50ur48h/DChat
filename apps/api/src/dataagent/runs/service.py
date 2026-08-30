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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final, cast

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dataagent.db.models import (
    AgentRun,
    Conversation,
    DataSource,
    Finding,
    Message,
    Organization,
    QueryExecution,
    ResultArtifact,
    UsageLedger,
)
from dataagent.runs.events import EventType, RecordedEvent, read_events, write_event
from dataagent.tenancy.session import org_session

__all__ = [
    "ConversationView",
    "ExecutionView",
    "InvalidTransitionError",
    "MessageView",
    "NotFoundError",
    "PriorTurn",
    "RunView",
    "add_finding",
    "conversation_history",
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
    #: When this thread was put away, or None while it is in the list
    #: (**D-039**). Archiving hides it; it never removes the runs, the events or
    #: the executions underneath, which is why the field is a timestamp a reader
    #: can ask "when" of rather than a flag.
    archived_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MessageView:
    id: uuid.UUID
    role: str
    content: str
    run_id: uuid.UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PriorTurn:
    """An earlier question in this thread, and what it was answered with.

    Deliberately not ``agent.context.HistoryTurn``, which is the same two fields
    wearing a different hat: that one carries the prompt's framing and its
    clipping rules, and this one is a row read out of the database. Keeping them
    apart is what stops a change to how a thread *reads* from becoming a change
    to what a thread *is* — and it keeps the import direction one-way, since
    `agent` imports `runs` and never the reverse.

    ``answer`` is None for a run that has not produced one: still going, failed,
    or interrupted by a restart.
    """

    run_id: uuid.UUID
    question: str
    answer: str | None


@dataclass(frozen=True, slots=True)
class FindingView:
    id: uuid.UUID
    statement: str
    #: ``query_executions.id`` values — the citation an answer is walked back
    #: through.
    support: list[str]
    confidence: str
    #: True when the composed answer rests on this finding (WP9.2). The card
    #: shows the cited ones as evidence and leaves the rest in the trace, where
    #: they are the investigation's working rather than its conclusion.
    cited: bool = False


#: The budget dimensions a waiting person is shown. **Steps, time and queries —
#: never tokens or model calls.** Those two are spend, and an organization can
#: switch spend off (**D-066**); a progress strip that reported token counts
#: would hand back through one door what the other was closed to prevent. They
#: are also the two a reader cannot act on: nobody waiting for an answer is
#: helped by knowing it has used 80,268 tokens.
PROGRESS_DIMENSIONS: Final = ("iterations", "queries", "wall_seconds")


def _progress_of(budget: dict[str, object] | None) -> dict[str, object]:
    """How far through its allowance a run is, as counters and ceilings.

    Reshaped rather than passed through: `agent_runs.budget` is the loop's own
    accounting object, and putting it on the wire whole would make every field
    it ever grows a public one.
    """
    if not budget:
        return {}
    limits = budget.get("limits")
    ceilings = cast(dict[str, object], limits) if isinstance(limits, dict) else {}
    used: dict[str, object] = {}
    allowed: dict[str, object] = {}
    for name in PROGRESS_DIMENSIONS:
        # `elapsed_seconds` is what the loop calls the wall clock it has spent,
        # against a ceiling it calls `wall_seconds`.
        key = "elapsed_seconds" if name == "wall_seconds" else name
        if key in budget:
            used[name] = budget[key]
        if name in ceilings:
            allowed[name] = ceilings[name]
    return {"used": used, "limits": allowed} if used or allowed else {}


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
    #: How far through its allowance this run is — the counters and the ceilings
    #: together, as `agent_runs.budget` already stores them (**B-177**).
    #:
    #: **On the wire so a waiting person can be told where they are**, which is
    #: the whole of it: a compound question now runs to five and a half minutes,
    #: and five minutes with no sense of progress is worse than four with one.
    #: Counters only — never a prediction. What finishes a run is a model
    #: deciding it has enough, and nothing here knows when that will be.
    progress: dict[str, object] = field(default_factory=dict[str, object])
    #: What this answer does not establish. Rendered beside the answer, never
    #: instead of it, and empty is both the common case and a good one.
    limitations: list[str] = field(default_factory=list[str])
    #: One line on how the answer was reached, for a reader who will not open the
    #: SQL — architecture 4.2's fourth part of an answer (**B-100**). Empty for
    #: runs composed before it was stored, and for runs that never composed.
    method: str = ""
    #: Which semantic definitions governed this answer, and how many there were
    #: to match (**B-087**). Both, because they mean nothing apart: an empty list
    #: beside `0` is an organization that has defined nothing, and an empty list
    #: beside `18` is a question that named none of them — and for three gate
    #: walks in a row those two were indistinguishable, so a naming problem read
    #: as a broken feature every time.
    definitions_applied: list[str] = field(default_factory=list[str])
    definitions_available: int = 0
    #: `answered` | `partly` | `refused` (**D-044**). `None` for runs that ended
    #: before revision 0030, and for runs that have not ended.
    #:
    #: **Separate from `status`, and not a boolean.** WP7.2b's rule is that a run
    #: which could not answer *completes* — `failed` is reserved for the platform
    #: breaking — so `completed` alone cannot tell the three apart. And a question
    #: can be half-answered (**B-134**), which no boolean can say.
    state: str | None = None
    #: What the run could not answer, in the composer's words. Non-empty exactly
    #: when `state` is `partly`, so the card always has the missing half to name.
    unanswered: str = ""
    #: The chart this answer carries, or the reason it carries none (WP11.1).
    #: `{"spec": …}` or `{"declined": …, "code": …}`; None when no chart was
    #: asked for. A refusal is here rather than in `limitations` because that
    #: list is about whether the answer is *true*, and a missing picture says
    #: nothing about that — the card renders this where the chart would be.
    chart: dict[str, object] | None = None


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
    """Start a thread, stamped with the database it will be about.

    **The stamp is what keeps D-022 whole while D-045 moves the choice.** A
    caller that names no source gets the organization's active one written onto
    the row *here*, at creation, rather than resolved at each run — so the thread
    still records the source it used, which was D-022's whole point, and an Admin
    who changes the organization's choice tomorrow does not silently re-point
    conversations that already ran. Two answers in one thread cannot come from
    two databases.

    A caller that *does* name a source still gets it, unchanged. Nothing here
    overrides an explicit choice; this fills a blank.
    """
    async with org_session(org_id) as session:
        if data_source_id is None:
            data_source_id = (
                await session.execute(
                    select(Organization.active_data_source_id).where(Organization.id == org_id)
                )
            ).scalar_one_or_none()
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


async def list_conversations(
    *, org_id: uuid.UUID, user_id: uuid.UUID, archived: bool = False
) -> list[ConversationView]:
    """This caller's conversations, newest first, with enough to render a list.

    ``archived`` selects **one** of the two lists rather than widening the first
    into both: an archived thread that stayed in the default list would make the
    button appear broken, and a screen that mixed them would need a badge on
    every row to say which is which. The archived ones are a place you go, not
    noise you filter.
    """
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
            .where(
                Conversation.archived_at.is_not(None)
                if archived
                else Conversation.archived_at.is_(None)
            )
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
                archived_at=conversation.archived_at,
            )
            for conversation, count, source_name in rows.all()
        ]


async def rename_conversation(
    *, org_id: uuid.UUID, user_id: uuid.UUID, conversation_id: uuid.UUID, title: str
) -> ConversationView:
    """Give a thread a name its owner will recognise later (plan WP11.2).

    Only the owner's own, through `_owned_conversation` — the same rule the rest
    of this module follows, and the reason a colleague's thread is a 404 even to
    an Admin (**B-037**).

    The title is what the list shows, so an empty one is stored as NULL rather
    than as a blank string: the list already renders a thread with no title by
    falling back to its first question, and a row of empty space would look like
    a rendering fault instead of a thread nobody named.
    """
    cleaned = title.strip()
    async with org_session(org_id) as session:
        row = await _owned_conversation(session, conversation_id=conversation_id, user_id=user_id)
        row.title = cleaned or None
        await session.flush()
    return await get_conversation(org_id=org_id, user_id=user_id, conversation_id=conversation_id)


async def set_conversation_archived(
    *, org_id: uuid.UUID, user_id: uuid.UUID, conversation_id: uuid.UUID, archived: bool
) -> ConversationView:
    """Put a thread away, or bring it back (**D-039**).

    **Not a delete, and the screen says so.** A conversation is the root of its
    runs, their events, their findings and their query executions — the trace
    architecture 0.2.4 makes durable and `agent_events` holds append-only by
    grant. Removing that from a list screen would destroy the evidence behind
    answers somebody may already have acted on, at the surface where a misclick
    is cheapest. True erasure is Phase 12's retention story: every table, a
    receipt, a window.

    Idempotent in both directions. Archiving an archived thread keeps the
    original timestamp rather than moving it, because *when it was put away* is
    the question the column exists to answer and a repeated call is not a new
    event.
    """
    async with org_session(org_id) as session:
        row = await _owned_conversation(session, conversation_id=conversation_id, user_id=user_id)
        if archived and row.archived_at is None:
            row.archived_at = datetime.now(UTC)
        elif not archived:
            row.archived_at = None
        await session.flush()
    return await get_conversation(org_id=org_id, user_id=user_id, conversation_id=conversation_id)


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
            archived_at=row.archived_at,
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
    grounding = _grounding(run.state)
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
                cited=finding.cited,
            )
            for finding in findings
        ],
        state=run.outcome_state,
        unanswered=run.unanswered or "",
        started_at=run.started_at,
        finished_at=run.finished_at,
        failure_reason=run.failure_reason,
        cost_estimate=run.cost_estimate,
        progress=_progress_of(run.budget),
        model_usage=dict(run.model_usage),
        limitations=[str(note) for note in run.limitations],
        method=run.method or "",
        chart=dict(run.chart) if run.chart else None,
        # Read off the run's own persisted state rather than recomputed: what
        # matters is what governed *this* run, and re-matching now would answer
        # with today's definitions about yesterday's answer.
        definitions_applied=grounding[0],
        definitions_available=grounding[1],
    )


def _grounding(state: object) -> tuple[list[str], int]:
    """The definitions a run applied, and how many it could have (**B-087**).

    Defensive about shape because `state` is a JSON column written by a model
    of the loop that has changed before and will again: a run recorded before
    this field existed must render as "nothing to say", never as an error on a
    page whose whole job is explaining what happened.
    """
    if not isinstance(state, dict):
        return [], 0
    stored = cast("dict[str, object]", state)
    applied = stored.get("applied_definitions")
    available = stored.get("definitions_available")
    names = (
        [str(name) for name in cast("list[object]", applied)] if isinstance(applied, list) else []
    )
    return names, available if isinstance(available, int) else 0


async def get_run(
    *, org_id: uuid.UUID, run_id: uuid.UUID, user_id: uuid.UUID | None = None
) -> RunView:
    async with org_session(org_id) as session:
        return await _run_view(session, await _owned_run(session, run_id=run_id, user_id=user_id))


async def list_conversation_runs(
    *, org_id: uuid.UUID, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> list[RunView]:
    """Every run in this thread, oldest first (**B-106**).

    **The screen needs all of them, and used to hold one.** The conversation
    rendered a single answer card for the newest run, so every earlier answer
    lost its chart, its method line, its limitations, its findings, its evidence
    controls and its trace the moment a second question was asked — durable rows
    with no route to them. The card is now the assistant turn, and a turn needs
    its run.

    **One request rather than one per message.** The alternative was for the
    client to call `GET …/runs/{id}` for every assistant message it found, which
    is a thread-length number of round trips to render a screen, growing with the
    thing a person is most likely to keep using.

    Ownership is checked once on the conversation, exactly as `list_messages`
    does: a thread belongs to the person who started it, its runs are not a
    separate grant, and re-checking each run against the same user would only be
    a slower way to reach the same answer.
    """
    async with org_session(org_id) as session:
        await _owned_conversation(session, conversation_id=conversation_id, user_id=user_id)
        rows = (
            (
                await session.execute(
                    select(AgentRun)
                    # `(created_at, id)` as a pair, for the reason
                    # `conversation_history` gives: two runs created in the same
                    # microsecond still need an order rather than a coin toss.
                    .where(AgentRun.conversation_id == conversation_id)
                    .order_by(AgentRun.created_at, AgentRun.id)
                )
            )
            .scalars()
            .all()
        )
        return [await _run_view(session, row) for row in rows]


async def conversation_history(
    *, org_id: uuid.UUID, run_id: uuid.UUID, turns: int
) -> list[PriorTurn]:
    """The turns this run follows, oldest first, capped at ``turns``.

    Written for the agent (**D-029**, B-064), which until this existed answered
    every question as though it were the first one in the thread.

    Four properties, each of which has a test:

    **Only what came before.** A conversation can hold a run created *after* this
    one — somebody asks a second question while the first is still running — and
    that run is not history, it is the future. The comparison is on
    ``(created_at, id)`` as a pair, so two runs created in the same microsecond
    still have an order rather than a coin toss.

    **Never this run's own question.** ``post_message`` writes the message and
    the run together, so by the time the runner reads the thread the current
    question is already in ``messages``. Rendering it as history would show the
    model its own question twice, once framed as something already said.

    **Newest first in the query, oldest first in the result.** The index on
    ``(org_id, conversation_id, created_at DESC)`` makes "the last three" cheap;
    the prompt wants them in the order they happened.

    **Scoped by the run, not by a caller.** ``user_id=None`` for the same reason
    ``transition`` uses it — the agent is not a member of anything, and RLS is
    what scopes it to the organization. A thread is read only through a run that
    is already being executed inside it.
    """
    if turns <= 0:
        return []

    async with org_session(org_id) as session:
        run = await _owned_run(session, run_id=run_id, user_id=None)
        earlier = (
            (
                await session.execute(
                    select(AgentRun)
                    .where(
                        AgentRun.conversation_id == run.conversation_id,
                        or_(
                            AgentRun.created_at < run.created_at,
                            and_(
                                AgentRun.created_at == run.created_at,
                                AgentRun.id < run.id,
                            ),
                        ),
                    )
                    .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
                    .limit(turns)
                )
            )
            .scalars()
            .all()
        )
        if not earlier:
            return []

        # Ascending, so a run that composed twice — the critic's one re-entry —
        # leaves the answer a person was actually shown as the surviving value.
        replies = (
            await session.execute(
                select(Message.run_id, Message.content)
                .where(
                    Message.run_id.in_([row.id for row in earlier]),
                    Message.role == ROLE_ASSISTANT,
                )
                .order_by(Message.created_at, Message.id)
            )
        ).all()
        answers: dict[uuid.UUID, str] = {}
        for reply_run_id, content in replies:
            if reply_run_id is not None:
                answers[reply_run_id] = content
        return [
            PriorTurn(run_id=row.id, question=row.question, answer=answers.get(row.id))
            for row in reversed(earlier)
        ]


async def _roll_up_usage(session: AsyncSession, run: AgentRun) -> None:
    """Fill ``cost_estimate`` and ``model_usage`` from this run's ledger rows.

    **Both columns have existed since revision 0012 and nothing ever wrote
    them.** `model_usage`'s own comment calls it *"a rollup for the trace UI"*;
    the rollup was never built, so every run in every database carries `NULL`
    and `{}`, the API returns them, and no screen could show what a question
    cost. `usage_ledger` had the answer the whole time — one row per provider
    call, with role, model, tokens and `cost_usd` — and `budget.spent_on_run`
    already sums it to enforce the per-run ceiling. The ceiling worked off data
    the run's own row never received.

    **`NULL` means unpriced, never free**, which is the rule both columns were
    given at birth and the one this must not quietly soften. A run holding any
    call the price table does not cover gets `NULL` rather than a total that
    silently omits it — an understated number is worse than an absent one,
    because it reads as the answer. A run with no ledger rows at all also gets
    `NULL`: nothing was recorded, which is not the same claim as nothing was
    spent, and the two are indistinguishable from here.

    The breakdown is kept whatever the total does, so `NULL` never means "we
    know nothing" — `unpriced_calls` says exactly how many calls could not be
    priced, and `by_model` still names them.
    """
    rows = (
        await session.execute(
            select(
                UsageLedger.model,
                UsageLedger.role,
                func.count().label("calls"),
                func.coalesce(func.sum(UsageLedger.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(UsageLedger.output_tokens), 0).label("output_tokens"),
                func.sum(UsageLedger.cost_usd).label("cost_usd"),
                func.count().filter(UsageLedger.cost_usd.is_(None)).label("unpriced"),
                # Cached input is summed over the rows that reported one, and
                # the count of those rows travels beside it — `0` and "nobody
                # said" are different claims and a bare sum conflates them.
                func.sum(UsageLedger.cached_input_tokens).label("cached_input_tokens"),
                func.count()
                .filter(UsageLedger.cached_input_tokens.isnot(None))
                .label("cached_reported"),
                func.count().filter(UsageLedger.tokens_estimated.is_(True)).label("estimated"),
            )
            .where(UsageLedger.run_id == run.id)
            .group_by(UsageLedger.model, UsageLedger.role)
            .order_by(UsageLedger.model, UsageLedger.role)
        )
    ).all()

    by_model: list[dict[str, object]] = []
    calls = inputs = outputs = unpriced = 0
    cached = cached_reported = estimated = 0
    total = Decimal("0")
    for row in rows:
        calls += row.calls
        inputs += int(row.input_tokens)
        outputs += int(row.output_tokens)
        unpriced += row.unpriced
        cached += int(row.cached_input_tokens or 0)
        cached_reported += row.cached_reported
        estimated += row.estimated
        if row.cost_usd is not None:
            total += row.cost_usd
        by_model.append(
            {
                "model": row.model,
                "role": row.role,
                "calls": row.calls,
                "input_tokens": int(row.input_tokens),
                "output_tokens": int(row.output_tokens),
                "cached_input_tokens": (
                    None if row.cached_reported == 0 else int(row.cached_input_tokens or 0)
                ),
                "estimated_calls": row.estimated,
                # A string, because JSONB has no decimal and a float would round
                # a price. The reader formats it; nothing here does arithmetic on
                # it again.
                "cost_usd": None if row.cost_usd is None else str(row.cost_usd),
            }
        )

    run.model_usage = {
        "calls": calls,
        "input_tokens": inputs,
        "output_tokens": outputs,
        # **Recorded, not discounted** (revision 0034). `cost_estimate` prices
        # the whole input at the full rate, and the provider bills the cached
        # part at less — so this is the measurement of how far that overstates,
        # kept beside the total rather than folded into it. None means no call
        # reported a cached share, which is not the same as none being cached.
        "cached_input_tokens": None if cached_reported == 0 else cached,
        "unpriced_calls": unpriced,
        # How many of these calls were counted by us rather than by the
        # provider. A total that mixes measured and estimated tokens and does
        # not say so is the shape this project keeps filing.
        "estimated_calls": estimated,
        "by_model": by_model,
    }
    run.cost_estimate = None if (unpriced or not rows) else total


async def transition(
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    status: str,
    failure_reason: str | None = None,
    state: str | None = None,
    unanswered: str = "",
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
            # **Recorded beside `failure_reason` because it is the same kind of
            # fact**: what this ending was, written when it happened (**B-133**,
            # **D-044**). It stays in `totals` as well — the trace is the record of
            # the run — but the trace is not something a screen can read per run
            # without a query each.
            run.outcome_state = state
            # Empty becomes NULL, because the CHECK that makes `partly` impossible
            # without a named part treats '' and NULL alike and a column that holds
            # both for the same meaning is one nobody can query.
            run.unanswered = unanswered or None
            # Written at the ending, for the same reason `outcome_state` is: it
            # is a fact about the run that is true once and never again, and a
            # screen should not have to re-derive it from the ledger per row.
            await _roll_up_usage(session, run)
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


def _chart_outcome(chart: Mapping[str, object] | None) -> str | None:
    """What the trace says happened to the chart: nothing, a spec, or which rule
    refused it."""
    if chart is None:
        return None
    if chart.get("spec") is not None:
        return "spec"
    code = chart.get("code")
    return str(code) if code else "declined"


async def record_answer(
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    content: str,
    limitations: Sequence[str] = (),
    chart: Mapping[str, object] | None = None,
    method: str = "",
    coverage: Mapping[str, object] | None = None,
) -> MessageView:
    """Write the assistant's reply for a run, and what it does not establish.

    Separate from ``transition`` on purpose: composing an answer and declaring the
    run over are two different claims, and WP9's critic runs between them.

    ``limitations`` land on the run rather than inside the message text, so the
    answer card can render them as their own thing — beside the answer, never
    instead of it — and so a reader can tell a hedged sentence the model chose to
    write from a caveat the platform established (WP9.2).

    ``chart`` lands there for the same reason and one more (WP11.1): it holds a
    spec **or** the sentence saying why there is none, and a refusal that had
    nowhere to live would leave a picture silently absent — which looks like a
    broken page rather than a decision. `None` means no chart was asked for.

    ``method`` is the fourth part of architecture 4.2's answer and the last to
    get a home (**B-100**). `composer.method_note` has built it on every run
    since Phase 9 and nothing stored it, so the one line written for a reader who
    will not open the SQL was the one that never reached them. Empty string
    rather than a sentinel: a caller that has nothing to say writes nothing, and
    the column stays NULL.
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
        run.limitations = list(limitations)
        if method:
            run.method = method
        if chart is not None:
            run.chart = dict(chart)
        await session.flush()
        await write_event(
            session,
            org_id=org_id,
            run_id=run_id,
            event_type="answer_composed",
            payload={
                "message_id": str(message.id),
                "length": len(content),
                "limitations": len(limitations),
                # Both outcomes are in the trace, and an absent chart is told
                # apart from one nobody asked for: `null`, `"spec"` or the
                # refusal's own code (B-087's discipline, for pictures).
                "chart": _chart_outcome(chart),
                # **Present whatever it says, including "I could not look."** A
                # run where the period check abstained has to be distinguishable
                # in the trace from one where it ran and passed; without this key
                # the absence of a caveat would mean two different things and a
                # reader would have no way to tell which (B-157, D-059).
                "coverage": dict(coverage) if coverage is not None else None,
            },
        )
        return MessageView(
            id=message.id,
            role=message.role,
            content=message.content,
            run_id=run_id,
            created_at=message.created_at,
        )


async def mark_cited(
    *, org_id: uuid.UUID, run_id: uuid.UUID, executions: Sequence[uuid.UUID | str]
) -> int:
    """Mark the findings this run's answer rests on, and return how many.

    Matched by **shared execution**, not by statement text: the composer
    rephrases what the loop concluded, and a match on wording would lose the link
    exactly when the answer was written well. A finding that cites one of the
    answer's executions is a claim about the same query, which is what makes it
    evidence for the same answer.

    Idempotent, so a re-composed answer after the critic's re-entry re-marks
    rather than accumulating.
    """
    wanted = {str(execution) for execution in executions}
    if not wanted:
        return 0

    async with org_session(org_id) as session:
        rows = (
            (await session.execute(select(Finding).where(Finding.run_id == run_id))).scalars().all()
        )
        marked = 0
        for finding in rows:
            cited = bool(wanted.intersection(str(item) for item in finding.support))
            if finding.cited != cited:
                finding.cited = cited
            marked += int(cited)
        return marked


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
