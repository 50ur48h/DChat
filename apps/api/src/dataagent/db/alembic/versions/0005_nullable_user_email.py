"""users.email becomes nullable, and fabricated addresses are erased (B-009)

An identity provider sends an ``email`` claim only when its app registration asks
for one. Entra External ID does not send it by default. With a NOT NULL column,
just-in-time provisioning had to invent something, and wrote
``<subject>@unknown.invalid`` — a value that looks like an address, is not one,
and would be trusted by every later feature that reads the column.

A column that cannot say "unknown" makes something up. This revision lets it say
so, and deletes the addresses that were invented before it could.

``.invalid`` is reserved by RFC 2606 and can never be a real address, so the
cleanup below cannot match a genuine one.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FABRICATED_SUFFIX = "@unknown.invalid"


def upgrade() -> None:
    op.alter_column("users", "email", existing_type=sa.String(length=320), nullable=True)
    op.execute(f"UPDATE users SET email = NULL WHERE email LIKE '%{FABRICATED_SUFFIX}'")


def downgrade() -> None:
    # Restoring NOT NULL means restoring the fiction, because that is what the
    # previous schema required. Written explicitly so that a downgrade is a
    # decision someone reads rather than a constraint error they work around.
    op.execute(
        f"UPDATE users SET email = external_subject || '{FABRICATED_SUFFIX}' WHERE email IS NULL"
    )
    op.alter_column("users", "email", existing_type=sa.String(length=320), nullable=False)
