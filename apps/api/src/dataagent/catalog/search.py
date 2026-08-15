"""Finding the right table by asking for it in words (architecture Part 5.2).

This is the retrieval half of "schema RAG": the agent — and, from this work
package, a person — describes what they are looking for, and gets back the few
table cards worth reading rather than a catalog dump.

Lexical only, deliberately, and not as a compromise. Plan §6 Phase 4 makes the
embeddings key optional and says search runs on the text index without one; a
vector rerank over cards nobody has embedded would be a code path that is never
executed and therefore never right. It arrives with the key that makes it
possible (**B-018**), and the shape here already has the seam: rank, then
reorder.

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

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import Float, Select, func, select
from sqlalchemy.sql.elements import ColumnElement

from dataagent.db.models import CARD_TEXT_CONFIGURATION, CatalogSnapshot, CatalogTable
from dataagent.tenancy.session import org_session

__all__ = ["CardHit", "search_cards"]

#: Enough to choose from, few enough to put in a prompt.
DEFAULT_LIMIT = 10
MAX_LIMIT = 50

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
    #: ts_rank_cd, which weighs how close the matched words are to each other as
    #: well as how often they appear. Comparable within one result set only.
    rank: float


async def search_cards(
    org_id: uuid.UUID,
    query: str,
    *,
    data_source_id: uuid.UUID | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[CardHit]:
    """Table cards matching ``query``, best first.

    Only ever the **active** snapshot of each data source: a superseded catalog
    is kept for runs that are still reading it (D-012), and returning one here
    would answer a question about a database as it used to be.
    """
    text = query.strip()
    if not text:
        return []

    capped = max(1, min(limit, MAX_LIMIT))

    def matching(
        tsquery: ColumnElement[str],
    ) -> Select[tuple[uuid.UUID, str, str, str | None, float]]:
        rank: ColumnElement[float] = func.ts_rank_cd(CatalogTable.card_tsv, tsquery).cast(Float)
        statement = (
            select(
                CatalogSnapshot.data_source_id,
                CatalogTable.schema_name,
                CatalogTable.table_name,
                CatalogTable.card_text,
                rank.label("rank"),
            )
            .join(CatalogSnapshot, CatalogSnapshot.id == CatalogTable.snapshot_id)
            .where(
                CatalogSnapshot.status == "active",
                CatalogTable.card_tsv.op("@@")(tsquery),
            )
            # Ties broken by name so the same query twice gives the same answer;
            # an unstable order would make a golden eval flap for no reason.
            .order_by(rank.desc(), CatalogTable.schema_name, CatalogTable.table_name)
            .limit(capped)
        )
        if data_source_id is not None:
            statement = statement.where(CatalogSnapshot.data_source_id == data_source_id)
        return statement

    strict = matching(func.websearch_to_tsquery(CARD_TEXT_CONFIGURATION, text))
    terms = _or_terms(text)

    async with org_session(org_id) as session:
        rows = (await session.execute(strict)).all()
        if not rows and terms:
            # Nothing matched with every word required, which for a question is
            # the normal case rather than the exception (B-041). Ask again for
            # any word, and let the rank decide what is worth reading.
            rows = (
                await session.execute(matching(func.to_tsquery(CARD_TEXT_CONFIGURATION, terms)))
            ).all()

    return [
        CardHit(
            data_source_id=source_id,
            schema_name=schema_name,
            table_name=table_name,
            card_text=card_text or "",
            rank=float(found),
        )
        for source_id, schema_name, table_name, card_text, found in rows
    ]
