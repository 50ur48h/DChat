"""conversations, messages, runs, events and findings — the product's own record

WP7.1, architecture Part 10.1 and 10.3. Five tenant tables, and between them they
hold everything a person asked, everything the agent did about it, and everything
it concluded. Phases 5 and 6 already record what was *read* and what was *spent*;
this is the revision that says what any of it was **for**.

Four things here are deliberate and worth reading before changing them.

**``agent_events`` is append-only, by grant.** The same lock ``audit_log`` has
carried since revision 0002: UPDATE and DELETE are revoked from the application
role, so a trace can be written and never rewritten. It matters more here than it
looks, because the trace is the product's honesty claim — architecture 10.3 makes
this table the single source of truth and SSE merely its live tail. A trace that
could be edited after the fact would be a story rather than a record.

**``seq`` is assigned under the run's own row lock.** ``UNIQUE (run_id, seq)``
says two events cannot share a position; it does not say who picks the next one.
``runs/events.py`` takes ``SELECT ... FOR UPDATE`` on the ``agent_runs`` row
before computing ``MAX(seq) + 1``, so concurrent writers on one run serialise on
the run itself. A counter column on ``agent_runs`` would do the same job; the row
lock was chosen because it needs no column the architecture does not have, and
because every event writer has to prove the run exists anyway.

**``query_executions.run_id`` and ``usage_ledger.run_id`` get their foreign key
here.** Both have carried the column since revisions 0010 and 0011 with a comment
saying a constraint pointing at a table that does not exist is not a constraint.
The table now exists. ``ON DELETE SET NULL``, for the reason D-016 gives about
data sources: a row that records an act outlives the thing it was about. What
this costs is stated plainly — **any run_id already written that names no run is
set to NULL by this migration**, because it never referenced anything and the
constraint cannot be added while it is there. On a fresh database that is a
no-op; on a machine that ran WP6.1's or WP6.2's smoke scripts it clears the
invented uuids they passed.

**Nulling those rows needs RLS out of the way for the length of one statement.**
Every tenant table FORCEs row-level security, which applies to the owner running
this migration too, and the policy dereferences ``app.org_id`` — unset here, and
unsettable to anything meaningful when the statement spans every organization. So
FORCE is lifted, the cleanup runs, and FORCE goes back on, inside this
migration's own transaction. It is the narrowest window that works and it is
visible; the alternative was a data fix nobody could see.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-15
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "dataagent_app"
POLICY = "org_isolation"

CONVERSATIONS = "conversations"
MESSAGES = "messages"
RUNS = "agent_runs"
EVENTS = "agent_events"
FINDINGS = "findings"

#: Newest first: this is also the order they are dropped in.
TENANT_TABLES = (FINDINGS, EVENTS, MESSAGES, RUNS, CONVERSATIONS)

#: Architecture 10.1's status domain, unabridged. ``validating`` is the critic's
#: window (Phase 9) and ``budget_exhausted`` is a distinct ending from ``failed``:
#: one is a run that ran out of its allowance and still owes the user an answer
#: with caveats (arch 4.4), the other is a run that broke.
RUN_STATUSES = (
    "queued",
    "running",
    "validating",
    "completed",
    "interrupted",
    "failed",
    "budget_exhausted",
)

#: Which of those mean the run is over. Copied into the model layer, which is
#: what decides whether a transition is legal.
TERMINAL_STATUSES = ("completed", "interrupted", "failed", "budget_exhausted")

MESSAGE_ROLES = ("user", "assistant")

#: Architecture 10.3's event types, verbatim and closed. A trace UI has to render
#: every one of them, so an unrecognised type is a bug rather than an extension
#: point — the CHECK constraint says so at the only place it could be introduced.
EVENT_TYPES = (
    "run_started",
    "intent_classified",
    "context_selected",
    "capability_checked",
    "plan_created",
    "step_started",
    "tool_called",
    "sql_validated",
    "sql_rejected",
    "query_executed",
    "result_summarized",
    "finding_added",
    "hypothesis_updated",
    "reflection",
    "critic_verdict",
    "budget_warning",
    "budget_exhausted",
    "answer_composed",
    "run_finished",
    "error",
)

#: How sure the agent is of a finding, in the words architecture 10.3's own
#: example payload uses.
CONFIDENCE_LEVELS = ("high", "medium", "low")

#: Tables whose ``run_id`` becomes a real foreign key in this revision.
RUN_REFERENCES = ("query_executions", "usage_ledger")


def _in_list(column: str, values: Sequence[str]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{value}'" for value in values))


def _uuid_pk() -> sa.Column[uuid.UUID]:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _jsonb(name: str, default: str) -> sa.Column[dict[str, object]]:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        server_default=sa.text(f"'{default}'::jsonb"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        CONVERSATIONS,
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        # The person whose conversation this is. SET NULL rather than CASCADE:
        # removing somebody from an organization must not delete the history of
        # what was asked in it, which is the same rule the audit trail follows.
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(300), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_conversations_org_id_created_at",
        CONVERSATIONS,
        ["org_id", sa.text("created_at DESC")],
    )

    op.create_table(
        RUNS,
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'queued'")),
        # The question as asked. Duplicated from the user's message on purpose
        # (architecture 10.1 puts it here): a run is read on its own by the trace,
        # by the eval harness and by a support question, and none of them should
        # need a join to find out what was being answered.
        sa.Column("question", sa.Text(), nullable=False),
        # What this run is allowed to spend (arch 4.4) and where it had got to
        # (arch 8.1's crash-resumable checkpoint). Both are Phase 8's to fill;
        # they exist from here so a run row never changes shape mid-phase.
        _jsonb("budget", "{}"),
        _jsonb("state", "{}"),
        # A rollup for the trace UI. `usage_ledger` stays authoritative — and
        # `cost_estimate` is nullable for the reason `cost_usd` is: null means
        # nobody has priced it, never that it was free.
        _jsonb("model_usage", "{}"),
        sa.Column("cost_estimate", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        #: Sanitized before it arrives, like every other error column here.
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], [f"{CONVERSATIONS}.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(_in_list("status", RUN_STATUSES), name="status_valid"),
        # A run that has ended says when. Stated as a constraint because
        # "finished but with no finish time" is the shape a crashed transition
        # leaves behind, and it would otherwise be invisible until a screen
        # rendered a blank.
        sa.CheckConstraint(
            "(status IN ({})) = (finished_at IS NOT NULL)".format(
                ", ".join(f"'{status}'" for status in TERMINAL_STATUSES)
            ),
            name="finished_at_matches_status",
        ),
    )
    op.create_index(
        "ix_agent_runs_org_id_conversation_id_created_at",
        RUNS,
        ["org_id", "conversation_id", sa.text("created_at DESC")],
    )

    op.create_table(
        MESSAGES,
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        # Beyond architecture 10.1's column list, and required by 10.2's contract
        # for this route: the body carries an idempotency key, which has to be
        # stored somewhere to be honoured. Here rather than on the run, because
        # it identifies the *client's* message — a retried POST is the same
        # question, not a second one. Null for anything the agent writes.
        sa.Column("idempotency_key", sa.String(200), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], [f"{CONVERSATIONS}.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], [f"{RUNS}.id"], ondelete="SET NULL"),
        sa.CheckConstraint(_in_list("role", MESSAGE_ROLES), name="role_valid"),
    )
    op.create_index(
        "ix_messages_org_id_conversation_id_created_at",
        MESSAGES,
        ["org_id", "conversation_id", "created_at"],
    )
    # What makes a retried POST cheap instead of a second billed run. Partial, so
    # the agent's own messages — which carry no key — are not competing for one
    # NULL slot per conversation.
    op.create_index(
        "uq_messages_idempotency_key",
        MESSAGES,
        ["org_id", "conversation_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        EVENTS,
        # bigserial per architecture 10.1: this table grows faster than any other
        # in the product — one row per step of every run — and must never be the
        # reason an integer runs out.
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        # 1-based and gap-free within a run. The replay contract depends on it:
        # `?after=seq` means "everything I have not seen", and a gap would make a
        # reconnecting client wait forever for a number that will never arrive.
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(40), nullable=False),
        # Built for eyes (arch 10.3): short public strings from structured tool
        # output. Never raw model reasoning, and never an unmasked value.
        _jsonb("payload", "{}"),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], [f"{RUNS}.id"], ondelete="CASCADE"),
        sa.CheckConstraint(_in_list("type", EVENT_TYPES), name="type_valid"),
        sa.CheckConstraint("seq > 0", name="seq_positive"),
        sa.UniqueConstraint("run_id", "seq", name="uq_agent_events_run_id_seq"),
    )
    # The only read this table has: "give me this run's events after seq N",
    # which is both the poll in Phase 7 and the SSE replay in Phase 8.
    op.create_index("ix_agent_events_org_id_run_id_seq", EVENTS, ["org_id", "run_id", "seq"])

    op.create_table(
        FINDINGS,
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        # Which executions back this up. A list of `query_executions.id` values,
        # so a claim in an answer can be walked back to the SQL that produced it —
        # the citation the M7 gate is about.
        sa.Column(
            "support",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("confidence", sa.String(10), nullable=False, server_default=sa.text("'medium'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], [f"{RUNS}.id"], ondelete="CASCADE"),
        sa.CheckConstraint(_in_list("confidence", CONFIDENCE_LEVELS), name="confidence_valid"),
    )
    op.create_index("ix_findings_org_id_run_id", FINDINGS, ["org_id", "run_id"])

    for table in TENANT_TABLES:
        # Revision 0002's ALTER DEFAULT PRIVILEGES already covers tables created
        # afterwards by the same owner. Stated again so this revision is true on
        # its own, as 0004, 0007, 0010 and 0011 do.
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {POLICY} ON {table}
            USING (org_id = current_setting('app.org_id')::uuid)
            WITH CHECK (org_id = current_setting('app.org_id')::uuid)
        """)

    # Append-only, enforced by the database rather than by discipline — the same
    # lock revision 0002 puts on `audit_log`, and for a stronger reason: this
    # table is what the product shows a user as proof of how an answer was
    # reached (architecture 10.3).
    op.execute(f"REVOKE UPDATE, DELETE ON {EVENTS} FROM {APP_ROLE}")

    _attach_run_foreign_keys()


def _attach_run_foreign_keys() -> None:
    """Give the two ``run_id`` columns written before this table existed a key.

    The cleanup below is the whole reason this is a function with a comment
    rather than two lines: a ``run_id`` that names no run cannot survive the
    constraint, and there is no way to invent the run it meant. Setting it to
    NULL says "this row belongs to no run", which is what was already true.
    """
    for table in RUN_REFERENCES:
        # FORCE applies row-level security to the owner as well, and the policy
        # dereferences app.org_id — unset here, and meaningless for a statement
        # that must cross every organization. Lifted for one statement, inside
        # this migration's transaction, and put straight back.
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            UPDATE {table} SET run_id = NULL
            WHERE run_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM {RUNS} WHERE {RUNS}.id = {table}.run_id)
        """)
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.create_foreign_key(
            f"fk_{table}_run_id_{RUNS}",
            table,
            RUNS,
            ["run_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    for table in RUN_REFERENCES:
        op.drop_constraint(f"fk_{table}_run_id_{RUNS}", table, type_="foreignkey")

    op.execute(f"GRANT UPDATE, DELETE ON {EVENTS} TO {APP_ROLE}")
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {table}")
        op.drop_table(table)
