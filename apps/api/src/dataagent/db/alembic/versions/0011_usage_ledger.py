"""usage_ledger: every call to a model, what it cost, and what it was for

WP6.1, architecture Part 4.9 and 8.3. One row per provider call — not per agent
run, not per user question. Anything coarser cannot answer the two questions this
table exists for: *is this organization within its quota* (8.3 checks it at run
start and at each call) and *where is the money actually going*, which is the
question model tiering is the lever for.

``role`` and ``tier`` are on the row rather than derivable from ``model``,
because the map from role to model is configuration and changes: a ledger that
recorded only the model could not tell you afterwards that the spend came from
``observe`` back when it was on the strong tier. The whole point of 8.3's
tiering claim is a before-and-after comparison, and this is what makes it
possible.

Failures are recorded too, with ``status = 'error'``. A provider that fails after
generating tokens has still spent them, and a 429 that produced nothing is the
signal that a fallback happened — with WP6.2's chain, one attempt becomes an
``error`` row and the next an ``ok`` row, so the ledger shows the switch rather
than hiding it.

``cost_usd`` is nullable and null means **not priced**, never free. Prices are
configuration (``LLM_PRICES``); a model nobody has priced yet must not
contribute zero to a total that a quota is enforced from.

Nothing here holds a prompt or a completion. Those are the customer's question
and the model's answer — they belong to the run's own event log (Phase 7), under
its retention, not in a cost table that will be aggregated and kept for years.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "dataagent_app"
POLICY = "org_isolation"

LEDGER = "usage_ledger"

TENANT_TABLES = (LEDGER,)

#: Architecture 4.9's roles. Copied rather than imported: a migration must mean
#: the same thing in a year, when the application's own list has moved on.
#: ``dataagent.llm.base.ROLES`` is the live copy and a test asserts they agree.
ROLES = ("intake", "observe", "plan", "sql", "critic", "compose")

TIERS = ("small", "mid", "strong")

#: ok — the provider answered. error — it did not, and ``error`` says so.
STATUSES = ("ok", "error")


def _in_list(column: str, values: Sequence[str]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{value}'" for value in values))


def upgrade() -> None:
    op.create_table(
        LEDGER,
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        # No foreign key: `agent_runs` arrives in Phase 7, and a constraint
        # pointing at a table that does not exist is not a constraint. Same
        # reasoning as revision 0010's `run_id`.
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("tier", sa.String(10), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        # True when the counts came from our own arithmetic rather than from the
        # provider. Kept separate so a total can say how much of it is measured.
        sa.Column("tokens_estimated", sa.Boolean(), nullable=False, server_default=sa.false()),
        # Null means unpriced, not free.
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        # The second call of a parse-then-repair pair. Makes "how often does the
        # model fail to follow a schema" a GROUP BY rather than a guess.
        sa.Column("repaired", sa.Boolean(), nullable=False, server_default=sa.false()),
        #: Sanitized before it arrives: providers raise nothing that has not been.
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(_in_list("role", ROLES), name="role_valid"),
        sa.CheckConstraint(_in_list("tier", TIERS), name="tier_valid"),
        sa.CheckConstraint(_in_list("status", STATUSES), name="status_valid"),
        # A failure that does not say what failed is not a record of anything —
        # the same rule revision 0010 applies to refusals.
        sa.CheckConstraint("(status = 'error') = (error IS NOT NULL)", name="error_matches_status"),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0", name="token_counts_non_negative"
        ),
    )
    # Quotas are windows: "this organization's tokens today, this month". Both
    # are a range scan on this index.
    op.create_index(
        "ix_usage_ledger_org_id_created_at", LEDGER, ["org_id", sa.text("created_at DESC")]
    )
    # "What did this run cost", which the trace UI shows and the budget in Phase 8
    # reads back after a crash.
    op.create_index("ix_usage_ledger_org_id_run_id", LEDGER, ["org_id", "run_id"])

    for table in TENANT_TABLES:
        # Revision 0002's ALTER DEFAULT PRIVILEGES already covers tables created
        # afterwards by the same owner. Stated again so this revision is true on
        # its own, as 0004, 0007 and 0010 do.
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
    op.drop_table(LEDGER)
