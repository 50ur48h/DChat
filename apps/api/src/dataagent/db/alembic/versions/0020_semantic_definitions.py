"""semantic_definitions — what a metric means here, in a form a check can read

WP10.2c, architecture Part 5.4, and DECISIONS **D-033**: *prose informs the
model, a structured definition binds it.* WP10.2a let the agent read a policy
document mid-run and WP10.2b made an answer resting on one say that nothing
checked it. This is the other half — a definition the **critic** can enforce,
which is the only kind that binds.

**Why the filters are structured and the description is not.** A definition
carries both: `description` is prose for the prompt, because that is what makes a
model use the metric correctly in the first place, and `required_filters` is a
list of `{table, column, op, values}` objects, because that is what an AST check
can compare a statement against. **B-078** is the argument for the split: given
the same definition as prose, a live model wrote it into its SQL and then
reasoned its way back out two iterations later, answering 1,054 where the
document said 747. Nothing could object, because a paragraph has no filters to
compare against.

**`status`, because an imported definition is a proposal.** WP10.2d points the
import at a customer's own metric tables (**B-059**), and an imported definition
**constrains generated SQL** — it is a privileged object, not data a crawler may
trust. So it arrives `proposed` and an Admin blesses it to `active`; only
`active` definitions are ever loaded. The column exists now rather than in the
revision that fills it because the loader already has to filter on it, which is
the difference between a field with a consumer and a field without one (WP7.1's
objection).

**`provenance`, for the same WP.** A definition records where it came from — a
source, a table, a snapshot — so that when the customer's own table changes, the
drift is visible rather than silently stale. Null for one somebody typed.

**Scoped to a data source, not to an organization.** A definition names columns,
and columns belong to a database. An organization with a pizza warehouse and an
F&B warehouse has two different things called `net_revenue`, and a definition
that applied to both would validate against one catalog and mislead against the
other.

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "dataagent_app"
POLICY = "org_isolation"
DEFINITIONS = "semantic_definitions"

#: A metric is something you aggregate; a dimension is something you group by.
#: Architecture 5.4's two, and a CHECK rather than a lookup table because the set
#: is small, closed, and changing it is a migration either way.
DEFINITION_KINDS = ("metric", "dimension")

#: `proposed` until an Admin says otherwise (WP10.2d). `retired` rather than
#: deletion, so a run that cited a definition last month can still explain
#: itself — the same reason D-016 keeps an audit row past its subject.
DEFINITION_STATUSES = ("proposed", "active", "retired")


def _in_list(column: str, values: Sequence[str]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{value}'" for value in values))


def upgrade() -> None:
    op.create_table(
        DEFINITIONS,
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("data_source_id", postgresql.UUID(as_uuid=True), nullable=False),
        # The name a question is matched against — `net_revenue`. Lowercased by
        # the service, because "Net Revenue" and "net_revenue" are the same
        # metric and a catalog of near-duplicates is worse than none.
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default=sa.text("'metric'")),
        # Prose, for the prompt. This is what makes a model use the metric
        # correctly; the filters below are what catch it when it does not.
        sa.Column("description", sa.Text(), nullable=False),
        # `sum(orders.total_amount)` — how the metric is computed, rendered into
        # the prompt as guidance. Not parsed and not enforced: what is enforced
        # is `required_filters`, and claiming to check an expression this
        # revision cannot compare would be worse than saying so.
        sa.Column("expression", sa.Text(), nullable=True),
        # `[{"table": "orders", "column": "status", "op": "not_in",
        #    "values": ["cancelled", "refunded"]}]`
        sa.Column(
            "required_filters",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # The words a person might use for it — "net revenue", "revenue net of
        # refunds". Matching on the bare name alone would miss every question a
        # human actually types.
        sa.Column(
            "synonyms",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # Where it came from, when it was not typed: `{"kind": "import",
        # "table": "meta_metric", "snapshot_id": …}` (B-059, WP10.2d).
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        # The definition describes this database's columns, so it goes when the
        # database does. Unlike a record of an act (D-016), it has no meaning
        # once the thing it describes is gone.
        sa.ForeignKeyConstraint(["data_source_id"], ["data_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(_in_list("kind", DEFINITION_KINDS), name="kind_valid"),
        sa.CheckConstraint(_in_list("status", DEFINITION_STATUSES), name="status_valid"),
        # One meaning per name per database. Two active definitions of
        # `net_revenue` would make "which one binds" a question with no answer,
        # and the critic would enforce whichever the query happened to return.
        sa.UniqueConstraint(
            "data_source_id", "name", name="uq_semantic_definitions_data_source_id_name"
        ),
    )
    op.create_index(
        "ix_semantic_definitions_org_id_data_source_id",
        DEFINITIONS,
        ["org_id", "data_source_id"],
    )

    # Revision 0002's ALTER DEFAULT PRIVILEGES already covers tables created
    # afterwards by the same owner. Stated again so this revision is true on its
    # own, as 0004, 0007, 0010, 0011, 0012 and 0016 do.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {DEFINITIONS} TO {APP_ROLE}")
    op.execute(f"ALTER TABLE {DEFINITIONS} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {DEFINITIONS} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY {POLICY} ON {DEFINITIONS}
        USING (org_id = current_setting('app.org_id')::uuid)
        WITH CHECK (org_id = current_setting('app.org_id')::uuid)
    """)


def downgrade() -> None:
    op.drop_table(DEFINITIONS)
