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

**A search says which arms it actually ran** (**B-073**). Embedding the query
costs money and can therefore be refused — by a run at its ceiling, by a
provider having a bad afternoon, or by a deployment that never configured a key
— and in every one of those cases the lexical arm still answers. The difference
between *"the vector arm found nothing"* and *"the vector arm never ran"* is
invisible in the passages themselves and is the whole difference between a
corpus that does not cover a question and a retrieval that quietly halved
itself, so `Retrieval` reports it rather than leaving the caller to infer it.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import Float, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from dataagent.config import Settings
from dataagent.db.models import KnowledgeChunk, KnowledgeDocument
from dataagent.knowledge.embeddings import Embedder, embed_texts
from dataagent.llm.base import LLMError
from dataagent.llm.budget import RunCostExceededError
from dataagent.tenancy.session import org_session

__all__ = ["Passage", "Retrieval", "search_knowledge"]

logger = logging.getLogger(__name__)

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


#: Why the vector arm did not run, in words a person and a model can both read.
#: Not an exception and not a silent absence: each of these is a normal state of
#: the system, and the caller's job is to say so rather than to fail.
NO_EMBEDDER = (
    "Only the wording of the documents was searched: this deployment has no "
    "embedding model configured, so a passage that means the same thing in "
    "different words was not found."
)
EMBEDDING_REFUSED = (
    "Only the wording of the documents was searched: the search by meaning was "
    "not run because this run has reached its spending ceiling."
)
EMBEDDING_FAILED = (
    "Only the wording of the documents was searched: the search by meaning "
    "could not be run just now."
)


@dataclass(frozen=True, slots=True)
class Retrieval:
    """What one search found, and how much of the search actually happened.

    ``arms`` is what ran, not what returned something — an empty vector arm and
    an absent one are different facts and only one of them is about the corpus.
    """

    passages: tuple[Passage, ...] = ()
    arms: tuple[str, ...] = ()
    #: Set when the vector arm was skipped, naming which of the ordinary reasons
    #: it was. None when both arms ran.
    degraded: str | None = None


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
    run_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    settings: Settings | None = None,
) -> Retrieval:
    """The passages most likely to explain a term, best first.

    ``embedder`` is optional and its absence is a *degradation*, not an error:
    with no embedder this is lexical search, which is what a deployment with no
    embedding key gets and is still better than nothing. Saying so here rather
    than raising is what lets such a deployment keep working — and `degraded` is
    what stops it looking like a corpus that has nothing to say.

    ``run_id`` charges the query embedding to a run, which is what puts it under
    D-019's ceiling (**B-073**). Passing none is right for a person searching
    from the documents page — there is no run to charge and no loop to bound —
    and wrong for anything inside the agent, which is why every caller in
    `agent/` passes one.
    """
    if not query.strip():
        return Retrieval()

    vector, degraded = await _query_vector(
        org_id=org_id,
        query=query,
        embedder=embedder,
        run_id=run_id,
        actor_user_id=actor_user_id,
        settings=settings,
    )

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
    arms = ("vector", "lexical") if vector is not None else ("lexical",)
    return Retrieval(passages=tuple(_capped(ordered, limit)), arms=arms, degraded=degraded)


async def _query_vector(
    *,
    org_id: uuid.UUID,
    query: str,
    embedder: Embedder | None,
    run_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None,
    settings: Settings | None,
) -> tuple[list[float] | None, str | None]:
    """The query as a vector, or the reason there is not one.

    **Through `embed_texts`, never `embedder.embed` directly.** That function is
    where the ledger row and the run's ceiling are, and calling the client
    underneath it would be a spend nothing counted — which is precisely the hole
    B-073 was filed about, one layer down from where it was noticed.

    **A failure here is caught, not propagated.** The lexical arm has already
    been paid for and still answers; turning a spending ceiling or a provider
    hiccup into a failed search would take a working half of the feature away
    for the sake of consistency. What must not happen is that it goes unsaid,
    and the second half of the return value is that.
    """
    if embedder is None:
        return None, NO_EMBEDDER

    try:
        vectors = await embed_texts(
            org_id=org_id,
            texts=[query],
            embedder=embedder,
            run_id=run_id,
            actor_user_id=actor_user_id,
            settings=settings,
        )
    except LLMError as error:
        # Logged, because a deployment whose vector arm has quietly stopped
        # working should be discoverable from something other than answer
        # quality. The message is already sanitized by the embedder.
        logger.warning("the query could not be embedded, searching lexically only: %s", error)
        if isinstance(error, RunCostExceededError):
            return None, EMBEDDING_REFUSED
        return None, EMBEDDING_FAILED

    return (list(vectors[0]), None) if vectors else (None, EMBEDDING_FAILED)


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
