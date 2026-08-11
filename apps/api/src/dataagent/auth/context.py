"""Turning a principal into "who, in which organization, with what role".

The identity provider answers only *who*. Membership and role are ours, so this
is where a validated token becomes something the rest of the API can authorize
against (architecture Part 6.1-6.2).

Resolution reads the platform database as the **owner** role via a system
session, not through the tenant session. It has to: deciding which organization a
request belongs to is precisely the question the tenant session needs answered
before it can be opened. The read is narrow — one user row, one membership row —
and it never returns tenant data.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from dataagent.auth.principal import Principal
from dataagent.db.engine import system_session
from dataagent.db.models import OrgMembership, User


class AuthorizationError(Exception):
    """The caller is known but may not do this.

    ``reason`` is a short machine-readable code, recorded with the denial so a
    later question ("why was this refused?") has an answer that is not a guess.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Everything authorization needs, resolved once per request."""

    principal: Principal
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: str


async def resolve_user_id(principal: Principal) -> uuid.UUID | None:
    """The local user row for this token's subject, if we have ever seen them."""
    async with system_session() as session:
        result = await session.execute(
            select(User.id).where(User.external_subject == principal.subject)
        )
        return result.scalars().one_or_none()


async def resolve_context(principal: Principal, org_id: uuid.UUID) -> RequestContext:
    """Resolve a principal's membership of one organization.

    Raises ``AuthorizationError`` rather than returning ``None`` so that every
    refusal carries a reason worth recording. The two failures are deliberately
    different codes: an unknown subject means "no account here yet", while a
    known subject with no membership means "this account asked for a tenant it
    does not belong to" — which is the interesting one.
    """
    user_id = await resolve_user_id(principal)
    if user_id is None:
        raise AuthorizationError(
            "unknown_user", "No account exists for this identity in this deployment"
        )

    async with system_session() as session:
        result = await session.execute(
            select(OrgMembership.role).where(
                OrgMembership.user_id == user_id, OrgMembership.org_id == org_id
            )
        )
        role = result.scalars().one_or_none()

    if role is None:
        raise AuthorizationError(
            "not_a_member", "This account is not a member of the requested organization"
        )

    return RequestContext(principal=principal, user_id=user_id, org_id=org_id, role=role)
