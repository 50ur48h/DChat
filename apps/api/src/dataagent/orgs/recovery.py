"""A way back into an organization whose Admins can no longer sign in (**B-017**).

**The gap this closes.** Roles live in `org_memberships` and change through an
Admin-only route, so the moment nobody who can authenticate holds Admin, the
organization is bricked: it cannot invite anyone, cannot change a role, cannot
register a data source. It happened here — the identity provider stopped
recognising the account that created the demo org, and the Phase 3 gate was
unblocked by editing the database (`ops/scripts/set_role.sh`). A tenant will hit
it too: people leave, accounts are deleted, directories are migrated.

**An organization arms its own way back; the platform gains no new power.** The
alternative weighed in the plan was a break-glass platform-operator role that
could reassign any organization's Admin — a permanent cross-tenant privilege
that must be defended forever and is worth attacking precisely because it
exists. This is the opposite: an Admin mints a grant, keeps it outside the
product, and nothing here can do anything an Admin could not already do.

**A grant is a bearer credential and is handled like one.** The raw token is
returned exactly once and never stored; only its SHA-256 hash is kept, so a
leaked backup hands out no working grants. It is single-use, revocable, and
listed on the members screen — a credential nobody can see is one nobody renews
or retires.

**Claiming is deliberately not org-scoped at lookup.** The claimant may not be a
member yet, and if they are, they are not an Admin — so no org-scoped session
could be opened on their behalf. The token *is* the authorization, looked up by
hash through the system session, exactly as `accept_invitation` does. Everything
that follows the lookup happens in an org-scoped session, where RLS applies.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from dataagent.db.engine import system_session
from dataagent.db.models import Organization, OrgMembership, OrgRecoveryGrant, User
from dataagent.orgs.service import ConflictError, audit
from dataagent.tenancy.session import org_session

__all__ = [
    "ClaimedRecovery",
    "GrantView",
    "IssuedGrant",
    "arm_grant",
    "claim_recovery",
    "list_grants",
    "revoke_grant",
]

TOKEN_BYTES = 32
ROLE_ADMIN = "admin"

#: A year. Long because the whole purpose is to still be there when something
#: has gone wrong years from now, and bounded anyway because a credential that
#: never ages is worse hygiene than one that does. The members screen shows the
#: date, so renewing is visible rather than remembered.
DEFAULT_VALIDITY = timedelta(days=365)

#: Beyond which "arm in advance" stops meaning anything a person can reason
#: about. Two years is already a long time to hold an admin key in a drawer.
MAX_VALIDITY = timedelta(days=730)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class IssuedGrant:
    grant_id: uuid.UUID
    label: str
    expires_at: datetime
    #: Shown once. Not recoverable afterwards, by design.
    token: str


@dataclass(frozen=True, slots=True)
class GrantView:
    """One armed grant, without anything that could be redeemed."""

    id: uuid.UUID
    label: str
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    revoked_at: datetime | None

    @property
    def state(self) -> str:
        """What an Admin needs to know at a glance.

        Order matters: a grant that was used *and* has since expired is
        interesting because it was used. Expiry is the least interesting reason
        a grant is no longer live, so it is checked last.
        """
        if self.used_at is not None:
            return "used"
        if self.revoked_at is not None:
            return "revoked"
        if self.expires_at <= datetime.now(UTC):
            return "expired"
        return "armed"


@dataclass(frozen=True, slots=True)
class ClaimedRecovery:
    org_id: uuid.UUID
    org_name: str
    #: Whether this created a membership or raised an existing one. Both are
    #: legitimate, and they are different stories in an audit log.
    was_member: bool


async def arm_grant(
    *, org_id: uuid.UUID, actor_user_id: uuid.UUID, label: str, validity: timedelta | None = None
) -> IssuedGrant:
    """Mint a recovery token for this organization, shown once.

    Admin-only, enforced at the route by the same dependency every other
    membership change uses. Deliberately *not* restricted to one grant per
    organization: an Admin handing over to a successor should be able to arm a
    new one before retiring the old, and forcing revoke-then-arm would leave a
    window with no way back at all — which is the defect this closes.
    """
    window = validity or DEFAULT_VALIDITY
    if window > MAX_VALIDITY:
        raise ConflictError(f"A recovery grant can be valid for at most {MAX_VALIDITY.days} days.")
    if window <= timedelta(0):
        raise ConflictError("A recovery grant has to be valid for at least a day.")

    token = secrets.token_urlsafe(TOKEN_BYTES)
    grant_id = uuid.uuid4()
    expires_at = datetime.now(UTC) + window
    cleaned = label.strip() or "Recovery grant"

    async with org_session(org_id) as session:
        session.add(
            OrgRecoveryGrant(
                id=grant_id,
                org_id=org_id,
                token_hash=hash_token(token),
                label=cleaned,
                created_by=actor_user_id,
                expires_at=expires_at,
            )
        )
        # The label and the expiry, never the token or its hash. An audit row is
        # read by more people than the table it describes.
        audit(
            session,
            org_id=org_id,
            actor_user_id=actor_user_id,
            action="org.recovery_armed",
            object_type="recovery_grant",
            object_id=str(grant_id),
            details={"label": cleaned, "expires_at": expires_at.isoformat()},
        )

    return IssuedGrant(grant_id=grant_id, label=cleaned, expires_at=expires_at, token=token)


async def list_grants(*, org_id: uuid.UUID) -> list[GrantView]:
    """Every grant this organization has ever armed, newest first.

    Used and revoked ones stay in the list rather than being filtered out: "has
    anybody ever recovered this organization, and when" is exactly the question
    an Admin should be able to answer without a database.
    """
    async with org_session(org_id) as session:
        rows = (
            (
                await session.execute(
                    select(OrgRecoveryGrant).order_by(OrgRecoveryGrant.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [
            GrantView(
                id=row.id,
                label=row.label,
                created_at=row.created_at,
                expires_at=row.expires_at,
                used_at=row.used_at,
                revoked_at=row.revoked_at,
            )
            for row in rows
        ]


async def revoke_grant(
    *, org_id: uuid.UUID, actor_user_id: uuid.UUID, grant_id: uuid.UUID
) -> GrantView:
    """Kill a grant whose token may have got out.

    Revoking an already-used one is allowed and does nothing but say so: the
    token is spent either way, and refusing would make an Admin who is not sure
    which state it is in unable to act on a suspicion.
    """
    async with org_session(org_id) as session:
        row = (
            (await session.execute(select(OrgRecoveryGrant).where(OrgRecoveryGrant.id == grant_id)))
            .scalars()
            .one_or_none()
        )
        if row is None:
            raise ConflictError("No such recovery grant.")
        if row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
        audit(
            session,
            org_id=org_id,
            actor_user_id=actor_user_id,
            action="org.recovery_revoked",
            object_type="recovery_grant",
            object_id=str(grant_id),
            details={"label": row.label},
        )
        return GrantView(
            id=row.id,
            label=row.label,
            created_at=row.created_at,
            expires_at=row.expires_at,
            used_at=row.used_at,
            revoked_at=row.revoked_at,
        )


async def claim_recovery(*, user: User, token: str) -> ClaimedRecovery:
    """Redeem a grant and become an Admin of its organization.

    **Every failure returns the same sentence.** Telling "no such grant" apart
    from "expired" from "already used" would make this an oracle for guessing
    tokens, and there is nothing the caller could do differently with the
    detail — the same reasoning `accept_invitation` records.

    **It raises an existing membership**, which is the difference from an
    invitation and the reason this is not one. The person locked out of an
    organization is usually already in it as a Reader or a Contributor;
    `accept_invitation` adds a membership only when there is not one already, so
    redeeming an Admin invitation would leave them exactly as stuck as before.
    """
    invalid = ConflictError("That recovery grant is not valid.")

    async with system_session() as session:
        grant = (
            (
                await session.execute(
                    select(OrgRecoveryGrant).where(OrgRecoveryGrant.token_hash == hash_token(token))
                )
            )
            .scalars()
            .one_or_none()
        )
        if grant is None or grant.used_at is not None or grant.revoked_at is not None:
            raise invalid
        if grant.expires_at <= datetime.now(UTC):
            raise invalid
        org_name = (
            (
                await session.execute(
                    select(Organization.name).where(Organization.id == grant.org_id)
                )
            )
            .scalars()
            .one()
        )

    org_id = grant.org_id

    async with org_session(org_id) as session:
        # Re-read inside the org session, and mark it used *there*. Two requests
        # racing the same token both pass the check above; only one can take the
        # row here, and the second finds it spent — the property
        # `accept_invitation` relies on for the same reason.
        held = (
            (
                await session.execute(
                    select(OrgRecoveryGrant).where(
                        OrgRecoveryGrant.id == grant.id, OrgRecoveryGrant.used_at.is_(None)
                    )
                )
            )
            .scalars()
            .one_or_none()
        )
        if held is None:
            raise invalid
        held.used_at = datetime.now(UTC)
        held.used_by = user.id

        membership = (
            (
                await session.execute(
                    select(OrgMembership).where(
                        OrgMembership.org_id == org_id, OrgMembership.user_id == user.id
                    )
                )
            )
            .scalars()
            .one_or_none()
        )
        was_member = membership is not None
        previous = membership.role if membership is not None else None
        if membership is None:
            session.add(OrgMembership(org_id=org_id, user_id=user.id, role=ROLE_ADMIN))
        else:
            membership.role = ROLE_ADMIN

        audit(
            session,
            org_id=org_id,
            actor_user_id=user.id,
            action="org.recovery_claimed",
            object_type="recovery_grant",
            object_id=str(grant.id),
            details={"label": held.label, "was_member": was_member, "previous_role": previous},
        )

    return ClaimedRecovery(org_id=org_id, org_name=org_name, was_member=was_member)
