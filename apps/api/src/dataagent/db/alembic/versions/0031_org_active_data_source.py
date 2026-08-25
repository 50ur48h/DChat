"""organizations.active_data_source_id — an Admin chooses the database once (D-045)

**Where the choice is made moves; whether it is recorded does not.** D-022 put
`data_source_id` on `conversations` because a thread is about one database, and
that reasoning is untouched: every conversation still records the source it used,
and a follow-up still reaches the same database as the question it follows. What
changes is who names it. A member picking a database before they may ask a
question is a database tool's flow; the product is a chat product, and the
database is something an Admin configures once.

**A column with a foreign key, not a key in `organizations.settings`.** The JSONB
column is already there and would have held the id with no migration at all — and
no referential integrity, so a deleted source would leave a plausible id behind
for every reader to defensively re-check. `ON DELETE SET NULL` is the same trade
D-022 made one table over: the pointer degrades to "none named", which is a state
the resolver already handles correctly, rather than to a dangling id, which is a
state nothing handles.

**Nullable, and null is not an error.** It is every organization that existed
before this revision and every one whose Admin has not chosen yet. Such an
organization behaves exactly as it does today: `resolve_data_source` still
resolves a single registered source, and still refuses rather than guesses when
there is more than one. **The refusal is kept, not replaced** — what closes the
ambiguity is an Admin *saying* which database the organization means, not
permission for the platform to guess. That was D-022's sentence and it survives
this revision word for word.

**The foreign key is not the tenant check.** A constraint check does not consult
row-level security, so another organization's source id satisfies the database
perfectly well. `orgs/service.set_active_data_source` looks the id up through the
org session and answers 404, exactly as `runs/service._named_data_source` does,
and a test registers a second organization's source and proves it.

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("active_data_source_id", sa.Uuid(), nullable=True),
    )
    # The cycle is deliberate and Postgres is fine with it: `data_sources.org_id`
    # already points the other way with ON DELETE CASCADE. Dropping an
    # organization cascades its sources away, and the SET NULL below is moot on a
    # row that is itself being deleted; dropping one *source* is the case this
    # exists for, and it leaves the organization with no active source rather
    # than with an id that resolves to nothing.
    op.create_foreign_key(
        "fk_organizations_active_data_source_id",
        "organizations",
        "data_sources",
        ["active_data_source_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_organizations_active_data_source_id", "organizations", type_="foreignkey"
    )
    op.drop_column("organizations", "active_data_source_id")
