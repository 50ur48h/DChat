"""a conversation names the database it is about (D-022)

WP7.2c made the scheduler refuse rather than guess when an organization has more
than one registered data source, because a silently wrong database produces a
confident, correctly-cited answer about somebody else's data. That refusal was
always meant to be temporary: something has to *name* the source, and this is it.

``conversations.data_source_id`` is the answer, and the column is on the
conversation rather than on the message or the run because a thread is about one
database. Follow-up questions in Phase 8 must reach the same source as the
question they follow, or a conversation's citations stop being comparable with
each other.

Nullable, and null is meaningful: it is a conversation that named nothing, which
is the shape every conversation written before this revision has. Those still
work exactly as they did — the scheduler falls back to "the one source, if there
is exactly one" and refuses otherwise — so this migration needs no backfill and
changes no existing behaviour.

``ON DELETE SET NULL`` rather than CASCADE. Deleting a data source must not
delete the record of what people asked about it; the conversation, its runs and
its trace all survive with the join cleared, which is the same trade D-016 makes
for ``query_executions.data_source_id`` one table over. A conversation that has
lost its source behaves like one that never named it: the next question refuses
and says why, rather than quietly retargeting a different database.

No RLS work here. ``conversations`` became a tenant table in revision 0012 with
its policy, its ``TENANT_TABLES`` line and its seed/forge pair in the rls_proof
suite; a new column on an existing protected table inherits all of it.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "conversations"
COLUMN = "data_source_id"
FK = "fk_conversations_data_source_id"
INDEX = "ix_conversations_data_source_id"


def upgrade() -> None:
    # No column comment: this codebase documents columns on the model, and the
    # drift check compares the two — a comment here and not there is a
    # difference the models would have to grow a convention to absorb.
    op.add_column(TABLE, sa.Column(COLUMN, postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        FK,
        source_table=TABLE,
        referent_table="data_sources",
        local_cols=[COLUMN],
        remote_cols=["id"],
        ondelete="SET NULL",
    )
    # "Which conversations point at the source I am about to remove" is the
    # question an operator asks before removing one, and PostgreSQL does not
    # index a foreign key for you.
    op.create_index(INDEX, TABLE, [COLUMN])


def downgrade() -> None:
    op.drop_index(INDEX, table_name=TABLE)
    op.drop_constraint(FK, TABLE, type_="foreignkey")
    op.drop_column(TABLE, COLUMN)
