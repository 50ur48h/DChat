"""Finding the right table by asking for it in words (architecture Part 5.2).

This is the retrieval half of "schema RAG": the agent — and, from this work
package, a person — describes what they are looking for, and gets back the few
table cards worth reading rather than a catalog dump.

**Hybrid since B-018, and the vector arm is what the whole thing was for.**
Lexical search finds `orders` from *"revenue"* only because the word "revenue"
happens to appear in that card's prose, and cannot find it from *"how much did we
make"* at all. Golden eval **#14** — *"which day of the week is busiest?"* —
failed live for exactly that reason: no card contains "busiest", so the planner
was handed a catalog without the table its question was about. The two arms are
merged by **Reciprocal Rank Fusion on rank**, the same way `knowledge/retrieve.py`
merges its two, and for the same reason: a `ts_rank_cd` and a cosine distance are
numbers on unrelated scales, so normalising them against each other invents a
comparison nobody can defend.

**The embedder is optional and its absence is a degradation, not an error.** A
deployment with no embedding key gets exactly the search it had before, and a
caller inside a run gets its query embedding metered and capped like every other
spend (**B-073**, D-031) — which is why this takes a `run_id` at all.

``websearch_to_tsquery`` rather than ``plainto_tsquery`` because people type
search engine syntax without being told to — quoted phrases and ``or`` mean what
they look like, and a stray operator is not a 500.

**Two passes, and the second is what makes the agent work** (**B-041**).
``websearch_to_tsquery`` joins bare words with **AND**, which is right for a
person typing ``orders july`` into a search box and wrong for the agent, whose
query is a whole question. *"How many orders were placed in July 2026?"* becomes
``'mani' & 'order' & 'place' & 'juli' & '2026'``, and no card on earth contains
all of those — so the M7 gate question found **nothing**, and the model was
handed an empty catalog. So: the strict query runs first and keeps every promise
above, and **only when it matches nothing** the terms are retried joined by OR
and ranked. Nothing that used to match stops matching; something that used to
return nothing now returns candidates, best first.

The fallback is deliberately not the default. AND-first means a two-word search
still means both words, which is what a person expects and what keeps precision
where precision is available; OR is the answer to "this matched nothing", not a
looser search everywhere.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Float, Select, func, select
from sqlalchemy.sql.elements import ColumnElement

from dataagent.config import Settings
from dataagent.db.models import CARD_TEXT_CONFIGURATION, CatalogSnapshot, CatalogTable
from dataagent.knowledge.embeddings import Embedder, embed_texts
from dataagent.llm.base import LLMError
from dataagent.tenancy.session import org_session

__all__ = ["CardHit", "search_cards"]

logger = logging.getLogger(__name__)

#: Enough to choose from, few enough to put in a prompt.
DEFAULT_LIMIT = 10
MAX_LIMIT = 50

#: How deep each arm looks before the merge. Wider than the limit, because RRF
#: can only promote something an arm found — a card outside both lists is
#: unreachable however good it is.
CANDIDATE_DEPTH = 25

#: RRF's smoothing constant, the value the original paper used. Named rather
#: than inlined so that changing it is visibly a retrieval-quality decision.
#: Shared in spirit with `knowledge/retrieve.py`, not in code: the two merge
#: different things and should be free to be tuned apart.
RRF_K = 60

#: Word characters only. Everything else — quotes, operators, punctuation — is a
#: separator, so nothing a caller types can reach ``to_tsquery`` as syntax. That
#: is what makes the OR pass safe to build by hand rather than parse.
_WORD = re.compile(r"[^0-9A-Za-z_]+")

#: A single letter carries no meaning and matches half the catalog.
_MIN_TERM = 2


def _or_terms(text: str) -> str:
    """The caller's words, joined by OR, as ``to_tsquery`` syntax.

    Stemming and stopword removal are left to PostgreSQL: ``to_tsquery`` applies
    the same English configuration the index was built with, so "How many orders"
    becomes ``'mani' | 'order'`` and the question words disappear on their own.
    Doing it here would mean maintaining a stopword list that disagreed with the
    index sooner or later.
    """
    terms = [term for term in _WORD.split(text) if len(term) >= _MIN_TERM]
    return " | ".join(terms)


@dataclass(frozen=True, slots=True)
class CardHit:
    data_source_id: uuid.UUID
    schema_name: str
    table_name: str
    card_text: str
    #: The merged score: with one arm it is `ts_rank_cd`, with two it is the RRF
    #: sum. Comparable within one result set only — it is what truncation sorts
    #: by when a prompt has to give something up (`agent/context.render`).
    rank: float
    #: Which arm found it: `vector`, `lexical`, or `both`. In the trace because
    #: "which words chose these tables" is the silent decision B-060 was filed
    #: for, and a card found by meaning was chosen by a different mechanism from
    #: one found by wording.
    found_by: str = "lexical"


#: One row as either arm returns it, before the merge.
_Row = tuple[uuid.UUID, str, str, str | None]

#: What identifies a card across the two arms: its source and its qualified name.
_Key = tuple[uuid.UUID, str, str]


async def search_cards(
    org_id: uuid.UUID,
    query: str,
    *,
    data_source_id: uuid.UUID | None = None,
    limit: int = DEFAULT_LIMIT,
    embedder: Embedder | None = None,
    run_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    settings: Settings | None = None,
) -> list[CardHit]:
    """Table cards matching ``query``, best first.

    Only ever the **active** snapshot of each data source: a superseded catalog
    is kept for runs that are still reading it (D-012), and returning one here
    would answer a question about a database as it used to be.

    ``embedder`` adds the vector arm. Without it this is exactly the lexical
    search it has always been — which is what a deployment with no embedding key
    gets, and what a catalog whose cards are still `queued` gets on the arm that
    can reach them.
    """
    text = query.strip()
    if not text:
        return []

    capped = max(1, min(limit, MAX_LIMIT))
    vector = await _query_vector(
        org_id=org_id,
        query=text,
        embedder=embedder,
        run_id=run_id,
        actor_user_id=actor_user_id,
        settings=settings,
    )

    def scoped(
        statement: Select[tuple[uuid.UUID, str, str, str | None]],
    ) -> Select[tuple[uuid.UUID, str, str, str | None]]:
        statement = statement.join(
            CatalogSnapshot, CatalogSnapshot.id == CatalogTable.snapshot_id
        ).where(CatalogSnapshot.status == "active")
        if data_source_id is not None:
            statement = statement.where(CatalogSnapshot.data_source_id == data_source_id)
        return statement

    def selected() -> Select[tuple[uuid.UUID, str, str, str | None]]:
        return select(
            CatalogSnapshot.data_source_id,
            CatalogTable.schema_name,
            CatalogTable.table_name,
            CatalogTable.card_text,
        )

    def matching(tsquery: ColumnElement[str]) -> Select[tuple[uuid.UUID, str, str, str | None]]:
        rank: ColumnElement[float] = func.ts_rank_cd(CatalogTable.card_tsv, tsquery).cast(Float)
        return (
            scoped(selected())
            .where(CatalogTable.card_tsv.op("@@")(tsquery))
            # Ties broken by name so the same query twice gives the same answer;
            # an unstable order would make a golden eval flap for no reason.
            .order_by(rank.desc(), CatalogTable.schema_name, CatalogTable.table_name)
            .limit(CANDIDATE_DEPTH)
        )

    def nearest(embedded: list[float]) -> Select[tuple[uuid.UUID, str, str, str | None]]:
        # `is_not(None)` is stated rather than left to the operator: a card with
        # no vector has no distance, and depending on how NULL sorts in a given
        # version is the kind of assumption that works until it does not.
        return (
            scoped(selected())
            .where(CatalogTable.embedding.is_not(None))
            .order_by(
                CatalogTable.embedding.cosine_distance(embedded),
                CatalogTable.schema_name,
                CatalogTable.table_name,
            )
            .limit(CANDIDATE_DEPTH)
        )

    terms = _or_terms(text)
    async with org_session(org_id) as session:

        async def rows(statement: Select[tuple[uuid.UUID, str, str, str | None]]) -> list[_Row]:
            # Destructured into plain tuples rather than passed on as SQLAlchemy
            # `Row`s: the merge is ordinary Python and should not have to know
            # what produced its input.
            return [
                (source_id, schema_name, table_name, card_text)
                for source_id, schema_name, table_name, card_text in (
                    await session.execute(statement)
                ).all()
            ]

        lexical = await rows(matching(_strict(text)))
        if not lexical and terms:
            # Nothing matched with every word required, which for a question is
            # the normal case rather than the exception (B-041). Ask again for
            # any word, and let the rank decide what is worth reading.
            lexical = await rows(matching(func.to_tsquery(CARD_TEXT_CONFIGURATION, terms)))
        semantic = await rows(nearest(vector)) if vector else []

    return _merge(lexical, semantic)[:capped]


def _strict(text: str) -> ColumnElement[str]:
    return func.websearch_to_tsquery(CARD_TEXT_CONFIGURATION, text)


async def _query_vector(
    *,
    org_id: uuid.UUID,
    query: str,
    embedder: Embedder | None,
    run_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None,
    settings: Settings | None,
) -> list[float] | None:
    """The question as a vector, or None and a line in the log.

    **Through `embed_texts`, never `embedder.embed`**, because that is where the
    ledger row and the run's ceiling are (**B-073**).

    A failure degrades rather than propagates, exactly as it does in
    `knowledge/retrieve.py` and for the stronger reason: this is the *context*
    stage of every run, so raising here would turn a spending ceiling or a busy
    provider into a question that cannot be answered at all. Unlike the knowledge
    tool there is nobody to tell — a card search has no envelope of its own — so
    the visible half is `CardHit.found_by`, which the run records in
    `context_selected`.
    """
    if embedder is None:
        return None
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
        logger.warning("card search fell back to wording alone: %s", error)
        return None
    return list(vectors[0]) if vectors else None


def _merge(lexical: Sequence[_Row], semantic: Sequence[_Row]) -> list[CardHit]:
    """Reciprocal Rank Fusion over the two arms, best first.

    On **rank**, not on score: a `ts_rank_cd` and a cosine distance are numbers
    on unrelated scales, and normalising them against each other invents a
    comparison nobody can defend. RRF asks only *"how near the top did each arm
    put this?"*, which is the one thing the two lists agree on how to say.

    With one arm this is a monotone transformation of that arm's own order, so a
    deployment with no embedder gets exactly the ranking it got before.
    """
    # Keyed by **source** as well as name: an organization with two databases
    # can have `public.orders` in both, and merging them would report one table
    # where there are two — the same confusion D-022 exists to prevent, arriving
    # through a dictionary key.
    scores: dict[_Key, float] = {}
    arms: dict[_Key, set[str]] = {}
    rows: dict[_Key, _Row] = {}

    for arm, found in (("lexical", lexical), ("vector", semantic)):
        for position, row in enumerate(found, start=1):
            key = (row[0], row[1], row[2])
            rows.setdefault(key, row)
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + position)
            arms.setdefault(key, set()).add(arm)

    # Ties broken by name, for the reason the lexical order already was: an
    # unstable order makes a golden eval flap for no reason.
    ordered = sorted(scores, key=lambda key: (-scores[key], key[1], key[2], str(key[0])))
    return [
        CardHit(
            data_source_id=rows[key][0],
            schema_name=rows[key][1],
            table_name=rows[key][2],
            card_text=rows[key][3] or "",
            rank=scores[key],
            found_by="both" if len(arms[key]) > 1 else next(iter(arms[key])),
        )
        for key in ordered
    ]
