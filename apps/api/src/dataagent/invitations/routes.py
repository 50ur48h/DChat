"""Redeeming an invitation, and claiming a recovery grant (architecture Part 10.2).

Neither is under ``/v1/orgs/{org_id}``, and for the same reason: the caller is
not yet a member of that organization — or, for a recovery, is a member without
the role the route would demand. No org-scoped guard could admit them. The token
is the authorization.

They are together in this module because they are the same shape and the same
risks: a hashed bearer token, one indistinguishable failure message so neither
becomes a guessing oracle, and a redemption that must survive two requests
racing it. What differs is what redemption *does*, and **B-017** turns on
exactly that difference — an invitation adds a membership only where there is
none, while a recovery raises the one that is already there.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from dataagent.auth.guards import current_principal
from dataagent.auth.principal import Principal
from dataagent.invitations.service import accept_invitation
from dataagent.orgs.recovery import claim_recovery
from dataagent.orgs.service import ConflictError, ensure_user

router = APIRouter(prefix="/v1", tags=["invitations"])


class AcceptIn(BaseModel):
    token: str = Field(min_length=1)


class AcceptOut(BaseModel):
    org_id: uuid.UUID
    org_name: str
    role: str


@router.post("/invitations/accept", response_model=AcceptOut, summary="Redeem an invitation")
async def accept(
    body: AcceptIn, principal: Annotated[Principal, Depends(current_principal)]
) -> AcceptOut:
    user = await ensure_user(principal)
    try:
        accepted = await accept_invitation(user=user, token=body.token)
    except ConflictError as error:
        # One message for every failure — unknown, expired and already-used are
        # deliberately indistinguishable, or this becomes a token-guessing oracle.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return AcceptOut(org_id=accepted.org_id, org_name=accepted.org_name, role=accepted.role)


class ClaimRecoveryIn(BaseModel):
    token: str = Field(min_length=1)


class ClaimRecoveryOut(BaseModel):
    org_id: uuid.UUID
    org_name: str
    role: str
    was_member: bool = Field(
        description=(
            "True when you were already in this organization and have been "
            "raised to Admin; false when the claim also added you. Both are "
            "normal — the locked-out person is usually already a member."
        )
    )


@router.post(
    "/recovery-grants/claim",
    response_model=ClaimRecoveryOut,
    summary="Claim a recovery grant and become an Admin of its organization",
)
async def claim(
    body: ClaimRecoveryIn, principal: Annotated[Principal, Depends(current_principal)]
) -> ClaimRecoveryOut:
    """**B-017.** The way back into an organization whose Admins can no longer
    sign in, armed in advance by an Admin who still could.

    Authenticated, because the whole point is to make *this* caller an Admin and
    there has to be somebody to make one. Not org-scoped, because a claimant who
    is already a member is a Reader or a Contributor and would fail the guard
    that matters.
    """
    user = await ensure_user(principal)
    try:
        claimed = await claim_recovery(user=user, token=body.token)
    except ConflictError as error:
        # One message for every failure — unknown, expired, revoked and spent
        # are deliberately indistinguishable, exactly as for an invitation.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return ClaimRecoveryOut(
        org_id=claimed.org_id,
        org_name=claimed.org_name,
        role="admin",
        was_member=claimed.was_member,
    )
