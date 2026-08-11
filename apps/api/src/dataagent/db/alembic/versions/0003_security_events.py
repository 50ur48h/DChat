"""security_events: denials that belong to no tenant

Architecture Part 8.2, extended per DECISIONS D-008.

``audit_log`` answers "who touched what **in my organization**" and is org-scoped
and RLS-protected. Some denials have no organization to belong to: an
authenticated user asking for an organization they are not a member of, or one
whose membership cannot be resolved at all. Those must still be queryable — they
are the shape of an account probing for tenants — so they land here.

This table is deliberately **not** tenant-scoped, and its organization column is
named ``attempted_org_id`` rather than ``org_id`` for two reasons: it records
what was asked for rather than what the row belongs to, and the RLS proof suite
treats any ``org_id`` column as a tenant scope that must be declared and
protected. Naming it truthfully keeps that guard meaningful.

Only a platform operator connecting as the owner reads this table; the
application role may insert and nothing more.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "dataagent_app"


def upgrade() -> None:
    op.create_table(
        "security_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=100), nullable=False),
        sa.Column("actor_subject", sa.String(length=255), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attempted_org_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("route", sa.String(length=200), nullable=True),
        sa.Column("method", sa.String(length=10), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "outcome IN ('denied', 'error')", name=op.f("ck_security_events_outcome_valid")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_security_events")),
    )
    op.execute("CREATE INDEX ix_security_events_ts ON security_events (ts DESC)")
    op.create_index(
        "ix_security_events_actor_subject", "security_events", ["actor_subject"], unique=False
    )
    op.create_index(
        "ix_security_events_attempted_org_id", "security_events", ["attempted_org_id"], unique=False
    )

    # Append-only for the application, exactly like audit_log: the process that
    # records a denial must not be able to erase it.
    op.execute(f"GRANT INSERT, SELECT ON security_events TO {APP_ROLE}")
    op.execute(f"REVOKE UPDATE, DELETE ON security_events FROM {APP_ROLE}")


def downgrade() -> None:
    op.drop_index("ix_security_events_attempted_org_id", table_name="security_events")
    op.drop_index("ix_security_events_actor_subject", table_name="security_events")
    op.drop_index("ix_security_events_ts", table_name="security_events")
    op.drop_table("security_events")
