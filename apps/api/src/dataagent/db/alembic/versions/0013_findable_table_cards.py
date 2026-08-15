"""rewrite existing table cards so a table is findable by its own name (B-039)

WP7.3's chat UI and Phase 8's refusal demo both rest on `search_tables`, and it
could not reliably find a table by the table's own name. PostgreSQL's English
parser reads ``public.shops`` as one *host* token::

    to_tsvector('english', 'public.shops')   -> 'public.shops':1
    websearch_to_tsquery('english', 'shops') -> 'shop'
    ... @@ ...                               -> false

So a card was findable by its name only when that name happened to appear again
as plain English in its prose. On the two demo catalogs **6 rows of 13 failed**,
including ``menu_items`` on both sources — the table Phase 8's flagship refusal
demo is about, which would have refused for the wrong reason and made the M8 gate
pass while demonstrating nothing.

``cards.build_card`` now opens ``shops (public.shops) is a table ...`` instead of
``public.shops is a table ...``. This revision brings cards written before that
change into the same shape.

**Why a SQL rewrite rather than regenerating the prose.** Rebuilding a card means
running ``build_card`` over catalog rows, which is application code — and a
migration that imports application code stops meaning the same thing the moment
that code moves on. The transformation here is exact instead: the old opening is
a known prefix, so it is replaced with the new one and nothing else is touched.
The result is byte-identical to what ``build_card`` now produces, so a later
refresh writes the same text rather than a diff.

The ``LIKE`` guard makes this safe to re-run and safe on a database that already
holds new-format cards: only a card still opening with its qualified name is
rewritten.

``card_tsv`` is a **generated** column, so no index maintenance appears here —
writing ``card_text`` is what rebuilds it, which is exactly the property revision
0009 introduced it for.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-15
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "catalog_tables"

#: FORCE row-level security applies to the migration's own owner too, and the
#: policy dereferences ``app.org_id`` — which no statement spanning every
#: organization can set meaningfully. Lifted for the rewrite and put straight
#: back, inside this migration's transaction, exactly as revision 0012 does.
_NO_FORCE = f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY"
_FORCE = f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY"

_QUALIFIED = "schema_name || '.' || table_name"

_TO_NEW_FORM = f"""
    UPDATE {TABLE}
    SET card_text = table_name || ' (' || {_QUALIFIED} || ')'
                    || substring(card_text from length({_QUALIFIED}) + 1)
    WHERE card_text IS NOT NULL
      AND card_text LIKE {_QUALIFIED} || ' is a %'
"""


def upgrade() -> None:
    op.execute(_NO_FORCE)
    op.execute(_TO_NEW_FORM)
    op.execute(_FORCE)


def downgrade() -> None:
    op.execute(_NO_FORCE)
    op.execute(f"""
        UPDATE {TABLE}
        SET card_text = {_QUALIFIED}
                        || substring(
                               card_text
                               from length(table_name || ' (' || {_QUALIFIED} || ')') + 1
                           )
        WHERE card_text IS NOT NULL
          AND card_text LIKE table_name || ' (' || {_QUALIFIED} || ') is a %'
    """)
    op.execute(_FORCE)
