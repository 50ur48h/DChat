"""org_recovery_grants — a way back when no Admin can sign in (B-017)

**The gap.** Roles live in `org_memberships` and change through an Admin-only
route. When the identity provider stopped recognising the account that created
the demo org, nobody who could sign in held Admin, and the Phase 3 gate was
unblocked by editing the database directly (`ops/scripts/set_role.sh`, written
that day). A tenant will hit this: people leave, accounts are deleted,
directories are migrated. The owner scheduled it here on 2026-08-12 — a product
gap, not a demo inconvenience.

**Why a grant an Admin arms in advance, and not a break-glass operator role.**
The plan weighed both and preferred this one because it adds *no new privilege*.
A platform-operator who can reassign any organization's Admin is a permanent
cross-tenant power that has to be defended forever, audited forever, and is worth
attacking precisely because it exists. This is the opposite shape: the
organization creates its own way back, holds it itself, and the platform gains no
authority it did not already have.

**Why not an ordinary invitation, which is nearly this.** Two reasons, both
fatal on their own. `accept_invitation` adds a membership only when there is not
one already — an existing Reader accepting an Admin invitation stays a Reader,
and the person who is locked out is very often already a member. And invitations
expire in seven days, which is useless for a credential whose whole purpose is to
be waiting years later.

**Nothing here weakens what a valid token is.** It is a bearer credential that
makes its holder an Admin of one organization, so it is hashed at rest like an
invitation, shown exactly once, revocable, listable so it cannot be forgotten,
and audited when armed, claimed and revoked.

**It expires, and that is a deliberate discomfort.** A credential that never
ages is worse hygiene than one that does, but an expiry that lapses silently
recreates B-017 exactly — the recovery path missing at the moment it is needed.
The compromise is a long default and a visible one: the members screen shows
every armed grant and when it runs out, so renewing is a thing somebody can see
they need to do rather than remember to.

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GRANTS = "org_recovery_grants"
POLICY = "org_recovery_grants_isolation"
APP_ROLE = "dataagent_app"


def upgrade() -> None:
    op.create_table(
        GRANTS,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        # SHA-256 of the token, never the token. A leaked backup hands out no
        # working grants, which is the reason invitations do the same.
        sa.Column("token_hash", sa.String(64), nullable=False),
        # What this one is for, in the Admin's own words — "kept in the ops
        # password manager". A list of identical rows is a list nobody audits.
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        # Single use. A recovery that left the token live afterwards would be a
        # permanent admin key sitting in somebody's notes.
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        # The record of who armed it survives their departure, unattributed
        # rather than erased — the rule the audit trail follows everywhere.
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["used_by"], ["users.id"], ondelete="SET NULL"),
        # The claim looks a grant up by hash alone, because the claimant is not
        # yet an Admin and may not be a member — exactly as an invitation is
        # redeemed. Unique so that lookup can never be ambiguous.
        sa.UniqueConstraint("token_hash", name="uq_org_recovery_grants_token_hash"),
    )
    op.create_index("ix_org_recovery_grants_org_id", GRANTS, ["org_id"])

    # SELECT, INSERT and UPDATE: a grant is armed, then marked used or revoked.
    # **No DELETE.** The record that a recovery happened is the point — an
    # organization whose Admin changed by this route must be able to show when,
    # by whom, and against which grant, and a row the application could remove
    # would make that a matter of trust rather than of record.
    op.execute(f"REVOKE ALL ON {GRANTS} FROM {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {GRANTS} TO {APP_ROLE}")
    op.execute(f"ALTER TABLE {GRANTS} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {GRANTS} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY {POLICY} ON {GRANTS}
        USING (org_id = current_setting('app.org_id')::uuid)
        WITH CHECK (org_id = current_setting('app.org_id')::uuid)
    """)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {GRANTS}")
    op.drop_index("ix_org_recovery_grants_org_id", table_name=GRANTS)
    op.drop_table(GRANTS)
