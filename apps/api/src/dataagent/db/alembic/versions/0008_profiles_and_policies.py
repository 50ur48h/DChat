"""column profiles, detected sensitivity, and the policies an Admin decides

WP4.2, architecture Part 5.2 and 5.3. Two kinds of fact arrive here and they are
stored differently on purpose (DECISIONS D-013):

* **What the data looks like** — null fraction, distinct estimate, min and max,
  top values, the semantic role, and the sensitivity a classifier *suspects*.
  These describe one snapshot's sample of one column, so they live on
  ``catalog_columns`` and are rebuilt whenever a snapshot is.
* **What an Admin decided** — allow, mask or deny, and why. That is a judgement
  about a column by name, not about a snapshot, and it must outlive every
  re-discovery: a refresh that quietly reset somebody's masking decision would
  be a data leak caused by a routine operation. So ``column_policies`` is keyed
  by name and is never touched by discovery.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "dataagent_app"
POLICY = "org_isolation"
POLICIES = "column_policies"


def upgrade() -> None:
    # -- what the profiler learned about one snapshot's columns ---------------
    op.add_column("catalog_columns", sa.Column("null_frac", sa.Float(), nullable=True))
    op.add_column("catalog_columns", sa.Column("distinct_est", sa.BigInteger(), nullable=True))
    # Rendered as text: a min/max pair has to hold dates, numbers and strings,
    # and one typed column per possibility would be four columns that are always
    # three-quarters null.
    op.add_column("catalog_columns", sa.Column("min_val", sa.Text(), nullable=True))
    op.add_column("catalog_columns", sa.Column("max_val", sa.Text(), nullable=True))
    op.add_column(
        "catalog_columns",
        sa.Column("top_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "catalog_columns", sa.Column("semantic_role", sa.String(length=20), nullable=True)
    )
    op.add_column(
        "catalog_columns",
        sa.Column(
            "sensitivity", sa.String(length=20), server_default=sa.text("'none'"), nullable=False
        ),
    )
    op.add_column("catalog_columns", sa.Column("sample_rows", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "semantic_role_valid",
        "catalog_columns",
        "semantic_role IS NULL OR semantic_role IN ('measure', 'dimension', 'time', 'id', 'other')",
    )
    op.create_check_constraint(
        "sensitivity_valid",
        "catalog_columns",
        "sensitivity IN ('none', 'suspected', 'confirmed')",
    )

    # -- how far the profiler got before its budget ran out -------------------
    op.add_column(
        "catalog_snapshots",
        sa.Column(
            "profile_status",
            sa.String(length=20),
            server_default=sa.text("'none'"),
            nullable=False,
        ),
    )
    op.add_column(
        "catalog_snapshots", sa.Column("profiled_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "profile_status_valid",
        "catalog_snapshots",
        "profile_status IN ('none', 'partial', 'complete')",
    )

    # -- what an Admin decided, keyed by name so it survives a refresh --------
    op.create_table(
        POLICIES,
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("data_source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_name", sa.String(length=255), nullable=False),
        sa.Column("table_name", sa.String(length=255), nullable=False),
        sa.Column("column_name", sa.String(length=255), nullable=False),
        sa.Column("policy", sa.String(length=20), nullable=False),
        sa.Column("mask_type", sa.String(length=20), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "policy IN ('allow', 'mask', 'deny')", name=op.f("ck_column_policies_policy_valid")
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_column_policies_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["data_source_id"],
            ["data_sources.id"],
            name=op.f("fk_column_policies_data_source_id_data_sources"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
            name=op.f("fk_column_policies_decided_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_column_policies")),
    )
    op.create_index(
        "uq_column_policies_column",
        POLICIES,
        ["data_source_id", "schema_name", "table_name", "column_name"],
        unique=True,
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {POLICIES} TO {APP_ROLE}")
    op.execute(f"ALTER TABLE {POLICIES} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {POLICIES} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY {POLICY} ON {POLICIES}
        USING (org_id = current_setting('app.org_id')::uuid)
        WITH CHECK (org_id = current_setting('app.org_id')::uuid)
    """)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {POLICIES}")
    op.drop_table(POLICIES)

    op.drop_constraint("ck_catalog_snapshots_profile_status_valid", "catalog_snapshots")
    op.drop_column("catalog_snapshots", "profiled_at")
    op.drop_column("catalog_snapshots", "profile_status")

    op.drop_constraint("ck_catalog_columns_sensitivity_valid", "catalog_columns")
    op.drop_constraint("ck_catalog_columns_semantic_role_valid", "catalog_columns")
    for column in (
        "sample_rows",
        "sensitivity",
        "semantic_role",
        "top_values",
        "max_val",
        "min_val",
        "distinct_est",
        "null_frac",
    ):
        op.drop_column("catalog_columns", column)
