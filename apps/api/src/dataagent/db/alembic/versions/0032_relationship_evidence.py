"""catalog_relationships.evidence — why an inferred join was believed (B-145)

`kind` and `confidence` have been on this table since revision 0007, and nothing
ever wrote anything but `declared` and `1.0`. Inference (D-050) changes that, and
a confidence on its own is a number to argue with rather than a fact to check:
0.95 says somebody was fairly sure, and says nothing about what they looked at.

This column holds the measurements — how many rows the parent had, how many of
them were distinct, how many non-null values the child carried, and how many of
those had no match. A wrong edge is then traceable to the numbers that produced
it, and a later change to the rule has something concrete to beat.

**Null for a declared key, and that is the honest value.** A foreign key the
engine states was not measured; it was read. Writing `{}` there would suggest an
empty measurement rather than no measurement.

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "catalog_relationships",
        sa.Column("evidence", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("catalog_relationships", "evidence")
