"""query executions and result artifacts: what the DAL did, and what it returned

WP5.2b, architecture Part 10.1 and 8.2. Every attempt to read a customer's
database leaves a row here — the ones that succeeded, the ones the engine
refused, and the ones this service refused before the engine was asked. The
refusals are the most interesting rows in the table and the easiest to forget to
write, so ``status`` has three values rather than a boolean.

Two columns beyond the architecture's list, both because 8.2 asks questions the
list cannot answer:

* ``actor_user_id`` — 8.2 says an execution records *who*, and "who accessed
  what, when, and was it sensitive" is the query this table exists for.
* ``violation_code`` — so "what did we refuse this week, and why" is a GROUP BY
  rather than a text search through sanitized messages.

``data_source_id`` is nullable and set to NULL when a source is removed. An
audit trail that disappears when somebody deletes the thing it is about is not
an audit trail; the address and the name are already in the row's own text.

Nothing here holds a credential, and nothing holds an unmasked value. The rows
in ``result_artifacts.sample_rows`` have been through ``dal/masking.py`` — there
is no unmasked copy anywhere for a later bug to find, which is the same rule
catalog samples follow (DECISIONS D-013).

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-13
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "dataagent_app"
POLICY = "org_isolation"

EXECUTIONS = "query_executions"
ARTIFACTS = "result_artifacts"

#: Newest first, because that is the order they are dropped in.
TENANT_TABLES = (ARTIFACTS, EXECUTIONS)

#: ok — it ran. error — the engine or the connection failed. refused — this
#: service would not send it, which is a security event and not an error.
STATUSES = ("ok", "error", "refused")


def _uuid_pk() -> sa.Column[uuid.UUID]:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        EXECUTIONS,
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        # SET NULL rather than CASCADE: removing a data source must not erase
        # the record of what was read from it.
        sa.Column("data_source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        # No foreign key yet: `runs` arrives in Phase 7, and a constraint
        # pointing at a table that does not exist is not a constraint.
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        # The canonical statement for anything that ran, and the submitted one
        # for anything refused — a refusal has no canonical form, because
        # canonicalising is what it did not get to.
        sa.Column("sql_text", sa.Text(), nullable=False),
        sa.Column("sql_hash", sa.String(64), nullable=False),
        sa.Column(
            "tables",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "columns",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("violation_code", sa.String(40), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        #: Sanitized before it arrives: a connector error has already been
        #: through the sanitizer, and a violation message is written to be shown.
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sensitive_accessed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["data_source_id"], ["data_sources.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status IN ({})".format(", ".join(f"'{status}'" for status in STATUSES)),
            name="status_valid",
        ),
        # A refusal has a code; anything else does not. Stated as a constraint so
        # a row that claims to be a refusal without saying what was refused
        # cannot exist.
        sa.CheckConstraint(
            "(status = 'refused') = (violation_code IS NOT NULL)",
            name="violation_code_matches_status",
        ),
    )
    # The two questions this table is read with: "what has this organization run
    # lately" and "how often does this exact statement appear".
    op.create_index(
        "ix_query_executions_org_id_created_at",
        EXECUTIONS,
        ["org_id", sa.text("created_at DESC")],
    )
    op.create_index("ix_query_executions_org_id_sql_hash", EXECUTIONS, ["org_id", "sql_hash"])

    op.create_table(
        ARTIFACTS,
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("query_execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        #: Shape rather than content: column names, row count, truncation.
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        #: Already masked. There is no unmasked copy of this anywhere.
        sa.Column(
            "sample_rows",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        #: Where the full result lives, in whatever store is configured — a path
        #: under the local artifact root now, a Blob name in Phase 12. Null when
        #: the sample *is* the whole result.
        sa.Column("storage_ref", sa.String(500), nullable=True),
        #: Retention is a promise to the customer, so it is a column rather than
        #: a policy someone remembers to apply (architecture Part 7.6).
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["query_execution_id"], [f"{EXECUTIONS}.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_result_artifacts_execution", ARTIFACTS, ["query_execution_id"])
    # Retention sweeps read this, and they read it across organizations as the
    # owner, so it is deliberately not org-prefixed.
    op.create_index("ix_result_artifacts_expires_at", ARTIFACTS, ["expires_at"])

    for table in TENANT_TABLES:
        # Revision 0002's ALTER DEFAULT PRIVILEGES already covers tables created
        # afterwards by the same owner. Stated again so this revision is true on
        # its own, as 0004 and 0007 do.
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
    op.drop_table(ARTIFACTS)
    op.drop_table(EXECUTIONS)
