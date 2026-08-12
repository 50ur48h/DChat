"""Platform database schema — the tables from architecture Part 10.1.

Revision 0001 creates these. Revision 0002 (WP1.2) adds row-level security and
locks ``audit_log`` down to inserts only.

``users`` is deliberately **not** tenant-scoped: one person can belong to several
organizations, and identity is global while membership is per-org. Everything
else here carries ``org_id``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from dataagent.db.base import ROLES, Base, CreatedAt, OrgId, UuidPk

# Imported for its side effect: the table has to be on Base.metadata or
# Alembic autogenerate would propose dropping it. Not tenant-scoped, so it
# lives outside this module and outside TENANT_TABLES (DECISIONS D-008).
from dataagent.db.security_events import SecurityEvent

__all__ = [
    "DATA_SOURCE_ENGINES",
    "DATA_SOURCE_STATUSES",
    "TENANT_TABLES",
    "AuditLog",
    "Base",
    "DataSource",
    "Invitation",
    "OrgMembership",
    "Organization",
    "SecurityEvent",
    "User",
]

ROLE_CHECK = "role IN ({})".format(", ".join(f"'{role}'" for role in ROLES))

#: The engines a connector exists for. Architecture Part 10.1 gives the column's
#: domain as ``pg|mssql|mysql``; MySQL is a V1.1 connector (Part 5.1), so it is
#: left out until it can be tested rather than accepted and then never reachable.
DATA_SOURCE_ENGINES: tuple[str, ...] = ("pg", "mssql")

#: ``registered`` is what WP3.1 can honestly claim: credentials are stored and
#: the address is recorded. WP3.2's connector moves a row to ``verified`` (it
#: connected *and* proved the credentials cannot write) or to ``error``.
DATA_SOURCE_STATUSES: tuple[str, ...] = ("registered", "verified", "error")


class Organization(Base):
    """A tenant. Its ``id`` is the ``org_id`` every other tenant row carries."""

    __tablename__ = "organizations"

    id: Mapped[UuidPk]
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    settings: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[CreatedAt]


class User(Base):
    """A person, identified by the IdP's subject claim.

    Not tenant-scoped, and holds no credentials: Entra External ID owns
    authentication (architecture Part 6.1). We store only what links a token to a
    membership.

    ``email`` is nullable, and that is deliberate (revision 0005, B-009): an
    access token carries an email claim only when the app registration asks for
    one, and a NOT NULL column forced provisioning to invent an address. The
    subject is the identity; the email is a convenience that may be missing.
    """

    __tablename__ = "users"

    id: Mapped[UuidPk]
    external_subject: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(String(320))
    name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[CreatedAt]


class OrgMembership(Base):
    """Which role a user holds in an organization.

    Roles live here, never in the IdP: they are org-scoped, invitation-driven and
    product-owned (architecture Part 6.1).
    """

    __tablename__ = "org_memberships"
    __table_args__ = (
        CheckConstraint(ROLE_CHECK, name="role_valid"),
        Index("ix_org_memberships_user_id", "user_id"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[CreatedAt]


class Invitation(Base):
    """A pending invitation to join an organization.

    Only the *hash* of the token is stored, so a database leak does not hand out
    working invitations.
    """

    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint(ROLE_CHECK, name="role_valid"),
        Index("ix_invitations_org_id_email", "org_id", "email"),
    )

    id: Mapped[UuidPk]
    org_id: Mapped[OrgId] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    accepted_at: Mapped[datetime | None]
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[CreatedAt]


class DataSource(Base):
    """A customer database this organization has registered (arch Part 5.1, 10.1).

    What is **not** here is the point of the table: no password, no DSN, no
    connection string. Credentials go to the ``SecretsProvider`` and this row
    keeps ``secret_ref``, a pointer that is useless without the store it names.

    ``settings`` holds the non-secret half of the connection — host, port,
    database, and the last four characters of the username so a screen can say
    *which* account is in use without reading the credential back.
    """

    __tablename__ = "data_sources"
    __table_args__ = (
        CheckConstraint(
            "engine IN ({})".format(", ".join(f"'{engine}'" for engine in DATA_SOURCE_ENGINES)),
            name="engine_valid",
        ),
        CheckConstraint(
            "status IN ({})".format(", ".join(f"'{status}'" for status in DATA_SOURCE_STATUSES)),
            name="status_valid",
        ),
        # Two sources with one name in an organization is a support call waiting
        # to happen. Scoped by org_id, so another tenant's names are unaffected —
        # and cannot be probed for, since the conflict can only be your own.
        Index("uq_data_sources_org_id_name", "org_id", "name", unique=True),
    )

    id: Mapped[UuidPk]
    org_id: Mapped[OrgId] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    engine: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Safe to show and safe to log: "host:port/database", never a credential.
    host_display: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'registered'")
    )
    settings: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    secret_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    #: Proven, never assumed (revision 0006). False is the safe default and the
    #: state every failed or incomplete verification leaves behind.
    readonly_verified: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    last_verified_at: Mapped[datetime | None]
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[CreatedAt]


class AuditLog(Base):
    """Append-only record of who did what (architecture Part 8.2).

    Revision 0002 revokes UPDATE and DELETE on this table from the application
    role, so "append-only" is a database grant rather than a convention. Never
    write result payloads, credentials or raw sensitive values here.
    """

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_org_id_ts", "org_id", text("ts DESC")),)

    # bigserial per architecture Part 10.1 — this table grows faster than any
    # other and must never be the reason an integer runs out.
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    org_id: Mapped[OrgId] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    object_type: Mapped[str | None] = mapped_column(String(100))
    object_id: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    sensitive: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    ts: Mapped[CreatedAt]


#: Every tenant-scoped table mapped to the column that identifies its tenant.
#: ``organizations`` is the exception worth spelling out: it *is* the tenant, so
#: its key is ``id``, not ``org_id`` — a policy written against ``org_id`` there
#: would silently fail to compile, or worse, be quietly skipped.
#:
#: WP1.2 builds the RLS policies from this map and WP1.3's proof suite asserts
#: every entry is covered, so a new tenant table cannot be added without
#: isolation and without a test that proves it.
TENANT_TABLES: dict[str, str] = {
    "organizations": "id",
    "org_memberships": "org_id",
    "invitations": "org_id",
    "audit_log": "org_id",
    "data_sources": "org_id",
}
