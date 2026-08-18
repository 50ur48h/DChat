"""agent_events accepts `knowledge_consulted` — the agent asking its documents

WP10.2a, **B-075**, DECISIONS **D-032**. One CHECK constraint and a paragraph of
reason.

Architecture 10.3 fixes the trace vocabulary at twenty types and calls an
unrecognised one a bug rather than an extension point — deliberately, because a
trace UI has to render each one and a type nobody wrote a renderer for shows a
person nothing. Widening that list is therefore a decision rather than a
convenience, and this is the argument for it.

**The Phase 10 gate turns on a person being able to see a document consulted
mid-run** (owner's direction, 2026-08-18). Until this WP the corpus could not
reach a run at all: `search_knowledge` was registered, described in every prompt,
and never dispatched. Now the planner can ask what a term means and the loop
answers from the organization's own writing — and the claim that this *happened*
has to be checkable in the trace, not taken on trust.

**`tool_called` records the asking and not the answer.** The registry already
emits it, carrying the tool's name and its safe arguments, which is exactly the
right amount for a tool whose result is a query execution recorded separately.
Here there is no execution: what came back is prose, and the three facts a reader
needs — what was asked, whether anything was written down, and which documents
answered — have nowhere else to go. Overloading `result_summarized`, whose whole
meaning is "a query result was reduced to a line", would make the timeline lie
about what kind of step it was.

Nothing is backfilled because nothing existed to backfill: this widens what the
constraint accepts and touches no row. `agent_events` is append-only by grant
(revision 0012), and this does not change that — the grant is unaffected.

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-18
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENTS = "agent_events"
CONSTRAINT = "type_valid"

#: Written out rather than imported from `dataagent.db.models`, for the reason
#: revision 0013 gives: a migration that imports application code stops meaning
#: what it meant the moment that code moves on, and a migration has to keep
#: meaning what it meant when it ran.
TYPES_BEFORE = (
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

#: Inserted after `tool_called`, because that is what it is the result of. The
#: order is not cosmetic: `EVENT_TYPES` in `db/models.py` and `EventType` in
#: `runs/events.py` are asserted to be the same list **in the same order**, so a
#: type appended in one place and inserted in another fails that test rather than
#: drifting quietly.
TYPES_AFTER = (
    *TYPES_BEFORE[:7],
    "knowledge_consulted",
    *TYPES_BEFORE[7:],
)


def _in_list(column: str, values: Sequence[str]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{value}'" for value in values))


def _replace(values: Sequence[str]) -> None:
    op.drop_constraint(CONSTRAINT, EVENTS, type_="check")
    op.create_check_constraint(CONSTRAINT, EVENTS, _in_list("type", values))


def upgrade() -> None:
    _replace(TYPES_AFTER)


def downgrade() -> None:
    # Rows of the new type would violate the narrower constraint, so they go
    # first. Deleting from an append-only table is something only a migration
    # running as the owner can do, and it is the honest behaviour here: the
    # alternative is a downgrade that fails on any database where the feature
    # was used, which is every database worth downgrading.
    op.execute(f"DELETE FROM {EVENTS} WHERE type = 'knowledge_consulted'")
    _replace(TYPES_BEFORE)
