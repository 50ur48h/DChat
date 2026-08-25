"""Org and membership routes (architecture Part 10.2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, EmailStr, Field

from dataagent.auth.context import RequestContext
from dataagent.auth.guards import current_principal, require_admin, require_member
from dataagent.auth.principal import Principal
from dataagent.invitations.service import IssuedInvitation, create_invitation
from dataagent.orgs import service
from dataagent.orgs.recovery import GrantView, arm_grant, list_grants, revoke_grant
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


class ActiveDataSourceOut(BaseModel):
    """Which database this organization asks questions of.

    Both fields null together means no Admin has chosen yet — not an error, and
    the state every organization was in before revision 0031.
    """

    data_source_id: uuid.UUID | None
    data_source_name: str | None


class SetActiveDataSourceIn(BaseModel):
    data_source_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "The data source to answer this organization's questions from, or "
            "null to clear the choice and return to resolving a single "
            "registered source and refusing when there is more than one."
        ),
    )


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


class ArmRecoveryIn(BaseModel):
    label: str = Field(
        default="Recovery grant",
        max_length=200,
        description=(
            "What this grant is for, in your own words — where the token will be "
            "kept, or who holds it. A list of identical rows is a list nobody audits."
        ),
    )
    days: int | None = Field(
        default=None,
        ge=1,
        le=730,
        description=(
            "How long it stays valid. Defaults to 365 days. Long on purpose: it "
            "has to still be there when something has gone wrong, and the "
            "members screen shows the date so renewing is visible."
        ),
    )


class RecoveryGrantOut(BaseModel):
    id: uuid.UUID
    label: str
    created_at: datetime
    expires_at: datetime
    state: str = Field(description="armed | used | revoked | expired")
    used_at: datetime | None = None
    revoked_at: datetime | None = None


class ArmedRecoveryOut(RecoveryGrantOut):
    token: str = Field(
        description=(
            "The recovery token, shown exactly once and never recoverable "
            "afterwards — only its hash is stored. Keep it somewhere outside "
            "this product: it makes whoever holds it an Admin of this "
            "organization."
        )
    )


@router.get(
    "/orgs/{org_id}/active-data-source",
    response_model=ActiveDataSourceOut,
    summary="The database this organization asks questions of",
)
async def get_active_data_source(
    context: Annotated[RequestContext, Depends(require_member)],
) -> ActiveDataSourceOut:
    """Readable by any member, and that is deliberate (**D-045**).

    The chat screen has to know whether asking is possible before it offers a
    composer, and a Reader who cannot read this gets a control that fails for a
    reason the screen is unable to explain. What it discloses is the *name* of a
    database every member already queries — no host, no account, no credential —
    so it tells a member nothing their own answers would not.
    """
    active = await service.active_data_source(context.org_id)
    return ActiveDataSourceOut(
        data_source_id=active.data_source_id, data_source_name=active.data_source_name
    )


@router.put(
    "/orgs/{org_id}/active-data-source",
    response_model=ActiveDataSourceOut,
    summary="Choose the database this organization asks questions of",
)
async def set_active_data_source(
    body: SetActiveDataSourceIn,
    context: Annotated[RequestContext, Depends(require_admin)],
) -> ActiveDataSourceOut:
    """Admin only, and audited like every other org-shaping change.

    A `PUT` because it sets one value to one state and setting it twice is
    setting it once — and because null is a real value here, meaning *no choice*,
    which a `PATCH` of a partial body could not distinguish from *field omitted*.
    """
    try:
        active = await service.set_active_data_source(
            org_id=context.org_id,
            actor_user_id=context.user_id,
            data_source_id=body.data_source_id,
        )
    except service.NotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such data source"
        ) from error
    return ActiveDataSourceOut(
        data_source_id=active.data_source_id, data_source_name=active.data_source_name
    )


def _grant_out(view: GrantView) -> RecoveryGrantOut:
    return RecoveryGrantOut(
        id=view.id,
        label=view.label,
        created_at=view.created_at,
        expires_at=view.expires_at,
        state=view.state,
        used_at=view.used_at,
        revoked_at=view.revoked_at,
    )


@router.post(
    "/orgs/{org_id}/recovery-grants",
    response_model=ArmedRecoveryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Arm a way back in, in case no Admin can sign in later",
)
async def arm_recovery(
    body: ArmRecoveryIn, context: Annotated[RequestContext, Depends(require_admin)]
) -> ArmedRecoveryOut:
    """**B-017.** An organization whose only Admin loses their identity has no
    way back: roles change through an Admin-only route, so nobody who can sign in
    can repair it. This mints a token an Admin keeps outside the product.

    Admin-only, and it grants the platform nothing — the alternative weighed was
    a break-glass operator role that could reassign any organization's Admin,
    which is a permanent cross-tenant privilege worth attacking precisely
    because it exists.
    """
    try:
        issued = await arm_grant(
            org_id=context.org_id,
            actor_user_id=context.user_id,
            label=body.label,
            validity=timedelta(days=body.days) if body.days else None,
        )
    except ConflictError as error:
        raise _conflict(error) from error
    return ArmedRecoveryOut(
        id=issued.grant_id,
        label=issued.label,
        created_at=datetime.now(UTC),
        expires_at=issued.expires_at,
        state="armed",
        token=issued.token,
    )


@router.get(
    "/orgs/{org_id}/recovery-grants",
    response_model=list[RecoveryGrantOut],
    summary="Every recovery grant this organization has armed",
)
async def recovery_grants(
    context: Annotated[RequestContext, Depends(require_admin)],
) -> list[RecoveryGrantOut]:
    """Used and revoked ones stay in the list. "Has anybody ever recovered this
    organization, and when" is a question an Admin should be able to answer
    without a database, and a credential nobody can see is one nobody renews."""
    return [_grant_out(view) for view in await list_grants(org_id=context.org_id)]


@router.post(
    "/orgs/{org_id}/recovery-grants/{grant_id}/revoke",
    response_model=RecoveryGrantOut,
    summary="Revoke a recovery grant",
)
async def revoke_recovery(
    grant_id: uuid.UUID, context: Annotated[RequestContext, Depends(require_admin)]
) -> RecoveryGrantOut:
    try:
        return _grant_out(
            await revoke_grant(
                org_id=context.org_id, actor_user_id=context.user_id, grant_id=grant_id
            )
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
