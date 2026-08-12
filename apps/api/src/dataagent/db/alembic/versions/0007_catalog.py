"""catalog_snapshots, catalog_tables, catalog_columns, catalog_relationships

WP4.1, architecture Part 5.2 and 5.3 and 10.1. What one crawl of a customer database
learned, versioned: a snapshot is the unit of consistency (a run reasons about
one) and the unit of change (a crawl that finds nothing new creates none) —
DECISIONS D-012.

Four tenant tables, so four row-level security policies in this same revision.
That is not diligence, it is the rule from WP1.2, and
``test_no_tenant_table_can_be_added_without_protecting_it`` asks the database
which tables carry ``org_id`` and fails on any that is undeclared or unprotected.

The columns Phase 4.2 and 4.3 fill — profiles, sensitivity, cards, embeddings —
are deliberately absent. They arrive in the revision that has code to write them.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-12
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "dataagent_app"
POLICY = "org_isolation"

SNAPSHOTS = "catalog_snapshots"
TABLES = "catalog_tables"
COLUMNS = "catalog_columns"
RELATIONSHIPS = "catalog_relationships"

#: Newest first, because that is the order they are dropped in.
TENANT_TABLES = (RELATIONSHIPS, COLUMNS, TABLES, SNAPSHOTS)


def _uuid_pk() -> sa.Column[uuid.UUID]:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        SNAPSHOTS,
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("data_source_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Monotonic per data source. Version 1 is the first crawl that found
        # anything; a crawl that changes nothing does not spend a version.
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'building'"), nullable=False
        ),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("object_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        # Sanitized before it gets here: a failed crawl records why without
        # recording a DSN (architecture Part 5.1).
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('building', 'active', 'failed', 'superseded')",
            name=op.f("ck_catalog_snapshots_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_catalog_snapshots_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["data_source_id"],
            ["data_sources.id"],
            name=op.f("fk_catalog_snapshots_data_source_id_data_sources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_snapshots")),
    )
    op.create_index(
        "uq_catalog_snapshots_data_source_id_version",
        SNAPSHOTS,
        ["data_source_id", "version"],
        unique=True,
    )
    # One active snapshot per data source, enforced rather than assumed: "which
    # catalog is current" must have exactly one answer.
    op.create_index(
        "uq_catalog_snapshots_one_active",
        SNAPSHOTS,
        ["data_source_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        TABLES,
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_name", sa.String(length=255), nullable=False),
        sa.Column("table_name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        # sha256 over this table's shape — columns, types, nullability, keys. The
        # whole of incremental refresh turns on comparing these.
        sa.Column("structural_hash", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.CheckConstraint("kind IN ('table', 'view')", name=op.f("ck_catalog_tables_kind_valid")),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_catalog_tables_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            [f"{SNAPSHOTS}.id"],
            name=op.f("fk_catalog_tables_snapshot_id_catalog_snapshots"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_tables")),
    )
    op.create_index(
        "uq_catalog_tables_snapshot_id_schema_name_table_name",
        TABLES,
        ["snapshot_id", "schema_name", "table_name"],
        unique=True,
    )

    op.create_table(
        COLUMNS,
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("table_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("data_type", sa.String(length=255), nullable=False),
        sa.Column("nullable", sa.Boolean(), nullable=False),
        sa.Column("is_pk", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_catalog_columns_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["table_id"],
            [f"{TABLES}.id"],
            name=op.f("fk_catalog_columns_table_id_catalog_tables"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_columns")),
    )
    op.create_index("uq_catalog_columns_table_id_name", COLUMNS, ["table_id", "name"], unique=True)

    op.create_table(
        RELATIONSHIPS,
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("constraint_name", sa.String(length=255), nullable=False),
        sa.Column("from_schema", sa.String(length=255), nullable=False),
        sa.Column("from_table", sa.String(length=255), nullable=False),
        sa.Column("from_columns", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("to_schema", sa.String(length=255), nullable=False),
        sa.Column("to_table", sa.String(length=255), nullable=False),
        sa.Column("to_columns", postgresql.ARRAY(sa.Text()), nullable=False),
        # WP4.1 records only what the engine declares. Inferred edges — name
        # convention plus type match — arrive with the profiler that can score
        # them (architecture Part 5.2).
        sa.Column(
            "kind", sa.String(length=20), server_default=sa.text("'declared'"), nullable=False
        ),
        sa.Column(
            "confidence",
            sa.Numeric(precision=3, scale=2),
            server_default=sa.text("1.0"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('declared', 'inferred')", name=op.f("ck_catalog_relationships_kind_valid")
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_catalog_relationships_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            [f"{SNAPSHOTS}.id"],
            name=op.f("fk_catalog_relationships_snapshot_id_catalog_snapshots"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_relationships")),
    )
    op.create_index(
        "ix_catalog_relationships_snapshot_id_from_table",
        RELATIONSHIPS,
        ["snapshot_id", "from_schema", "from_table"],
    )

    for table in TENANT_TABLES:
        # Revision 0002's ALTER DEFAULT PRIVILEGES already covers tables created
        # afterwards by the same owner. Stated again so this revision is true on
        # its own, as 0004 does.
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {POLICY} ON {table}
            USING (org_id = current_setting('app.org_id')::uuid)
            WITH CHECK (org_id = current_setting('app.org_id')::uuid)
        """)


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {table}")
    op.drop_table(RELATIONSHIPS)
    op.drop_table(COLUMNS)
    op.drop_table(TABLES)
    op.drop_table(SNAPSHOTS)
