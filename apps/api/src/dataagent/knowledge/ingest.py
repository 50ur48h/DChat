"""Upload to searchable, and honest about where it stopped (architecture 5.5).

Five steps — store, extract, chunk, write, embed — and the interesting design is
entirely in what happens when one of them fails.

**A document is `indexed` only when it is searchable.** Extraction and embedding
both reach outside the process, so both fail, and a document that silently holds
no chunks looks exactly like one nobody uploaded. `status` and `failure_reason`
are paired by a CHECK constraint (revision 0016) so a failure without a reason
cannot be stored, and the reason is written for the person who uploaded the file
rather than for whoever reads the log.

**The chunks are written before the vectors, and that ordering is the design.**
`embedding` is nullable precisely so this is possible: the text lands and is
immediately searchable *lexically*, and the vectors arrive after a network round
trip that may fail. A run that stores the text and then cannot embed leaves a
document that is half-searchable and says so, which is strictly better than one
that lost its text because a rate limit was in force.

**Re-indexing is delete-and-rewrite, never append.** `seq` is gap-free and
unique per document, so re-chunking a changed document by appending would either
collide or leave orphans from the previous version — text that no longer exists
in the source but still answers questions. The old chunks go first, in the same
transaction that writes the new ones.

**Nothing here decides anything about the content.** It stores what was written
and hands it back framed as reference material; 7.4's threat model assumes a
customer's document may be hostile, and every protection against that lives at
the point of use rather than here.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dataagent.config import Settings, get_settings
from dataagent.db.models import KnowledgeChunk, KnowledgeDocument
from dataagent.knowledge.chunking import chunk_document
from dataagent.knowledge.embeddings import Embedder, embed_texts
from dataagent.knowledge.extract import ExtractionError, extract_text
from dataagent.knowledge.store import DocumentStore
from dataagent.llm.base import LLMError
from dataagent.tenancy.session import org_session

__all__ = [
    "DocumentView",
    "ingest_document",
    "list_documents",
    "reindex_document",
    "unembedded_chunks",
]

STATUS_PENDING = "pending"
STATUS_INDEXED = "indexed"
STATUS_FAILED = "failed"

#: How much of a failure's detail reaches the column, which is rendered to a
#: person. Long enough to name a cause, short enough not to be a stack trace.
REASON_LIMIT = 500


@dataclass(frozen=True, slots=True)
class DocumentView:
    """A document as a caller sees it."""

    id: uuid.UUID
    title: str
    mime: str
    status: str
    chunk_count: int
    failure_reason: str | None
    created_at: datetime
    indexed_at: datetime | None
    #: How many of this document's chunks have a vector. Below `chunk_count`
    #: means the text is searchable lexically and not yet semantically — a real
    #: and temporary state, not an error, and one a documents page should say
    #: out loud rather than round up to "indexed".
    embedded_count: int = 0


async def ingest_document(
    *,
    org_id: uuid.UUID,
    title: str,
    payload: bytes,
    mime: str,
    store: DocumentStore,
    embedder: Embedder | None = None,
    filename: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    settings: Settings | None = None,
) -> DocumentView:
    """Take an upload all the way to searchable, or record why it stopped.

    ``embedder`` is optional and None is a supported mode, not a degraded one:
    the chunks are written and left unembedded, which is exactly the state the
    backfill exists to finish. It is also how a deployment with no embedding key
    still gets lexical search over its documents.
    """
    resolved = settings if settings is not None else get_settings()

    document_id = uuid.uuid4()
    reference = await store.put(org_id=org_id, document_id=document_id, payload=payload, mime=mime)

    async with org_session(org_id) as session:
        session.add(
            KnowledgeDocument(
                id=document_id,
                org_id=org_id,
                title=title[:300],
                blob_path=reference,
                mime=mime,
                status=STATUS_PENDING,
                created_by=actor_user_id,
            )
        )
        await session.flush()

    try:
        text = extract_text(payload, mime=mime, filename=filename)
    except ExtractionError as error:
        # The one failure that is the *file's* fault rather than the platform's,
        # and the message is already written for whoever uploaded it.
        return await _fail(org_id, document_id, str(error))

    return await _index(
        org_id=org_id,
        document_id=document_id,
        text=text,
        embedder=embedder,
        actor_user_id=actor_user_id,
        settings=resolved,
    )


async def reindex_document(
    *,
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    store: DocumentStore,
    embedder: Embedder | None = None,
    actor_user_id: uuid.UUID | None = None,
    settings: Settings | None = None,
) -> DocumentView:
    """Chunk and embed a stored document again, from its original bytes.

    From the **original**, not from the existing chunks: re-embedding what was
    already chunked would carry forward every decision the old chunker made, and
    the reason to re-index is usually that one of those decisions changed.
    """
    resolved = settings if settings is not None else get_settings()

    async with org_session(org_id) as session:
        document = await session.get(KnowledgeDocument, document_id)
        if document is None:
            raise LookupError("No such document")
        reference, mime = document.blob_path, document.mime

    payload = await store.get(org_id=org_id, reference=reference)
    if payload is None:
        return await _fail(
            org_id,
            document_id,
            "The original file is no longer in storage, so it cannot be re-read.",
        )

    try:
        text = extract_text(payload, mime=mime)
    except ExtractionError as error:
        return await _fail(org_id, document_id, str(error))

    return await _index(
        org_id=org_id,
        document_id=document_id,
        text=text,
        embedder=embedder,
        actor_user_id=actor_user_id,
        settings=resolved,
    )


async def _index(
    *,
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    text: str,
    embedder: Embedder | None,
    actor_user_id: uuid.UUID | None,
    settings: Settings,
) -> DocumentView:
    """Chunk, write, embed — in that order, and each step survivable."""
    chunks = chunk_document(text)
    if not chunks:
        return await _fail(org_id, document_id, "This file produced no text to index.")

    # **Delete then write, in one transaction.** `seq` is unique per document, so
    # appending to a re-index would collide; and leaving the old rows would keep
    # answering questions from text the source no longer contains.
    async with org_session(org_id) as session:
        await session.execute(
            delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id)
        )
        for chunk in chunks:
            session.add(
                KnowledgeChunk(
                    org_id=org_id,
                    document_id=document_id,
                    seq=chunk.seq,
                    text_=chunk.text,
                    headings=list(chunk.headings),
                    token_estimate=chunk.token_estimate,
                )
            )
        await session.flush()

    if embedder is not None:
        try:
            vectors = await embed_texts(
                org_id=org_id,
                texts=[chunk.text for chunk in chunks],
                embedder=embedder,
                actor_user_id=actor_user_id,
                settings=settings,
            )
        except LLMError as error:
            # The text is already stored and already lexically searchable, so
            # this is a partial success and is recorded as a failure *with the
            # chunks intact* — the backfill's work list is exactly these rows.
            return await _fail(
                org_id, document_id, f"The text was stored but not embedded: {error}"
            )

        async with org_session(org_id) as session:
            rows = (
                (
                    await session.execute(
                        select(KnowledgeChunk)
                        .where(KnowledgeChunk.document_id == document_id)
                        .order_by(KnowledgeChunk.seq)
                    )
                )
                .scalars()
                .all()
            )
            # Paired by position, which is safe only because `embed_texts`
            # guarantees one vector per input in input order and both sides are
            # ordered by `seq`. That guarantee is asserted there, not assumed
            # here.
            for row, vector in zip(rows, vectors, strict=True):
                row.embedding = list(vector)

    return await _finish(org_id, document_id, len(chunks))


async def _finish(org_id: uuid.UUID, document_id: uuid.UUID, chunk_count: int) -> DocumentView:
    async with org_session(org_id) as session:
        document = await session.get(KnowledgeDocument, document_id)
        if document is None:  # pragma: no cover - it was written moments ago
            raise LookupError("No such document")
        document.status = STATUS_INDEXED
        document.failure_reason = None
        document.chunk_count = chunk_count
        document.indexed_at = datetime.now(UTC)
        await session.flush()
        return await _view(session, document)


async def _fail(org_id: uuid.UUID, document_id: uuid.UUID, reason: str) -> DocumentView:
    """Record the stop, with a reason the CHECK constraint insists on.

    The chunks are **not** removed. A document that extracted and chunked but
    could not embed is lexically searchable right now, and throwing that away to
    make the status tidy would be losing working behaviour to a bookkeeping
    preference.
    """
    async with org_session(org_id) as session:
        document = await session.get(KnowledgeDocument, document_id)
        if document is None:  # pragma: no cover - it was written moments ago
            raise LookupError("No such document")
        document.status = STATUS_FAILED
        document.failure_reason = reason[:REASON_LIMIT]
        document.chunk_count = await _count_chunks(session, document_id)
        await session.flush()
        return await _view(session, document)


async def _count_chunks(session: AsyncSession, document_id: uuid.UUID) -> int:
    total = (
        await session.execute(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.document_id == document_id)
        )
    ).scalar_one()
    return int(total)


async def _view(session: AsyncSession, document: KnowledgeDocument) -> DocumentView:
    embedded = (
        await session.execute(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(
                KnowledgeChunk.document_id == document.id,
                KnowledgeChunk.embedding.isnot(None),
            )
        )
    ).scalar_one()
    return DocumentView(
        id=document.id,
        title=document.title,
        mime=document.mime,
        status=document.status,
        chunk_count=document.chunk_count,
        failure_reason=document.failure_reason,
        created_at=document.created_at,
        indexed_at=document.indexed_at,
        embedded_count=int(embedded),
    )


async def list_documents(*, org_id: uuid.UUID) -> list[DocumentView]:
    """Every document this organization has, newest first."""
    async with org_session(org_id) as session:
        rows = (
            (
                await session.execute(
                    select(KnowledgeDocument).order_by(KnowledgeDocument.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [await _view(session, row) for row in rows]


async def unembedded_chunks(*, org_id: uuid.UUID, limit: int = 500) -> Sequence[uuid.UUID]:
    """The backfill's work list, straight off the partial index (revision 0016)."""
    async with org_session(org_id) as session:
        rows = (
            (
                await session.execute(
                    select(KnowledgeChunk.id)
                    .where(KnowledgeChunk.embedding.is_(None))
                    .order_by(KnowledgeChunk.created_at)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)
