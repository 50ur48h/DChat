"""Conversation and run routes (architecture Part 10.2).

Every route here is open to every role: asking questions and reading your own
traces is what the role matrix in 6.2 grants a Reader. What a Reader cannot do is
see somebody else's conversation, and that is not a role check at all — it is the
ownership check in ``runs/service.py``, which answers 404 rather than 403 so a
member cannot use the difference to discover that a conversation exists.

``POST …/messages`` answers **202** and a run id, not an answer. The question has
been accepted and recorded; the work has not been done. That is true today for a
blunt reason — WP7.2 brings the planner and nothing yet moves a run out of
``queued`` — and it stays true afterwards, because a research run takes longer
than a request should. The client polls ``GET …/runs/{id}`` for status and
``GET …/runs/{id}/events`` for the trace; Phase 8 turns the second into SSE over
the same rows, which is why the poll response already carries ``last_seq``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from dataagent.auth.context import RequestContext
from dataagent.auth.guards import require_member
from dataagent.runs import service
from dataagent.runs.service import NotFoundError

router = APIRouter(prefix="/v1", tags=["conversations"])

ConversationId = Annotated[uuid.UUID, Path(description="Conversation within this organization")]
RunId = Annotated[uuid.UUID, Path(description="Run within this organization")]


class CreateConversationIn(BaseModel):
    title: str | None = Field(
        default=None,
        max_length=300,
        description="Optional. Left unset, the first question becomes the title.",
    )


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str | None
    created_at: datetime
    message_count: int = 0
    last_run_id: uuid.UUID | None = None


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
    cost_estimate: Decimal | None = Field(
        default=None, description="Null means unpriced, never free."
    )
    model_usage: dict[str, object] = Field(default_factory=dict[str, object])


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
    return _conversation_out(
        await service.create_conversation(
            org_id=context.org_id, user_id=context.user_id, title=body.title
        )
    )


@router.get(
    "/orgs/{org_id}/conversations",
    response_model=list[ConversationOut],
    summary="Your conversations in this organization",
)
async def list_conversations(
    context: Annotated[RequestContext, Depends(require_member)],
) -> list[ConversationOut]:
    return [
        _conversation_out(view)
        for view in await service.list_conversations(org_id=context.org_id, user_id=context.user_id)
    ]


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
            )
            for finding in view.findings
        ],
        started_at=view.started_at,
        finished_at=view.finished_at,
        failure_reason=view.failure_reason,
        cost_estimate=view.cost_estimate,
        model_usage=view.model_usage,
    )


@router.get(
    "/orgs/{org_id}/runs/{run_id}/events",
    response_model=EventsOut,
    summary="This run's trace, after a sequence number",
)
async def get_run_events(
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
) -> EventsOut:
    """Poll now, SSE in Phase 8 — the same durable rows either way (arch 10.3)."""
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
