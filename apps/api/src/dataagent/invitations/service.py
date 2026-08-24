"""Invitations: the only way to join an existing organization.

The raw token is returned to the caller exactly once and never stored. Only its
SHA-256 hash goes in the database, so a leaked backup hands out no working
invitations — the same reason nobody stores passwords.

Acceptance is deliberately **not** org-scoped at lookup time: the accepting user
is by definition not yet a member, so no session could be opened for the
organization they are trying to join. The token is the authorization, and it is
looked up by hash through the system session.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from dataagent.db.models import Invitation, OrgMembership, User
from dataagent.orgs.service import ConflictError, audit
from dataagent.tenancy.session import app_session, org_session

TOKEN_BYTES = 32
EXPIRY = timedelta(days=7)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class IssuedInvitation:
    invitation_id: uuid.UUID
    email: str
    role: str
    expires_at: datetime
    #: Shown once. Not recoverable afterwards, by design.
    token: str


async def create_invitation(
    *, org_id: uuid.UUID, actor_user_id: uuid.UUID, email: str, role: str
) -> IssuedInvitation:
    token = secrets.token_urlsafe(TOKEN_BYTES)
    expires_at = datetime.now(UTC) + EXPIRY
    invitation_id = uuid.uuid4()

    async with org_session(org_id) as session:
        session.add(
            Invitation(
                id=invitation_id,
                org_id=org_id,
                email=email,
                role=role,
                token_hash=hash_token(token),
                expires_at=expires_at,
                invited_by=actor_user_id,
            )
        )
        audit(
            session,
            org_id=org_id,
            actor_user_id=actor_user_id,
            action="invitation.created",
            object_type="invitation",
            object_id=str(invitation_id),
            # The email is the point of the record; the token never appears.
            details={"email": email, "role": role},
        )

    return IssuedInvitation(
        invitation_id=invitation_id,
        email=email,
        role=role,
        expires_at=expires_at,
        token=token,
    )


@dataclass(frozen=True, slots=True)
class AcceptedInvitation:
    org_id: uuid.UUID
    org_name: str
    role: str


async def accept_invitation(*, user: User, token: str) -> AcceptedInvitation:
    """Redeem a token and join its organization.

    Every failure returns the same message. Distinguishing "no such invitation"
    from "expired" from "already used" would turn this into an oracle for
    guessing tokens, and the caller can do nothing differently with the detail.
    """
    invalid = ConflictError("That invitation is not valid. Ask an admin for a new one.")

    # **The token is the only thing known here.** Which organization it belongs
    # to is the *answer*, so there is no `app.org_id` to scope by and RLS would
    # return nothing. One audited lookup rather than an owner connection
    # (B-123); every write below is already org-scoped, which is why only the
    # read needed anything.
    async with app_session() as session:
        found = (
            await session.execute(
                text(
                    "SELECT id, org_id, org_name, member_role, expires_at, accepted_at "
                    "FROM auth_invitation_by_token(CAST(:token_hash AS varchar))"
                ),
                {"token_hash": hash_token(token)},
            )
        ).one_or_none()

    if found is None or found.accepted_at is not None:
        raise invalid
    if found.expires_at <= datetime.now(UTC):
        raise invalid

    invitation_id = found.id
    org_name = found.org_name
    role = found.member_role

    org_id = found.org_id

    async with org_session(org_id) as session:
        already = (
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

        if already is None:
            session.add(OrgMembership(org_id=org_id, user_id=user.id, role=role))

        # Marked accepted through the org-scoped session, so a token cannot be
        # redeemed twice even if two requests race: the second finds it taken.
        pending = (
            (
                await session.execute(
                    select(Invitation).where(
                        Invitation.id == invitation_id, Invitation.accepted_at.is_(None)
                    )
                )
            )
            .scalars()
            .one_or_none()
        )

        if pending is None:
            raise invalid

        pending.accepted_at = datetime.now(UTC)
        audit(
            session,
            org_id=org_id,
            actor_user_id=user.id,
            action="invitation.accepted",
            object_type="invitation",
            object_id=str(invitation_id),
            details={"role": role},
        )

    return AcceptedInvitation(org_id=org_id, org_name=org_name, role=role)
