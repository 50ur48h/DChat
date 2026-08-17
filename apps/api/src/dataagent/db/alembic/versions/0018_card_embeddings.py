"""catalog_tables.embedding — a table card can finally be found by meaning

**B-018**, open since WP4.3 and the oldest thing in Phase 10. Card search has
been lexical since it was built: `websearch_to_tsquery` over `card_tsv`, which
finds `orders` from *"revenue"* only because the word "revenue" happens to appear
in that card's prose, and cannot find it from *"how much did we make"* at all.
Golden eval **#14** — *"which day of the week is busiest?"* — fails live for
exactly that reason: no card contains "busiest", so the planner is handed a
catalog that does not include the table it needs.

Three things are deliberate, and the first is why this column did not exist
until now.

**D-014 refused to create it before something could fill it.** A vector column
nothing writes is a code path nothing exercises, and a rerank over cards nobody
embedded would be wrong the first time it mattered. What changed is not this
schema but **B-073**: an embedder now reaches the agent's own path metered
against the run and bounded by D-019's ceiling, so a *query* embedding inside
`build_context` is an ordinary spend rather than an unwatched one. The column
arrives with both halves of its filling — the backfill that writes card vectors
and the search that reads them — in the same PR.

**Nullable, for revision 0016's reason.** A card exists as soon as its text does
and becomes searchable by meaning once the provider has been called. That call is
a network round trip which can fail or be rate limited, and a NOT NULL column
would mean a catalog refresh either blocks on the provider or loses its cards.
`flags.embedding` has said `queued` on every card since WP4.3 precisely so this
backfill would have a work list waiting for it.

**No vector index, and here the argument is stronger than it was for chunks.**
Revision 0016 left `knowledge_chunks.embedding` unindexed and filed the
measurement as **B-071**; a catalog is smaller than a corpus by orders of
magnitude — one row per table per snapshot, tens per data source, not thousands
per document — so an exact scan over one organization's active cards is not a
latency question yet. An approximate index would also be the wrong trade here for
a reason that does not apply to chunks: a missed *chunk* costs one passage, while
a missed *card* costs the planner the table its question is about, which is
B-041's failure with a new cause.

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = "catalog_tables"

#: Architecture 10.1's width and `text-embedding-3-small`'s, verified against the
#: live account rather than read off a page (B-027's habit). Written as a literal
#: rather than imported from `dataagent.db.models`, for revision 0013's reason: a
#: migration that imports application code stops meaning what it meant when it
#: ran the moment that code moves on.
EMBEDDING_DIMENSIONS = 1536


def upgrade() -> None:
    # Already created by revision 0016 in any database that has run it; stated
    # again so this revision is true on its own, as 0016 said of its grants.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column(TABLES, sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=True))
    # Partial, over exactly the rows the backfill visits: a card with text and no
    # vector. Cheap to maintain — a catalog has tens of rows per source, not
    # millions — and it means "what still needs embedding" never scans the table.
    op.execute(f"""
        CREATE INDEX ix_catalog_tables_unembedded ON {TABLES} (org_id, snapshot_id)
        WHERE embedding IS NULL AND card_text IS NOT NULL
    """)
    # `catalog_tables` has carried an org_isolation policy and the application
    # role's grants since revision 0007. A column added to an existing table
    # inherits both, so there is nothing to add here — and nothing to forget.


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_catalog_tables_unembedded")
    op.drop_column(TABLES, "embedding")
