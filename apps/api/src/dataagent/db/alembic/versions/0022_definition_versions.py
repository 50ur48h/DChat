"""semantic_definition_versions — every state a definition has been in (B-088)

Until this revision a definition was **write-once**. There was no edit, no
un-accept, and `accept` refuses anything that is not still `proposed`, so the
only way to correct a filter was to delete the row in `psql` and import the
table again. The owner hit that during the Phase 10 gate walk: *"a semantic
layer whose definitions are write-once will not survive real use."* Editing is
B-088; this table is the half of it that has to exist first.

**Why history rather than an overwrite.** Architecture 5.4 says definitions are
*"validated against the catalog at save time and versioned"*, and nothing here
versioned anything. That is not pedantry once an edit exists: a definition
**binds** — its `required_filters` are enforced against the AST of generated SQL
— so *"what did this metric require when that answer was written"* is a question
about whether an answer was right, not a curiosity. An overwrite makes it
unanswerable, and the moment editing ships is the moment every unrecorded edit
starts accumulating. D-036 records the decision.

**What a version is.** One row per state the definition has been *in force* in:
written by hand (`created`), blessed from a proposal (`accepted`), corrected
(`updated`), or taken out of force (`retired`). A proposal is not a version —
it binds nothing while it waits, and numbering the sentences an Admin has not
yet agreed to would make version 1 mean two different things.

**Append-only, and the app role is granted accordingly.** SELECT and INSERT and
no more: a history a running process may rewrite is not one. The same argument
`audit_log` makes, and the app role's grants there are the precedent.

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "dataagent_app"
POLICY = "org_isolation"
DEFINITIONS = "semantic_definitions"
VERSIONS = "semantic_definition_versions"

#: What put the definition into this state. `accepted` and `created` are both
#: first versions and are told apart because the provenance differs: one is an
#: Admin's own sentence, the other is the customer's, blessed.
DEFINITION_CHANGES = ("created", "accepted", "updated", "retired")


def _in_list(column: str, values: Sequence[str]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{value}'" for value in values))


def upgrade() -> None:
    # The live row's version, so a reader of `semantic_definitions` alone knows
    # which history row it is looking at. Existing definitions become version 1
    # by the server default; their history begins at the first edit, which is
    # honest — nothing recorded what they said before this revision.
    op.add_column(
        DEFINITIONS,
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )

    op.create_table(
        VERSIONS,
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        # What the definition said at this version, in full rather than as a
        # diff. A diff is smaller and cannot answer the only question this table
        # exists for without replaying every row before it.
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("expression", sa.Text(), nullable=True),
        sa.Column(
            "required_filters",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "synonyms", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("change", sa.String(20), nullable=False),
        # Who did it. `SET NULL` rather than cascade, as everywhere else: a
        # departed employee's edits stay in the history, unattributed rather
        # than erased.
        sa.Column("changed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        # The history goes when the definition does. Keeping orphaned versions
        # of a deleted data source would be keeping the shape of a customer's
        # schema after they asked for it to go.
        sa.ForeignKeyConstraint(["definition_id"], [f"{DEFINITIONS}.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["changed_by"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(_in_list("change", DEFINITION_CHANGES), name="change_valid"),
        # One row per version. Two would make "what did it say at version 3" a
        # question with two answers, which is the one thing this table is for.
        sa.UniqueConstraint(
            "definition_id", "version", name="uq_semantic_definition_versions_definition_id_version"
        ),
    )
    op.create_index(
        "ix_semantic_definition_versions_org_id_definition_id",
        VERSIONS,
        ["org_id", "definition_id"],
    )

    # **SELECT and INSERT, deliberately not UPDATE or DELETE.** Revision 0002's
    # ALTER DEFAULT PRIVILEGES grants the app role all four on tables the owner
    # creates later, so this revokes what it does not want rather than merely
    # declining to grant it — the difference between a history and a log the
    # application can quietly repair.
    op.execute(f"REVOKE ALL ON {VERSIONS} FROM {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON {VERSIONS} TO {APP_ROLE}")
    op.execute(f"ALTER TABLE {VERSIONS} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {VERSIONS} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY {POLICY} ON {VERSIONS}
        USING (org_id = current_setting('app.org_id')::uuid)
        WITH CHECK (org_id = current_setting('app.org_id')::uuid)
    """)


def downgrade() -> None:
    op.drop_table(VERSIONS)
    op.drop_column(DEFINITIONS, "version")
