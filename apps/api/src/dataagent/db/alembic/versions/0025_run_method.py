"""agent_runs.method — how the answer was reached, in one line (B-100)

Architecture 4.2 makes an answer four things: the words, the evidence, the
**method**, and the limitations. Three of them have had a home since WP9.2 —
`answer` is a message, `limitations` and `chart` are columns here. The method was
the one that did not, and `composer.method_note` has been building it on every
run since Phase 9 for `assemble` to set on a `ComposedAnswer` that
`_write_ending` then dropped on the floor.

So this is not a new idea; it is the column the other three already had. It
lands beside them for the same reason `limitations` does — the answer card
renders it as its own thing, and a reader who will not open the SQL is exactly
who it is for.

**A column rather than recomputing it from `state`.** The checkpoint is still
there and the sentence could be rebuilt from it on every read, but it would be
rebuilt by *today's* code about yesterday's run — the same trap `_grounding`
calls out one function below, where re-matching definitions now would answer with
today's rules about an old answer. What the reader is owed is what this run did,
recorded when it did it.

Nullable, and empty for every run written before this revision: those runs were
composed by code that never stored one, and inventing a sentence for them now
would be this module asserting something it cannot know.

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNS = "agent_runs"


def upgrade() -> None:
    op.add_column(RUNS, sa.Column("method", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column(RUNS, "method")
