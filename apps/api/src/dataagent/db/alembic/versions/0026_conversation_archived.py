"""conversations.archived_at — put a thread away without destroying its trace (WP11.2a)

Plan WP11.2 says "conversation history list + rename/**delete**". This revision
is the delete half, and it is an **archive** instead — a plan-wording correction
recorded as **D-039**, agreed with the owner on 2026-08-20.

A conversation is the root of everything the product promises to be able to show
you afterwards: its runs, their events, their findings, their query executions.
Architecture 0.2.4 makes that trace durable and `agent_events` is append-only by
grant. A row that cascaded those away on a misclick would delete the evidence
behind answers a person may already have acted on, and it would do it through
the one surface where a slip is cheapest to make.

So the column records **when it was put away**, and nothing is removed. The list
stops showing it, the thread is still reachable by its own address, and it can
come back. True erasure — a customer asking for their data to be gone — is a
different feature with different requirements (every table, a receipt that it
happened, a retention window), and it belongs to Phase 12's retention story
rather than to a button on a list screen.

Nullable, and NULL means "not archived" rather than a flag defaulting to false:
the useful question a reader asks later is *when* this was put away, and a
boolean cannot answer it.

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONVERSATIONS = "conversations"


def upgrade() -> None:
    op.add_column(
        CONVERSATIONS, sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
    )
    # The list screen's query is "mine, not archived, newest first", and it runs
    # on every visit to the conversations page.
    op.create_index(
        "ix_conversations_user_archived",
        CONVERSATIONS,
        ["user_id", "archived_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_user_archived", table_name=CONVERSATIONS)
    op.drop_column(CONVERSATIONS, "archived_at")
