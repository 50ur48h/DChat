"""table cards: the text the agent will read, and the index it is found by

WP4.3, architecture Part 5.3. A card is a compact description of one table —
what it holds, its columns and their roles, its keys and its neighbours — written
so that a language model can be given *this* instead of a schema dump. Part 5.2
is blunt about why: the full catalog never enters a prompt, and "schema RAG" is
what makes a two-thousand-table database workable.

``card_tsv`` is a **generated** column rather than one a trigger maintains. The
index can then never disagree with the text it indexes, which is the failure a
trigger eventually has: someone writes a card in a migration or a backfill, the
trigger is not there, and search quietly stops finding a table nobody notices is
missing.

There is no ``embedding`` column yet. Plan §6 Phase 4 makes the embeddings key
optional and says card search runs lexical-only without one; a vector column
nothing can fill is a column that is never exercised, so it arrives with the key
that fills it (**B-018**).

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "catalog_tables"

#: 'english' rather than 'simple': a card is prose, and a person searching
#: "revenue" should find a table whose card says "revenues". Stored in the
#: generated column's definition, so changing it is a migration — which it
#: should be, since every existing row's index would have to be rebuilt.
CONFIGURATION = "english"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("card_text", sa.Text(), nullable=True))
    op.add_column(
        TABLE,
        sa.Column(
            "flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        TABLE,
        # The engine's own estimate, not a count.
        sa.Column("row_estimate", sa.BigInteger(), nullable=True),
    )

    # Generated, so the text and its index cannot drift. COALESCE because a
    # table that has not been carded yet is a null, and to_tsvector(null) is
    # null — which would make the column unindexable rather than empty.
    op.execute(
        f"ALTER TABLE {TABLE} ADD COLUMN card_tsv tsvector "
        f"GENERATED ALWAYS AS (to_tsvector('{CONFIGURATION}', COALESCE(card_text, ''))) STORED"
    )
    op.execute(f"CREATE INDEX ix_catalog_tables_card_tsv ON {TABLE} USING GIN (card_tsv)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_catalog_tables_card_tsv")
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS card_tsv")
    op.drop_column(TABLE, "row_estimate")
    op.drop_column(TABLE, "flags")
    op.drop_column(TABLE, "card_text")
