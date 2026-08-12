"""data_sources: registered customer databases

Architecture Part 10.1. A tenant table, so it arrives with its row-level security
policy in the same revision — the rule from WP1.2, enforced by
``test_no_tenant_table_can_be_added_without_protecting_it``, which asks the
database which tables carry ``org_id`` and fails on any that is undeclared or
unprotected.

The column that is absent matters as much as the ones present: there is no
password, no DSN and no connection string here. ``secret_ref`` names an entry in
the SecretsProvider, and is worthless without it.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "dataagent_app"
POLICY = "org_isolation"
TABLE = "data_sources"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("engine", sa.String(length=20), nullable=False),
        sa.Column("host_display", sa.String(length=300), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'registered'"), nullable=False
        ),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("secret_ref", sa.String(length=300), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("engine IN ('pg', 'mssql')", name=op.f("ck_data_sources_engine_valid")),
        sa.CheckConstraint(
            "status IN ('registered', 'verified', 'error')",
            name=op.f("ck_data_sources_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_data_sources_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_data_sources_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_sources")),
    )
    op.create_index("uq_data_sources_org_id_name", TABLE, ["org_id", "name"], unique=True)

    # Revision 0002's ALTER DEFAULT PRIVILEGES already covers tables created
    # afterwards by the same owner. Stated again here so this revision is true on
    # its own: a database restored or provisioned by a different owner role would
    # otherwise leave the API unable to read its own table.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} TO {APP_ROLE}")

    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY {POLICY} ON {TABLE}
        USING (org_id = current_setting('app.org_id')::uuid)
        WITH CHECK (org_id = current_setting('app.org_id')::uuid)
    """)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {TABLE}")
    op.drop_index("uq_data_sources_org_id_name", table_name=TABLE)
    op.drop_table(TABLE)
