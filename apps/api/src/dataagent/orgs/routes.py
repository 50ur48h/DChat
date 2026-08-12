"""Org and membership routes (architecture Part 10.2)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, EmailStr, Field

from dataagent.auth.context import RequestContext
from dataagent.auth.guards import current_principal, require_admin, require_member
from dataagent.auth.principal import Principal
from dataagent.invitations.service import IssuedInvitation, create_invitation
from dataagent.orgs import service
from dataagent.orgs.service import ConflictError

router = APIRouter(prefix="/v1", tags=["organizations"])

ROLES = ("admin", "contributor", "reader")


class MembershipOut(BaseModel):
    org_id: uuid.UUID
    org_name: str
    role: str


class MeOut(BaseModel):
    subject: str
    user_id: uuid.UUID
    #: Null when the identity provider sent no email claim (B-009). The subject
    #: is the identity; this is a convenience that may legitimately be missing.
    email: str | None
    name: str | None
    memberships: list[MembershipOut]


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str | None
    name: str | None
    role: str


class CreateOrgIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ChangeRoleIn(BaseModel):
    role: str = Field(description="admin | contributor | reader")


class CreateInvitationIn(BaseModel):
    email: EmailStr
    role: str


class InvitationOut(BaseModel):
    invitation_id: uuid.UUID
    email: str
    role: str
    expires_at: str
    token: str = Field(
        description="Shown once and never stored. Only its hash is kept, so it cannot be re-read."
    )


def _validate_role(role: str) -> str:
    if role not in ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"role must be one of {', '.join(ROLES)}",
        )
    return role


def _conflict(error: ConflictError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))


@router.get("/me", response_model=MeOut, summary="The caller and their organizations")
async def me(principal: Annotated[Principal, Depends(current_principal)]) -> MeOut:
    """Works before the caller belongs to anything — that is how bootstrap starts."""
    user = await service.ensure_user(principal)
    memberships = await service.memberships_for(user.id)
    return MeOut(
        subject=principal.subject,
        user_id=user.id,
        email=user.email,
        name=user.name,
        memberships=[
            MembershipOut(org_id=m.org_id, org_name=m.org_name, role=m.role) for m in memberships
        ],
    )


@router.post(
    "/orgs",
    response_model=MembershipOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an organization; the creator becomes its Admin",
)
async def create_org(
    body: CreateOrgIn, principal: Annotated[Principal, Depends(current_principal)]
) -> MembershipOut:
    user = await service.ensure_user(principal)
    membership = await service.create_organization(user, body.name)
    return MembershipOut(
        org_id=membership.org_id, org_name=membership.org_name, role=membership.role
    )


@router.get("/orgs/{org_id}/members", response_model=list[MemberOut], summary="List members")
async def list_members(
    context: Annotated[RequestContext, Depends(require_member)],
) -> list[MemberOut]:
    members = await service.list_members(context.org_id)
    return [MemberOut(user_id=m.user_id, email=m.email, name=m.name, role=m.role) for m in members]


@router.patch(
    "/orgs/{org_id}/members/{user_id}", response_model=MemberOut, summary="Change a member's role"
)
async def change_role(
    body: ChangeRoleIn,
    context: Annotated[RequestContext, Depends(require_admin)],
    user_id: Annotated[uuid.UUID, Path()],
) -> MemberOut:
    try:
        member = await service.change_role(
            org_id=context.org_id,
            actor_user_id=context.user_id,
            target_user_id=user_id,
            role=_validate_role(body.role),
        )
    except ConflictError as error:
        raise _conflict(error) from error
    return MemberOut(user_id=member.user_id, email=member.email, name=member.name, role=member.role)


@router.delete(
    "/orgs/{org_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member",
)
async def remove_member(
    context: Annotated[RequestContext, Depends(require_admin)],
    user_id: Annotated[uuid.UUID, Path()],
) -> None:
    try:
        await service.remove_member(
            org_id=context.org_id, actor_user_id=context.user_id, target_user_id=user_id
        )
    except ConflictError as error:
        raise _conflict(error) from error


@router.post(
    "/orgs/{org_id}/invitations",
    response_model=InvitationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Invite someone to this organization",
)
async def invite(
    body: CreateInvitationIn, context: Annotated[RequestContext, Depends(require_admin)]
) -> InvitationOut:
    issued: IssuedInvitation = await create_invitation(
        org_id=context.org_id,
        actor_user_id=context.user_id,
        email=str(body.email),
        role=_validate_role(body.role),
    )
    return InvitationOut(
        invitation_id=issued.invitation_id,
        email=issued.email,
        role=issued.role,
        expires_at=issued.expires_at.isoformat(),
        token=issued.token,
    )
