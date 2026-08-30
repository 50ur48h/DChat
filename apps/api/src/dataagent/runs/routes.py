"""Conversation and run routes (architecture Part 10.2).

Every route here is open to every role: asking questions and reading your own
traces is what the role matrix in 6.2 grants a Reader. What a Reader cannot do is
see somebody else's conversation, and that is not a role check at all — it is the
ownership check in ``runs/service.py``, which answers 404 rather than 403 so a
member cannot use the difference to discover that a conversation exists.

``POST …/messages`` answers **202** and a run id, not an answer. The question is
accepted and recorded, and the work happens in a background task (architecture
0.2.4) — a research run takes thirty seconds to four minutes, which is longer
than any request should be held open. The client polls ``GET …/runs/{id}`` for
status and ``GET …/runs/{id}/events`` for the trace; Phase 8 turns the second
into SSE over the same rows, which is why the poll response already carries
``last_seq``.

The route schedules and returns. It does not wait, it does not report whether the
run succeeded, and it does not fail the request when the run does — everything a
run has to say about itself is in ``agent_runs`` and ``agent_events``, the only
channel that outlives the request that started it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from dataagent.agent.scheduler import schedule_run
from dataagent.auth.context import RequestContext
from dataagent.auth.guards import require_member
from dataagent.orgs import service as orgs_service
from dataagent.runs import service
from dataagent.runs.service import NotFoundError
from dataagent.runs.sse import event_stream

router = APIRouter(prefix="/v1", tags=["conversations"])

ConversationId = Annotated[uuid.UUID, Path(description="Conversation within this organization")]
RunId = Annotated[uuid.UUID, Path(description="Run within this organization")]


class CreateConversationIn(BaseModel):
    title: str | None = Field(
        default=None,
        max_length=300,
        description="Optional. Left unset, the first question becomes the title.",
    )
    data_source_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "The database every question in this conversation is answered against "
            "(D-022). Optional: left unset, a run uses the organization's single "
            "data source, and refuses — naming the choices — when there is more "
            "than one."
        ),
    )


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str | None
    created_at: datetime
    message_count: int = 0
    last_run_id: uuid.UUID | None = None
    data_source_id: uuid.UUID | None = None
    data_source_name: str | None = Field(
        default=None,
        description="Null when none was named, or when the source has since been removed.",
    )
    archived_at: datetime | None = Field(
        default=None,
        description=(
            "When this conversation was archived, or null while it is in the "
            "list. Archiving hides a thread; it never removes the runs, events "
            "or query executions underneath it, which stay reachable and "
            "auditable (D-039)."
        ),
    )


class RenameConversationIn(BaseModel):
    title: str = Field(
        max_length=300,
        description=(
            "The new title. Blank clears it, and the list falls back to the "
            "thread's first question."
        ),
    )


class ArchiveConversationIn(BaseModel):
    archived: bool = Field(
        description=(
            "True puts the thread away, false brings it back. Not a delete: "
            "nothing under the conversation is removed (D-039)."
        )
    )


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str = Field(description="user | assistant")
    content: str
    run_id: uuid.UUID | None = Field(
        default=None, description="The run this message belongs to, if any."
    )
    created_at: datetime


class AskIn(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    idempotency_key: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "Required. A client-generated id for this send. Retrying with the same "
            "key returns the run that already exists instead of starting — and "
            "paying for — a second one."
        ),
    )


class AskOut(BaseModel):
    run_id: uuid.UUID
    message_id: uuid.UUID
    status: str = Field(description="Always 'queued' here: the run is accepted, not finished.")
    created: bool = Field(
        description="False when an idempotency key matched an earlier send of this question."
    )


class FindingOut(BaseModel):
    id: uuid.UUID
    statement: str
    support: list[str] = Field(
        default_factory=list[str],
        description="Query execution ids backing this statement — the citation trail.",
    )
    confidence: str = Field(description="high | medium | low")
    cited: bool = Field(
        default=False,
        description=(
            "True when the composed answer rests on this finding. The rest are "
            "the investigation's working and belong in the trace."
        ),
    )


class RunOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    status: str = Field(
        description=(
            "queued | running | validating | completed | interrupted | failed | "
            "budget_exhausted. 'budget_exhausted' is an answer with caveats, not a failure."
        )
    )
    question: str
    answer: str | None = None
    findings: list[FindingOut] = Field(default_factory=list[FindingOut])
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure_reason: str | None = Field(
        default=None, description="Sanitized: names what failed, never an address or a credential."
    )
    state: str | None = Field(
        default=None,
        description=(
            "answered | partly | refused. **Not derivable from `status`**: a run "
            "that could not answer *completes* — `failed` is reserved for the "
            "platform breaking — so `completed` covers all three. And not a "
            "boolean: a question can be half-answered. Derived by the platform "
            "from whether the run produced a verified citation and whether it "
            "named something it could not answer; never chosen by a model. Null "
            "for runs that ended before this was recorded, and for runs that have "
            "not ended."
        ),
    )
    unanswered: str = Field(
        default="",
        description=(
            "The part of the question the run could not answer, in the composer's "
            "words. Non-empty exactly when `state` is 'partly', so a client always "
            "has the missing half to name."
        ),
    )
    cost_estimate: Decimal | None = Field(
        default=None, description="Null means unpriced, never free."
    )
    model_usage: dict[str, object] = Field(default_factory=dict[str, object])
    progress: dict[str, object] = Field(
        default_factory=dict[str, object],
        description=(
            "How far through its allowance this run is: `used` and `limits` over "
            "steps, queries and seconds. **Counters, never a prediction** — what "
            "ends a run is a model deciding it has enough, and nothing here "
            "knows when that will be. Spend dimensions are deliberately absent, "
            "because an organization can switch spend off (D-066)."
        ),
    )
    limitations: list[str] = Field(
        default_factory=list[str],
        description=(
            "What this answer does not establish, in plain words — a ceiling that "
            "stopped the search, a reviewer's warning, a period the data does not "
            "cover. Shown with the answer, never instead of it. Empty is the "
            "common case."
        ),
    )
    method: str = Field(
        default="",
        description=(
            "One line on how the answer was reached — how many queries, over how "
            "many steps, against which tables. Architecture 4.2's fourth part of "
            "an answer, for a reader who will not open the SQL. Built from the "
            "run's own counts, never from a model's account of its reasoning. "
            "Empty for runs composed before it was recorded, and for runs that "
            "never composed an answer."
        ),
    )
    chart: dict[str, object] | None = Field(
        default=None,
        description=(
            "The chart this answer carries, or why it carries none (WP11.1). "
            '`{"spec": …}` is a Vega-Lite spec to render; `{"declined": …, '
            '"code": …}` is a plain sentence for the reader, shown where the '
            "chart would have been. Null means no chart was asked for. It is "
            "deliberately **not** a limitation: that list is about whether the "
            "answer is true, and a missing picture says nothing about that."
        ),
    )
    definitions_applied: list[str] = Field(
        default_factory=list[str],
        description=(
            "The semantic definitions that governed this answer — the ones whose "
            "required filters the critic enforced against the SQL."
        ),
    )
    definitions_available: int = Field(
        default=0,
        description=(
            "How many active definitions this data source had when the question was "
            "asked. Read together with `definitions_applied`: empty beside 0 means "
            "nothing has been defined, empty beside a positive number means the "
            "question named none of them — which is a fact about the wording, not "
            "about the data, and is otherwise invisible."
        ),
    )


class ExecutionOut(BaseModel):
    """One query a run ran — the thing a citation points at (architecture 10.2)."""

    id: uuid.UUID
    run_id: uuid.UUID
    status: str = Field(
        description=(
            "ok | error | refused. 'refused' never reached an engine, so it has no "
            "rows and no duration — and this is the only place it is visible at all."
        )
    )
    sql: str = Field(description="This service's canonical statement, not the model's text.")
    tables: list[str] = Field(default_factory=list[str])
    columns: list[str] = Field(default_factory=list[str])
    row_count: int | None = None
    duration_ms: int | None = None
    violation_code: str | None = Field(
        default=None, description="Set exactly when status is 'refused'."
    )
    error: str | None = Field(
        default=None, description="Sanitized: names what failed, never a value."
    )
    sensitive_accessed: bool = False
    masked_columns: list[str] = Field(
        default_factory=list[str],
        description="Columns a policy masked here. Their values below are already masked.",
    )
    sample_rows: list[list[object]] = Field(
        default_factory=list[list[object]],
        description=(
            "Up to 50 rows, masked on the way in (WP5.2b) — there is no unmasked "
            "copy of these in the platform database."
        ),
    )
    truncated: bool = False
    created_at: datetime


class EventOut(BaseModel):
    seq: int
    type: str
    payload: dict[str, object]
    ts: datetime


class EventsOut(BaseModel):
    run_id: uuid.UUID
    events: list[EventOut]
    last_seq: int = Field(description="Pass back as ?after= to fetch only what has happened since.")


def _not_found(what: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"No such {what} in this organization"
    )


def _conversation_out(view: service.ConversationView) -> ConversationOut:
    return ConversationOut(
        id=view.id,
        title=view.title,
        created_at=view.created_at,
        message_count=view.message_count,
        last_run_id=view.last_run_id,
        data_source_id=view.data_source_id,
        data_source_name=view.data_source_name,
        archived_at=view.archived_at,
    )


def _message_out(view: service.MessageView) -> MessageOut:
    return MessageOut(
        id=view.id,
        role=view.role,
        content=view.content,
        run_id=view.run_id,
        created_at=view.created_at,
    )


@router.post(
    "/orgs/{org_id}/conversations",
    response_model=ConversationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Start a conversation",
)
async def create_conversation(
    body: CreateConversationIn, context: Annotated[RequestContext, Depends(require_member)]
) -> ConversationOut:
    try:
        return _conversation_out(
            await service.create_conversation(
                org_id=context.org_id,
                user_id=context.user_id,
                title=body.title,
                data_source_id=body.data_source_id,
            )
        )
    except NotFoundError as error:
        raise _not_found("data source") from error


@router.get(
    "/orgs/{org_id}/conversations",
    response_model=list[ConversationOut],
    summary="Your conversations in this organization",
)
async def list_conversations(
    context: Annotated[RequestContext, Depends(require_member)],
    archived: Annotated[
        bool,
        Query(
            description=(
                "False (the default) lists the threads in play; true lists the "
                "archived ones. One or the other, never both — an archived "
                "thread left in the default list would make the button look "
                "broken."
            )
        ),
    ] = False,
) -> list[ConversationOut]:
    return [
        _conversation_out(view)
        for view in await service.list_conversations(
            org_id=context.org_id, user_id=context.user_id, archived=archived
        )
    ]


@router.patch(
    "/orgs/{org_id}/conversations/{conversation_id}",
    response_model=ConversationOut,
    summary="Rename a conversation",
)
async def rename_conversation(
    body: RenameConversationIn,
    context: Annotated[RequestContext, Depends(require_member)],
    conversation_id: ConversationId,
) -> ConversationOut:
    """Retitle your own thread.

    Any member may be here — architecture 6.2 gives every role its own
    conversations — and `service` enforces that "your own" is literal: a
    colleague's thread is a 404 even to an Admin (**B-037**).
    """
    try:
        return _conversation_out(
            await service.rename_conversation(
                org_id=context.org_id,
                user_id=context.user_id,
                conversation_id=conversation_id,
                title=body.title,
            )
        )
    except NotFoundError as error:
        raise _not_found("conversation") from error


@router.post(
    "/orgs/{org_id}/conversations/{conversation_id}/archive",
    response_model=ConversationOut,
    summary="Archive a conversation, or bring it back",
)
async def archive_conversation(
    body: ArchiveConversationIn,
    context: Annotated[RequestContext, Depends(require_member)],
    conversation_id: ConversationId,
) -> ConversationOut:
    """Put a thread away without destroying what it produced (**D-039**).

    A POST rather than a DELETE, and the noun in the path says `archive` rather
    than the method implying removal — because nothing is removed. The runs,
    their events, their findings and their query executions all stay exactly
    where they were, which is what makes the trace worth having.

    It is also the honest shape for the reverse: `archived: false` brings the
    thread back, and there is no such thing as an un-DELETE.
    """
    try:
        return _conversation_out(
            await service.set_conversation_archived(
                org_id=context.org_id,
                user_id=context.user_id,
                conversation_id=conversation_id,
                archived=body.archived,
            )
        )
    except NotFoundError as error:
        raise _not_found("conversation") from error


@router.get(
    "/orgs/{org_id}/conversations/{conversation_id}",
    response_model=ConversationOut,
    summary="One conversation",
)
async def get_conversation(
    context: Annotated[RequestContext, Depends(require_member)], conversation_id: ConversationId
) -> ConversationOut:
    try:
        return _conversation_out(
            await service.get_conversation(
                org_id=context.org_id,
                user_id=context.user_id,
                conversation_id=conversation_id,
            )
        )
    except NotFoundError as error:
        raise _not_found("conversation") from error


@router.get(
    "/orgs/{org_id}/conversations/{conversation_id}/messages",
    response_model=list[MessageOut],
    summary="Everything said in this conversation, oldest first",
)
async def list_messages(
    context: Annotated[RequestContext, Depends(require_member)], conversation_id: ConversationId
) -> list[MessageOut]:
    try:
        return [
            _message_out(view)
            for view in await service.list_messages(
                org_id=context.org_id,
                user_id=context.user_id,
                conversation_id=conversation_id,
            )
        ]
    except NotFoundError as error:
        raise _not_found("conversation") from error


@router.post(
    "/orgs/{org_id}/conversations/{conversation_id}/messages",
    response_model=AskOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ask a question; queues the run that will answer it",
)
async def post_message(
    body: AskIn,
    context: Annotated[RequestContext, Depends(require_member)],
    conversation_id: ConversationId,
) -> AskOut:
    """202, because the question is accepted rather than answered.

    A repeat with the same ``idempotency_key`` answers 202 as well, with the run
    that already exists and ``created: false``. Same question, same run — which is
    what the client asked for by sending the key.
    """
    try:
        result = await service.post_message(
            org_id=context.org_id,
            user_id=context.user_id,
            conversation_id=conversation_id,
            content=body.content,
            idempotency_key=body.idempotency_key,
        )
    except NotFoundError as error:
        raise _not_found("conversation") from error

    if result.created:
        # Only for a question that is new. A replayed idempotency key already has
        # a run — scheduling again would answer it twice and bill for both, which
        # is the whole thing the key exists to prevent.
        await schedule_run(
            org_id=context.org_id,
            run_id=result.run_id,
            actor_user_id=context.user_id,
            role=context.role,
            # The conversation's own choice (D-022). None means it named none,
            # and the scheduler resolves or refuses — which is why this is
            # passed through rather than defaulted to anything here.
            data_source_id=result.data_source_id,
        )
    return AskOut(
        run_id=result.run_id,
        message_id=result.message_id,
        status=service.STATUS_QUEUED,
        created=result.created,
    )


@router.get(
    "/orgs/{org_id}/runs/{run_id}",
    response_model=RunOut,
    summary="A run: status, answer and findings",
)
async def get_run(
    context: Annotated[RequestContext, Depends(require_member)], run_id: RunId
) -> RunOut:
    try:
        view = await service.get_run(org_id=context.org_id, run_id=run_id, user_id=context.user_id)
    except NotFoundError as error:
        raise _not_found("run") from error
    return run_out(view, show_cost=await orgs_service.show_run_cost(context.org_id))


def run_out(view: service.RunView, *, show_cost: bool = True) -> RunOut:
    """One run, as the API states it.

    Shared by the single-run route and the thread's list (**B-106**) so the two
    cannot drift: a field added to one and not the other would make an answer
    look different depending on which request fetched it, and the thread is the
    place a reader compares two answers side by side.

    **`show_cost=False` withholds spend rather than hiding it** (D-066). The
    organization's switch is enforced here, where the bytes are chosen, and not
    in the browser: a screen that merely declines to render `cost_estimate` has
    still sent it, and anyone who opens the network tab reads what the switch
    was set to conceal. The web needs no change for this — `Cost` already
    renders nothing when there are no calls and no total.
    """
    return RunOut(
        id=view.id,
        conversation_id=view.conversation_id,
        status=view.status,
        question=view.question,
        answer=view.answer,
        findings=[
            FindingOut(
                id=finding.id,
                statement=finding.statement,
                support=finding.support,
                confidence=finding.confidence,
                cited=finding.cited,
            )
            for finding in view.findings
        ],
        state=view.state,
        unanswered=view.unanswered,
        started_at=view.started_at,
        finished_at=view.finished_at,
        failure_reason=view.failure_reason,
        # Not gated on `show_cost`: progress is steps and seconds, and the two
        # dimensions that are spend never reach it.
        progress=view.progress,
        cost_estimate=view.cost_estimate if show_cost else None,
        model_usage=view.model_usage if show_cost else {},
        limitations=view.limitations,
        method=view.method,
        chart=view.chart,
        definitions_applied=view.definitions_applied,
        definitions_available=view.definitions_available,
    )


@router.get(
    "/orgs/{org_id}/conversations/{conversation_id}/runs",
    response_model=list[RunOut],
    summary="Every run in this conversation, oldest first",
)
async def list_conversation_runs(
    context: Annotated[RequestContext, Depends(require_member)],
    conversation_id: ConversationId,
) -> list[RunOut]:
    """What the thread needs to render an answer as more than its words (**B-106**).

    The screen used to hold one run and render one answer card, so every answer
    but the newest lost its chart, its method, its findings, its evidence and its
    trace. One request for the thread, rather than one per assistant message.
    """
    try:
        views = await service.list_conversation_runs(
            org_id=context.org_id,
            user_id=context.user_id,
            conversation_id=conversation_id,
        )
    except NotFoundError as error:
        raise _not_found("conversation") from error
    show_cost = await orgs_service.show_run_cost(context.org_id)
    return [run_out(view, show_cost=show_cost) for view in views]


@router.get(
    "/orgs/{org_id}/runs/{run_id}/executions/{execution_id}",
    response_model=ExecutionOut,
    summary="The query behind a citation: sanitized SQL and masked rows",
)
async def get_execution(
    context: Annotated[RequestContext, Depends(require_member)],
    run_id: RunId,
    execution_id: Annotated[uuid.UUID, Path(description="An execution produced by this run")],
) -> ExecutionOut:
    """Resolve one of a finding's ``support`` ids into evidence a person can read.

    The execution is looked up **through** the run, so one belonging to another
    run is not found rather than refused — the run's own ownership check is the
    only one, and there is no second rule here that could drift away from it.
    """
    try:
        view = await service.get_execution(
            org_id=context.org_id,
            run_id=run_id,
            execution_id=execution_id,
            user_id=context.user_id,
        )
    except NotFoundError as error:
        raise _not_found("execution") from error
    return ExecutionOut(
        id=view.id,
        run_id=view.run_id,
        status=view.status,
        sql=view.sql,
        tables=view.tables,
        columns=view.columns,
        row_count=view.row_count,
        duration_ms=view.duration_ms,
        violation_code=view.violation_code,
        error=view.error,
        sensitive_accessed=view.sensitive_accessed,
        masked_columns=view.masked_columns,
        sample_rows=view.sample_rows,
        truncated=view.truncated,
        created_at=view.created_at,
    )


@router.get(
    "/orgs/{org_id}/runs/{run_id}/events",
    response_model=EventsOut,
    summary="This run's trace, after a sequence number — JSON, or SSE on request",
)
async def get_run_events(
    request: Request,
    context: Annotated[RequestContext, Depends(require_member)],
    run_id: RunId,
    after: Annotated[
        int,
        Query(
            ge=0,
            description=(
                "Return only events after this sequence number. 0, the default, is "
                "the whole trace from the beginning."
            ),
        ),
    ] = 0,
) -> EventsOut | StreamingResponse:
    """One URL, two deliveries of the same durable rows (architecture 10.2, 10.3).

    `Accept: text/event-stream` streams the trace and keeps streaming until the
    run ends; anything else answers once with JSON. **One route rather than two**
    because 10.2 lists one, and because a separate streaming URL would be a
    second contract that could drift from the poll — they read the same rows
    through the same function, and that is worth keeping structurally true.

    Ownership is checked before a stream begins, so an unauthorised caller gets
    a 404 rather than an empty stream that looks like a run with no trace.
    """
    if "text/event-stream" in request.headers.get("accept", ""):
        try:
            # Awaited here rather than inside the generator: an exception raised
            # after streaming has begun cannot become a status code, and a 404
            # delivered as an empty 200 stream is not a refusal anyone can see.
            await service.list_events(
                org_id=context.org_id, run_id=run_id, user_id=context.user_id, after=after
            )
        except NotFoundError as error:
            raise _not_found("run") from error
        return StreamingResponse(
            event_stream(
                org_id=context.org_id,
                run_id=run_id,
                user_id=context.user_id,
                after=after,
                last_event_id=request.headers.get("last-event-id"),
            ),
            media_type="text/event-stream",
            headers={
                # Proxies buffer by default, which turns a live trace into one
                # burst at the end; both headers are how you ask them not to.
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
    try:
        events = await service.list_events(
            org_id=context.org_id, run_id=run_id, user_id=context.user_id, after=after
        )
    except NotFoundError as error:
        raise _not_found("run") from error
    return EventsOut(
        run_id=run_id,
        events=[
            EventOut(seq=event.seq, type=event.type, payload=event.payload, ts=event.ts)
            for event in events
        ],
        # The caller's own `after` when nothing new arrived, so a poll loop that
        # blindly passes this back does not rewind to the start of the trace.
        last_seq=events[-1].seq if events else after,
    )
