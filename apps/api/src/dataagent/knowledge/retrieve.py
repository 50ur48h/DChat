"""Finding the passage that answers "what does this mean here" (arch 5.5).

Hybrid, in the sense 5.5 calls *poor-man's*: a vector search and a Postgres
full-text search, run separately and merged. Not because hybrid is fashionable
but because the two fail in opposite directions. Lexical search cannot find
"how much did we make" from a document that says *revenue*; vector search
happily returns something thematically close when the document contains the
exact term and the answer is that one word. Each covers the other's blind spot,
and **B-018** is the standing evidence — card search is lexical today and golden
eval #14 fails because of it.

Four things are deliberate.

**Isolation is the org session, not a WHERE clause.** Every query here runs
inside `org_session`, so row-level security scopes it whatever this module gets
wrong. There is an `org_id` predicate too, and that redundancy is the point:
architecture 5.10 wants two independent layers, and the one that holds when this
file has a bug is the one in the database.

**Merged by Reciprocal Rank Fusion, on rank rather than score.** A cosine
distance and a `ts_rank_cd` are numbers on unrelated scales; normalising them
against each other invents a comparison nobody can defend, and the weights would
be tuned on whichever corpus happened to be at hand. RRF only asks *"how near
the top did each list put this?"*, which is the one thing the two lists agree on
how to say.

**A document cannot fill the result on its own.** `PER_DOCUMENT_CAP` bounds how
many chunks any single document contributes, because a long policy document
otherwise wins every slot on term frequency alone and the answer is drawn from
one source that happens to be verbose.

**Unembedded chunks are still findable.** They are excluded from the vector arm
by construction — no vector, no distance — and included in the lexical arm.
That is the ingest ordering paying off: text is searchable the moment it lands.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import Float, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from dataagent.db.models import KnowledgeChunk, KnowledgeDocument
from dataagent.knowledge.embeddings import Embedder
from dataagent.tenancy.session import org_session

__all__ = ["Passage", "search_knowledge"]

#: How many results a caller gets by default. Small: these go into a prompt, and
#: 4.4's budget is spent on every iteration of every run.
DEFAULT_LIMIT = 5

#: How deep each arm looks before the merge. Wider than the limit, because RRF
#: can only promote something one arm found — a chunk outside both lists is
#: unreachable however good it is.
CANDIDATE_DEPTH = 25

#: At most this many chunks from any one document, so a long document cannot
#: fill the result on term frequency alone.
PER_DOCUMENT_CAP = 2

#: RRF's smoothing constant. 60 is the value the original paper used and the one
#: every implementation since has copied; it is here as a named constant rather
#: than a literal so that changing it is visibly a retrieval-quality decision
#: rather than a tweak.
RRF_K = 60


@dataclass(frozen=True, slots=True)
class Passage:
    """One retrieved chunk, with everything needed to attribute it.

    Provenance is not decoration. Architecture 5.5 makes retrieved text safe to
    show *because* it can be traced to a document and a position, and 7.4's
    framing — "these are records, not instructions" — is only honest if the
    record can be named.
    """

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    seq: int
    text: str
    headings: tuple[str, ...]
    #: Which arm found it, for the trace: `vector`, `lexical`, or `both`. A
    #: passage found by both is the strongest signal this module can report, and
    #: hiding which arm fired would make a retrieval regression invisible.
    found_by: str = "both"
    score: float = 0.0

    @property
    def citation(self) -> str:
        """How the passage names itself in a prompt and in an answer."""
        trail = " > ".join(self.headings)
        return f"{self.document_title}{f' — {trail}' if trail else ''} (chunk {self.seq})"


@dataclass
class _Hit:
    """One chunk's standing across both arms, before the merge."""

    row: KnowledgeChunk
    title: str
    ranks: dict[str, int] = field(default_factory=dict[str, int])

    @property
    def found_by(self) -> str:
        return "both" if len(self.ranks) > 1 else next(iter(self.ranks))

    @property
    def score(self) -> float:
        # Reciprocal Rank Fusion: each arm contributes 1/(k + rank), so a chunk
        # both arms liked outranks one that either loved alone. Ranks are
        # 1-based here, which is what the constant assumes.
        return sum(1.0 / (RRF_K + rank) for rank in self.ranks.values())


async def search_knowledge(
    *,
    org_id: uuid.UUID,
    query: str,
    embedder: Embedder | None = None,
    limit: int = DEFAULT_LIMIT,
    document_id: uuid.UUID | None = None,
) -> list[Passage]:
    """The passages most likely to explain a term, best first.

    ``embedder`` is optional and its absence is a *degradation*, not an error:
    with no embedder this is lexical search, which is what the product had
    before this WP and is still better than nothing. Saying so here rather than
    raising is what lets a deployment without an embedding key keep working.
    """
    if not query.strip():
        return []

    vector: list[float] | None = None
    if embedder is not None:
        batch = await embedder.embed([query])
        vector = list(batch.vectors[0]) if batch.vectors else None

    async with org_session(org_id) as session:
        hits: dict[uuid.UUID, _Hit] = {}
        if vector is not None:
            _absorb(hits, await _by_vector(session, org_id, vector, document_id), "vector")
        _absorb(hits, await _by_text(session, org_id, query, document_id), "lexical")

    # Merged and shaped *outside* the session, which is safe only because both
    # session factories set `expire_on_commit=False` — a detached row keeps its
    # loaded columns. Stated rather than assumed: were that ever flipped, this
    # would start raising on attribute access at the point of use rather than
    # here, which is a long way from the cause.
    ordered = sorted(hits.values(), key=lambda hit: hit.score, reverse=True)
    return _capped(ordered, limit)


