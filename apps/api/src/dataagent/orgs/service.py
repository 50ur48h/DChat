"""Organizations, membership and the audit trail they leave.

Every write here happens inside an org-scoped session, so row-level security is
in force even while the organization is being created. That has one consequence
worth stating up front, discovered while proving RLS in WP1.3:

**Bootstrap must choose the organization's id before inserting it.** The policy
on ``organizations`` is ``id = current_setting('app.org_id')``, so a row can only
be written by a session already scoped to that id. Letting the database generate
the id would mean writing a row the session is not allowed to write.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from dataagent.auth.principal import Principal
from dataagent.db.models import AuditLog, DataSource, Organization, OrgMembership, User
from dataagent.tenancy.session import app_session, org_session

ROLE_ADMIN = "admin"


class ConflictError(Exception):
    """A request that is well-formed but cannot be applied to the current state."""


class NotFoundError(Exception):
    """Named a row this organization cannot see — which reads the same as absent.

    Its own class rather than an import from ``datasources`` or ``runs``, which
    each define one for the same reason: the tenant boundary is what makes "not
    found" the right answer, and a module that owns a boundary owns the word for
    crossing it.
    """


@dataclass(frozen=True, slots=True)
class Membership:
    org_id: uuid.UUID
    org_name: str
    role: str


def audit(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    action: str,
    object_type: str | None = None,
    object_id: str | None = None,
    details: dict[str, object] | None = None,
) -> None:
    """Stage an audit row on an already org-scoped session.

    Takes the session rather than opening its own, so the audit row commits in
    the same transaction as the change it describes: an action that happened
    without a record, or a record of something that did not happen, are both
    worse than either alone.
    """
    session.add(
        AuditLog(
            org_id=org_id,
            actor_user_id=actor_user_id,
            action=action,
            object_type=object_type,
            object_id=object_id,
            details=details or {},
            sensitive=False,
        )
    )


async def ensure_user(principal: Principal) -> User:
    """Find or create the local user row for a validated principal.

    Just-in-time provisioning: the identity provider has already vouched for this
    subject, and refusing to record them until an invitation arrives would make
    ``GET /v1/me`` fail for a perfectly legitimate first-time caller. ``users`` is
    not tenant-scoped, so this needs the system session.

    Only claims that actually arrived are stored (B-009). An access token carries
    ``email`` only when the app registration asks for it, and inventing an address
    for the ones that do not would write a plausible lie into a column that later
    features will trust. A claim that shows up later is recorded then — which is
    what happens when an administrator finally adds the optional claim.
    """
    # `users` is not tenant-scoped and has no RLS policy; the application role
    # holds SELECT/INSERT/UPDATE on it from revision 0002 (B-123).
    async with app_session() as session:
        existing = (
            (await session.execute(select(User).where(User.external_subject == principal.subject)))
            .scalars()
            .one_or_none()
        )

        if existing is not None:
            # Filling a blank, never overwriting: the token is authoritative about
            # who this is, not about what we have already been told they are called.
            filled = False
            if existing.email is None and principal.email:
                existing.email, filled = principal.email, True
            if existing.name is None and principal.name:
                existing.name, filled = principal.name, True
            if filled:
                await session.commit()
                await session.refresh(existing)
            return existing

        user = User(
            external_subject=principal.subject,
            email=principal.email,
            name=principal.name,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def memberships_for(user_id: uuid.UUID) -> list[Membership]:
    """Every organization this user belongs to.

    Reads through the system session because the answer spans organizations —
    there is no single ``app.org_id`` that could scope it. It returns only rows
    joined to this user's own id, so it cannot become a way to enumerate tenants.
    """
    # **Spans every organization by definition** — "which tenants is this person
    # in" is the one question no single `app.org_id` can answer, so it cannot be
    # an org-scoped query. One audited function instead of an owner connection
    # (B-123).
    async with app_session() as session:
        rows = await session.execute(
            text(
                "SELECT org_id, org_name, member_role "
                "FROM auth_memberships_for_user(CAST(:user_id AS uuid))"
            ),
            {"user_id": user_id},
        )
        return [Membership(org_id=o, org_name=n, role=r) for o, n, r in rows.all()]


async def create_organization(user: User, name: str) -> Membership:
    """Create an organization; the creator becomes its Admin."""
    org_id = uuid.uuid4()  # chosen here, not by the database — see the module docstring

    async with org_session(org_id) as session:
        session.add(Organization(id=org_id, name=name))
        await session.flush()
        session.add(OrgMembership(org_id=org_id, user_id=user.id, role=ROLE_ADMIN))
        audit(
            session,
            org_id=org_id,
            actor_user_id=user.id,
            action="org.created",
            object_type="organization",
            object_id=str(org_id),
            details={"name": name},
        )

    return Membership(org_id=org_id, org_name=name, role=ROLE_ADMIN)


@dataclass(frozen=True, slots=True)
class Member:
    user_id: uuid.UUID
    #: None when the identity provider never sent an email claim (B-009).
    email: str | None
    name: str | None
    role: str


async def list_members(org_id: uuid.UUID) -> list[Member]:
    async with org_session(org_id) as session:
        rows = await session.execute(
            select(OrgMembership.user_id, User.email, User.name, OrgMembership.role)
            .join(User, User.id == OrgMembership.user_id)
            .where(OrgMembership.org_id == org_id)
            .order_by(User.email)
        )
        return [Member(user_id=u, email=e, name=n, role=r) for u, e, n, r in rows.all()]


async def _count_admins(session: AsyncSession, org_id: uuid.UUID) -> int:
    rows = await session.execute(
        select(OrgMembership.user_id).where(
            OrgMembership.org_id == org_id, OrgMembership.role == ROLE_ADMIN
        )
    )
    return len(rows.scalars().all())


async def change_role(
    *, org_id: uuid.UUID, actor_user_id: uuid.UUID, target_user_id: uuid.UUID, role: str
) -> Member:
    """Change a member's role, refusing to remove the last Admin.

    An organization with no Admin cannot invite anyone, cannot change a role and
    cannot register a data source — it is bricked, and only an operator with
    database access could repair it.
    """
    async with org_session(org_id) as session:
        membership = (
            (
                await session.execute(
                    select(OrgMembership).where(
                        OrgMembership.org_id == org_id, OrgMembership.user_id == target_user_id
                    )
                )
            )
            .scalars()
            .one_or_none()
        )

        if membership is None:
            raise ConflictError("That user is not a member of this organization")

        demoting_an_admin = membership.role == ROLE_ADMIN and role != ROLE_ADMIN
        if demoting_an_admin and await _count_admins(session, org_id) <= 1:
            raise ConflictError(
                "This is the only Admin. Promote someone else before changing this role."
            )

        previous, membership.role = membership.role, role
        audit(
            session,
            org_id=org_id,
            actor_user_id=actor_user_id,
            action="member.role_changed",
            object_type="user",
            object_id=str(target_user_id),
            details={"from": previous, "to": role},
        )

        user = (
            (await session.execute(select(User).where(User.id == target_user_id))).scalars().one()
        )
        return Member(user_id=user.id, email=user.email, name=user.name, role=role)


async def remove_member(
    *, org_id: uuid.UUID, actor_user_id: uuid.UUID, target_user_id: uuid.UUID
) -> None:
    async with org_session(org_id) as session:
        membership = (
            (
                await session.execute(
                    select(OrgMembership).where(
                        OrgMembership.org_id == org_id, OrgMembership.user_id == target_user_id
                    )
                )
            )
            .scalars()
            .one_or_none()
        )

        if membership is None:
            raise ConflictError("That user is not a member of this organization")

        if membership.role == ROLE_ADMIN and await _count_admins(session, org_id) <= 1:
            raise ConflictError("This is the only Admin and cannot be removed.")

        await session.delete(membership)
        audit(
            session,
            org_id=org_id,
            actor_user_id=actor_user_id,
            action="member.removed",
            object_type="user",
            object_id=str(target_user_id),
        )


@dataclass(frozen=True, slots=True)
class ActiveDataSource:
    """Which database this organization asks questions of, and its name.

    The name travels with the id because every screen that shows this shows it to
    a person: *"Questions are answered from Pizza demo"* is readable and
    ``a3f1…`` is not. Both are null together when no Admin has chosen yet.
    """

    data_source_id: uuid.UUID | None
    data_source_name: str | None


async def _active_data_source(session: AsyncSession, org: Organization) -> ActiveDataSource:
    """Read the pointer and resolve its name on an already org-scoped session."""
    if org.active_data_source_id is None:
        return ActiveDataSource(data_source_id=None, data_source_name=None)
    name = (
        await session.execute(
            select(DataSource.name).where(DataSource.id == org.active_data_source_id)
        )
    ).scalar_one_or_none()
    # A source deleted between the two statements would land here. The FK's
    # ON DELETE SET NULL means it cannot persist, so report what a reader can act
    # on — nothing is chosen — rather than an id with no name behind it.
    if name is None:
        return ActiveDataSource(data_source_id=None, data_source_name=None)
    return ActiveDataSource(data_source_id=org.active_data_source_id, data_source_name=name)


async def active_data_source(org_id: uuid.UUID) -> ActiveDataSource:
    """The organization's chosen database, readable by **any member**.

    Not Admin-only, deliberately: the chat screen has to know whether asking is
    possible at all, and a Reader who cannot see the answer to that gets a
    composer that fails for reasons the screen cannot explain. This exposes the
    name of a database the whole organization already queries — no host, no
    account, no credential — so it tells a member nothing their own answers would
    not.
    """
    async with org_session(org_id) as session:
        org = (
            (await session.execute(select(Organization).where(Organization.id == org_id)))
            .scalars()
            .one()
        )
        return await _active_data_source(session, org)


async def set_active_data_source(
    *, org_id: uuid.UUID, actor_user_id: uuid.UUID, data_source_id: uuid.UUID | None
) -> ActiveDataSource:
    """Choose the database this organization asks questions of. Admin only.

    **The foreign key is not the tenant check.** A constraint check does not
    consult row-level security, so another organization's source id would satisfy
    the database perfectly well and quietly point this organization at somebody
    else's data — the worst output this product can produce, and the only one
    with nothing about it that looks wrong. The lookup below runs inside the org
    session, so a source this organization does not own is simply not there, and
    the caller is told "no such data source" rather than anything about what
    exists elsewhere.

    ``None`` clears the choice, which is a legitimate thing for an Admin to want
    and returns the organization to resolving and refusing exactly as it did
    before revision 0031.
    """
    async with org_session(org_id) as session:
        org = (
            (await session.execute(select(Organization).where(Organization.id == org_id)))
            .scalars()
            .one()
        )

        if data_source_id is not None:
            owned = (
                (await session.execute(select(DataSource).where(DataSource.id == data_source_id)))
                .scalars()
                .one_or_none()
            )
            if owned is None:
                raise NotFoundError("No such data source")

        previous = org.active_data_source_id
        org.active_data_source_id = data_source_id
        audit(
            session,
            org_id=org_id,
            actor_user_id=actor_user_id,
            action="org.active_data_source_changed",
            object_type="data_source",
            object_id=str(data_source_id) if data_source_id is not None else None,
            details={
                "from": str(previous) if previous is not None else None,
                "to": str(data_source_id) if data_source_id is not None else None,
            },
        )
        await session.flush()
        return await _active_data_source(session, org)
