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
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import Float, func, select
from sqlalchemy.sql.elements import ColumnElement

from dataagent.db.models import CARD_TEXT_CONFIGURATION, CatalogSnapshot, CatalogTable
from dataagent.tenancy.session import org_session

__all__ = ["CardHit", "search_cards"]

#: Enough to choose from, few enough to put in a prompt.
DEFAULT_LIMIT = 10
MAX_LIMIT = 50


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

    tsquery: ColumnElement[str] = func.websearch_to_tsquery(CARD_TEXT_CONFIGURATION, text)
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
        .limit(max(1, min(limit, MAX_LIMIT)))
    )
    if data_source_id is not None:
        statement = statement.where(CatalogSnapshot.data_source_id == data_source_id)

    async with org_session(org_id) as session:
        rows = (await session.execute(statement)).all()

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