async def _by_vector(
    session: AsyncSession,
    org_id: uuid.UUID,
    vector: list[float],
    document_id: uuid.UUID | None,
) -> list[tuple[KnowledgeChunk, str]]:
    """Nearest by cosine distance, among chunks that have a vector at all.

    `embedding.is_not(None)` is stated rather than left to the operator: an
    unembedded row has no distance, and depending on how NULL sorts in a
    particular version is the kind of assumption that works until it does not.
    """
    statement = (
        select(KnowledgeChunk, KnowledgeDocument.title)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .where(
            # Redundant with row-level security, and deliberately so: two
            # independent layers, and the one that holds when this file is wrong
            # is the database's.
            KnowledgeChunk.org_id == org_id,
            KnowledgeChunk.embedding.is_not(None),
        )
        .order_by(KnowledgeChunk.embedding.cosine_distance(vector))
        .limit(CANDIDATE_DEPTH)
    )
    if document_id is not None:
        statement = statement.where(KnowledgeChunk.document_id == document_id)
    return list((await session.execute(statement)).all())  # type: ignore[arg-type]


async def _by_text(
    session: AsyncSession,
    org_id: uuid.UUID,
    query: str,
    document_id: uuid.UUID | None,
) -> list[tuple[KnowledgeChunk, str]]:
    """Postgres full-text, with B-041's lesson applied.

    `websearch_to_tsquery` ANDs bare words, so a natural-language question asks
    for a chunk containing *every* stem in it and matches nothing. B-041 cost
    the M7 gate for exactly that. The shape of the fix is B-041's own: the
    strict query runs first and keeps every promise it makes, and only when it
    matches nothing at all are the words retried joined by OR.
    """
    strict = func.websearch_to_tsquery("english", query)
    rows = await _ranked(session, org_id, strict, document_id)
    if rows:
        return rows

    words = [word for word in _words(query) if word]
    if not words:
        return []
    loose = func.to_tsquery("english", " | ".join(words))
    return await _ranked(session, org_id, loose, document_id)


async def _ranked(
    session: AsyncSession,
    org_id: uuid.UUID,
    tsquery: object,
    document_id: uuid.UUID | None,
) -> list[tuple[KnowledgeChunk, str]]:
    rank: ColumnElement[float] = func.ts_rank_cd(KnowledgeChunk.tsv, tsquery).cast(Float)
    statement = (
        select(KnowledgeChunk, KnowledgeDocument.title)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .where(KnowledgeChunk.org_id == org_id, KnowledgeChunk.tsv.op("@@")(tsquery))
        .order_by(rank.desc())
        .limit(CANDIDATE_DEPTH)
    )
    if document_id is not None:
        statement = statement.where(KnowledgeChunk.document_id == document_id)
    return list((await session.execute(statement)).all())  # type: ignore[arg-type]


def _words(query: str) -> list[str]:
    """The query's words, safe to put inside a `to_tsquery` string.

    Non-alphanumerics are dropped rather than escaped: `to_tsquery` has its own
    operator syntax, and a query carrying `&`, `!` or `:` would either error or
    mean something the user did not ask for. Nothing here reaches SQL as text —
    it is a bound parameter — so this is about *meaning*, not injection.
    """
    cleaned = "".join(character if character.isalnum() else " " for character in query.lower())
    return [word for word in cleaned.split() if len(word) > 1]


def _absorb(
    hits: dict[uuid.UUID, _Hit],
    rows: list[tuple[KnowledgeChunk, str]],
    arm: str,
) -> None:
    """Record each row's 1-based rank within one arm's result list."""
    for position, (row, title) in enumerate(rows, start=1):
        hit = hits.get(row.id)
        if hit is None:
            hit = _Hit(row=row, title=title)
            hits[row.id] = hit
        hit.ranks[arm] = position


def _capped(ordered: list[_Hit], limit: int) -> list[Passage]:
    """Best first, with no document allowed to fill the result on its own."""
    seen: defaultdict[uuid.UUID, int] = defaultdict(int)
    out: list[Passage] = []
    for hit in ordered:
        if len(out) >= limit:
            break
        if seen[hit.row.document_id] >= PER_DOCUMENT_CAP:
            continue
        seen[hit.row.document_id] += 1
        out.append(
            Passage(
                chunk_id=hit.row.id,
                document_id=hit.row.document_id,
                document_title=hit.title,
                seq=hit.row.seq,
                text=hit.row.text_,
                headings=tuple(str(heading) for heading in hit.row.headings),
                found_by=hit.found_by,
                score=hit.score,
            )
        )
    return out
