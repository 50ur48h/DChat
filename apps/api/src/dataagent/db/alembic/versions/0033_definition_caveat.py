"""semantic_definitions.caveat — what a definition makes the *answer* say

A definition has had three outputs and none of them reached the reader.
`description` and `expression` are prose for the prompt; `required_filters` is
structure for the critic; and `state.applied_definitions` was used in the
composer **only to suppress** the prose-definition caveat, so a matched
definition produced no caveat of its own.

That is fine for a metric whose whole meaning is a formula. It is not fine for
one whose meaning includes a limit on what may be claimed. MiseQ's own words:
*"Always report kg. For RM, filter `value_is_available=1` … never label
SUM(value_myr) as total restaurant waste cost."* The filter half binds through
D-033's critic. The sentence half had nowhere to go — it would have reached the
model and stopped there, and a model that dropped it would have been contradicted
by nothing.

**That is D-053's mistake facing the other way** (owner, 2026-08-28): a caveat
that reaches the model and not the reader is the same shape as one that reaches
the reader and enforces nothing. Both are a claim the platform states and does
not keep.

Nullable, because most definitions have nothing to add — a metric that is simply
a formula should not be made to sound uncertain by a column that always wants
filling.

Revision ID: 0033
Revises: 0032
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "semantic_definitions",
        sa.Column("caveat", sa.Text(), nullable=True),
    )
    op.add_column(
        "semantic_definition_versions",
        sa.Column("caveat", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("semantic_definition_versions", "caveat")
    op.drop_column("semantic_definitions", "caveat")
