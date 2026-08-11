"""Redeeming an invitation (architecture Part 10.2).

Not under ``/v1/orgs/{org_id}`` on purpose: the caller is not yet a member of
that organization, so no org-scoped guard could admit them. The token is the
authorization.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from dataagent.auth.guards import current_principal
from dataagent.auth.principal import Principal
from dataagent.invitations.service import accept_invitation
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
