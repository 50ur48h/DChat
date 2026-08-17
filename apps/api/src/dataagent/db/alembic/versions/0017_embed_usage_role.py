"""usage_ledger accepts the `embed` role, so embedding spends land in the ledger

WP10.1a. One sentence of schema and a paragraph of reason.

**WP6.1's rule is that no path spends tokens without writing a `usage_ledger`
row**, and embedding a document corpus spends real tokens — more, for a large
upload, than the chat calls that answer a question about it. Leaving that outside
the ledger would make *"what did this organization spend"* unanswerable from the
one table built to answer it, and would leave B-025's org quotas counting a
fraction of the bill.

**Why a new tier and not `small`.** D-018 says a role names a *tier*, and a tier
is "how much model this job is worth" on a small/mid/strong ladder. Embeddings
have no such ladder: there is one embedding model, chosen by
``EMBEDDINGS_MODEL``, and it is not a cheaper version of anything. Recording it
as `small` would put embedding tokens into the same bucket as intake and observe
calls, so any query grouping spend by tier would quietly report chat costs that
were never incurred. `embed` is its own value in both enumerations, which keeps
the column honest and makes the grouping query correct without a special case.

Nothing is backfilled because nothing existed to backfill: this widens what the
CHECK constraints accept and touches no row.

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-17
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEDGER = "usage_ledger"

#: The Phase 6 vocabulary, plus `embed`. Written out rather than imported from
#: `dataagent.db.models`, for the reason revision 0013 gives: a migration that
#: imports application code stops meaning the same thing the moment that code
#: moves on, and a migration has to keep meaning what it meant when it ran.
ROLES_BEFORE = ("intake", "observe", "plan", "sql", "critic", "compose")
ROLES_AFTER = (*ROLES_BEFORE, "embed")
TIERS_BEFORE = ("small", "mid", "strong")
TIERS_AFTER = (*TIERS_BEFORE, "embed")


def _in_list(column: str, values: Sequence[str]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{value}'" for value in values))


def _replace(constraint: str, column: str, values: Sequence[str]) -> None:
    op.drop_constraint(constraint, LEDGER, type_="check")
    op.create_check_constraint(constraint, LEDGER, _in_list(column, values))


def upgrade() -> None:
    _replace("role_valid", "role", ROLES_AFTER)
    _replace("tier_valid", "tier", TIERS_AFTER)


def downgrade() -> None:
    # Any row this revision made possible has to go before the narrower
    # constraint can be restored, or the downgrade fails on data it created —
    # which is the same courtesy revision 0012 paid, and stated for the same
    # reason: a downgrade that cannot run is not a downgrade.
    #
    # FORCE row-level security applies to the owner running this too, and the
    # policy dereferences `app.org_id`, which is unset here and meaningless for a
    # statement spanning every organization. So FORCE is lifted for exactly one
    # statement, inside this migration's own transaction.
    op.execute(f"ALTER TABLE {LEDGER} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"DELETE FROM {LEDGER} WHERE role = 'embed' OR tier = 'embed'")
    op.execute(f"ALTER TABLE {LEDGER} FORCE ROW LEVEL SECURITY")
    _replace("role_valid", "role", ROLES_BEFORE)
    _replace("tier_valid", "tier", TIERS_BEFORE)
