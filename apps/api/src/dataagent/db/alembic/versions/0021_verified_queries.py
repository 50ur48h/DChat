"""verified_queries — Admin-approved question→SQL pairs, as few-shot grounding

Architecture Part 5.4's other half, and the one it calls *"the highest-leverage
accuracy feature per dollar"*. A semantic definition says what a word means; a
verified query shows what a good answer to a question **looks like** in this
database — which join, which grain, which date column, which of the four
plausible tables is the one people actually use.

**Why a separate table rather than a column on `semantic_definitions`.** They
are enforced differently, and that is the distinction the whole semantic layer
turns on (D-033). A definition's `required_filters` **bind**: the critic checks
the AST and blocks a statement that ignores them. A verified query **informs**:
it is an example the planner is shown, and nothing checks that the model
followed it, because a question near an example is not the same question and
demanding the same SQL would be a false block on a correct answer. Two things
with two enforcement models do not belong in one row.

**Validated at save time, by the validator that guards execution.** An Admin
cannot bless a statement this platform would refuse to run: the same
`dal.validator.validate` that stands in front of every query judges it here,
against this data source's own catalog. That matters more for an example than
for an ordinary query — an approved statement naming a table that does not exist
is not merely broken, it is a worked demonstration of hallucination sitting in
the prompt, teaching the shape it was added to prevent.

**`retired` rather than deletion**, as everywhere else here: an answer that
was grounded in an example last month should still be explainable this month.

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "dataagent_app"
POLICY = "org_isolation"
VERIFIED = "verified_queries"

#: `active` is shown to the planner; `retired` is kept and shown to nobody.
VERIFIED_STATUSES = ("active", "retired")


def _in_list(column: str, values: Sequence[str]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{value}'" for value in values))


def upgrade() -> None:
    op.create_table(
        VERIFIED,
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("data_source_id", postgresql.UUID(as_uuid=True), nullable=False),
        # The question in the words a person would ask it. Matched against a new
        # question by overlap, so the phrasing is load-bearing rather than a
        # label: "which shop sold the most last month" retrieves the example,
        # "q17" does not.
        sa.Column("question", sa.Text(), nullable=False),
        # The approved statement. Validated against this data source's catalog
        # before the row is written, by the same validator that guards execution.
        sa.Column("sql", sa.Text(), nullable=False),
        # Why this shape is the right one — "join through shop_id, not name" —
        # rendered with the example. Optional, and worth more than it looks:
        # an example without its reason teaches the SQL and not the judgement.
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'active'")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        # The example names this database's tables, so it goes when the database
        # does — the same reasoning as `semantic_definitions` in 0020.
        sa.ForeignKeyConstraint(["data_source_id"], ["data_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(_in_list("status", VERIFIED_STATUSES), name="status_valid"),
        # One approved answer per question per database. Two would make "which
        # example is authoritative" a question with no answer, and the planner
        # would be shown whichever the index returned.
        sa.UniqueConstraint(
            "data_source_id", "question", name="uq_verified_queries_data_source_id_question"
        ),
    )
    op.create_index(
        "ix_verified_queries_org_id_data_source_id", VERIFIED, ["org_id", "data_source_id"]
    )

    # Revision 0002's ALTER DEFAULT PRIVILEGES already covers tables created
    # afterwards by the same owner. Stated again so this revision is true on its
    # own, as 0004, 0007, 0010, 0011, 0012, 0016 and 0020 do.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {VERIFIED} TO {APP_ROLE}")
    op.execute(f"ALTER TABLE {VERIFIED} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {VERIFIED} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY {POLICY} ON {VERIFIED}
        USING (org_id = current_setting('app.org_id')::uuid)
        WITH CHECK (org_id = current_setting('app.org_id')::uuid)
    """)


def downgrade() -> None:
    op.drop_table(VERIFIED)
