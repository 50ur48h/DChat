"""data_sources learns whether its credentials were proven read-only

WP3.2. Registration in WP3.1 could honestly say only "we stored this"; with a
connector there is something to record: whether the address answered, whether
the credentials worked, and whether the engine's own privilege catalog says they
cannot write (architecture M3, Part 7.5).

``readonly_verified`` defaults to **false**, and every path that fails to prove
otherwise leaves it false. An unverified data source is the safe default state,
not an error state.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "data_sources",
        sa.Column(
            "readonly_verified",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "data_sources",
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("data_sources", "last_verified_at")
    op.drop_column("data_sources", "readonly_verified")
