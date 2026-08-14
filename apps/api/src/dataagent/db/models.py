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
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    Computed,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from dataagent.db.base import ROLES, Base, CreatedAt, OrgId, UuidPk

# Imported for its side effect: the table has to be on Base.metadata or
# Alembic autogenerate would propose dropping it. Not tenant-scoped, so it
# lives outside this module and outside TENANT_TABLES (DECISIONS D-008).
from dataagent.db.security_events import SecurityEvent

__all__ = [
    "CARD_TEXT_CONFIGURATION",
    "COLUMN_POLICIES",
    "DATA_SOURCE_ENGINES",
    "DATA_SOURCE_STATUSES",
    "EXECUTION_STATUSES",
    "LLM_ROLES",
    "LLM_TIERS",
    "PROFILE_STATUSES",
    "RELATIONSHIP_KINDS",
    "SEMANTIC_ROLES",
    "SENSITIVITY_LEVELS",
    "SNAPSHOT_STATUSES",
    "TABLE_KINDS",
    "TENANT_TABLES",
    "USAGE_STATUSES",
    "AuditLog",
    "Base",
    "CatalogColumn",
    "CatalogRelationship",
    "CatalogSnapshot",
    "CatalogTable",
    "ColumnPolicy",
    "DataSource",
    "Invitation",
    "OrgMembership",
    "Organization",
    "QueryExecution",
    "ResultArtifact",
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

#: A snapshot is built, then becomes the one active catalog for its data source,
#: or fails and stays visible as a failure. The previous active one is
#: ``superseded`` — kept, not deleted, because a run that is still going may be
#: reasoning about it (architecture Part 5.2).
SNAPSHOT_STATUSES: tuple[str, ...] = ("building", "active", "failed", "superseded")

TABLE_KINDS: tuple[str, ...] = ("table", "view")

#: ``declared`` is a foreign key the engine enforces. ``inferred`` arrives with
#: the profiler in WP4.2, which can score a guess; WP4.1 never guesses.
RELATIONSHIP_KINDS: tuple[str, ...] = ("declared", "inferred")

#: What a column is *for*, which the DAL and the composer both reason about.
SEMANTIC_ROLES: tuple[str, ...] = ("measure", "dimension", "time", "id", "other")

#: ``suspected`` is the classifier's opinion and is enough to mask by default;
#: ``confirmed`` is a person's, and only a person may set it (architecture M4).
SENSITIVITY_LEVELS: tuple[str, ...] = ("none", "suspected", "confirmed")

#: What may be done with a column's values. ``mask`` is the automatic default
#: for anything the classifier suspects — the safe direction, chosen before
#: anyone has looked.
COLUMN_POLICIES: tuple[str, ...] = ("allow", "mask", "deny")

#: How far profiling got. ``partial`` is a normal outcome, not an error: a
#: budget that stops is a budget doing its job (architecture Part 5.2).
PROFILE_STATUSES: tuple[str, ...] = ("none", "partial", "complete")

#: The text search configuration table cards are indexed under. 'english', not
#: 'simple': a card is prose, and someone searching "revenue" should find a table
#: whose card says "revenues". Changing it is a migration, because every row's
#: generated index would have to be rebuilt.
CARD_TEXT_CONFIGURATION = "english"


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


class CatalogSnapshot(Base):
    """One crawl of one customer database (architecture Part 5.2, 10.1).

    The unit of consistency *and* of change (DECISIONS D-012). A run reasons
    about one snapshot from beginning to end, so a refresh underneath it cannot
    move the ground; and a crawl that finds every ``structural_hash`` unchanged
    creates no snapshot at all, so the common refresh costs nothing.

    It is also the run record: a crawl that fails leaves a snapshot that never
    reached ``active``, carrying a sanitized ``error``, rather than a row in a
    second table that would have to be joined to learn the same thing.
    """

    __tablename__ = "catalog_snapshots"
    __table_args__ = (
        CheckConstraint(
            "status IN ({})".format(", ".join(f"'{s}'" for s in SNAPSHOT_STATUSES)),
            name="status_valid",
        ),
        CheckConstraint(
            "profile_status IN ({})".format(", ".join(f"'{s}'" for s in PROFILE_STATUSES)),
            name="profile_status_valid",
        ),
        Index(
            "uq_catalog_snapshots_data_source_id_version",
            "data_source_id",
            "version",
            unique=True,
        ),
        # "Which catalog is current" must have exactly one answer, so the
        # database holds us to it rather than the code remembering to.
        Index(
            "uq_catalog_snapshots_one_active",
            "data_source_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[UuidPk]
    org_id: Mapped[OrgId] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'building'")
    )
    captured_at: Mapped[CreatedAt]
    completed_at: Mapped[datetime | None]
    object_count: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
    #: none until the profiler has run over this snapshot; ``partial`` when its
    #: budget stopped it, which is an outcome rather than a failure.
    profile_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'none'")
    )
    profiled_at: Mapped[datetime | None]


class CatalogTable(Base):
    """A table or view as one snapshot found it."""

    __tablename__ = "catalog_tables"
    __table_args__ = (
        CheckConstraint(
            "kind IN ({})".format(", ".join(f"'{k}'" for k in TABLE_KINDS)), name="kind_valid"
        ),
        Index(
            "uq_catalog_tables_snapshot_id_schema_name_table_name",
            "snapshot_id",
            "schema_name",
            "table_name",
            unique=True,
        ),
        Index("ix_catalog_tables_card_tsv", "card_tsv", postgresql_using="gin"),
    )

    id: Mapped[UuidPk]
    org_id: Mapped[OrgId] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalog_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    schema_name: Mapped[str] = mapped_column(String(255), nullable=False)
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    #: sha256 over this table's shape. Two crawls that agree on it agree about
    #: everything WP4.1 stores, which is what makes a refresh cheap.
    structural_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    #: The engine's own estimate, not a count (WP4.2 writes it).
    row_estimate: Mapped[int | None] = mapped_column(BigInteger)
    #: The prose an agent is given instead of a schema dump (WP4.3).
    card_text: Mapped[str | None] = mapped_column(Text)
    #: Where "this card still needs an embedding" is recorded, among other
    #: things a later phase will want to say about a table.
    flags: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    #: Generated by the database, so the index can never disagree with the text
    #: it indexes. Never assigned in Python — writing `card_text` is what
    #: updates it (revision 0009).
    card_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            f"to_tsvector('{CARD_TEXT_CONFIGURATION}', COALESCE(card_text, ''))", persisted=True
        ),
    )


class CatalogColumn(Base):
    """A column of a catalogued table, and what a sample of it looked like.

    The profile fields describe **this snapshot's sample**, so they are rebuilt
    with the snapshot. What an Admin *decided* about the column is not here —
    that is ``ColumnPolicy``, keyed by name, because a decision must survive a
    refresh (DECISIONS D-013).

    ``min_val``, ``max_val`` and ``top_values`` are masked before they are
    written whenever the column is sensitive: a masked sample is the only kind
    of sample that may exist in this database (architecture M4).
    """

    __tablename__ = "catalog_columns"
    __table_args__ = (
        CheckConstraint(
            "semantic_role IS NULL OR semantic_role IN ({})".format(
                ", ".join(f"'{role}'" for role in SEMANTIC_ROLES)
            ),
            name="semantic_role_valid",
        ),
        CheckConstraint(
            "sensitivity IN ({})".format(", ".join(f"'{level}'" for level in SENSITIVITY_LEVELS)),
            name="sensitivity_valid",
        ),
        Index("uq_catalog_columns_table_id_name", "table_id", "name", unique=True),
    )

    id: Mapped[UuidPk]
    org_id: Mapped[OrgId] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    table_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalog_tables.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ordinal: Mapped[int] = mapped_column(nullable=False)
    data_type: Mapped[str] = mapped_column(String(255), nullable=False)
    nullable: Mapped[bool] = mapped_column(nullable=False)
    is_pk: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    description: Mapped[str | None] = mapped_column(Text)

    null_frac: Mapped[float | None]
    distinct_est: Mapped[int | None] = mapped_column(BigInteger)
    min_val: Mapped[str | None] = mapped_column(Text)
    max_val: Mapped[str | None] = mapped_column(Text)
    top_values: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB)
    semantic_role: Mapped[str | None] = mapped_column(String(20))
    sensitivity: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'none'")
    )
    #: How many rows the profile was computed from. Without it, "12% null" is a
    #: number with no idea how much it is worth.
    sample_rows: Mapped[int | None]


class ColumnPolicy(Base):
    """What an Admin decided may be done with one column's values.

    Keyed by *name* rather than by a catalog row, and never written by
    discovery. A refresh that reset somebody's masking decision would be a leak
    caused by a routine operation, which is exactly the kind of failure nobody
    would think to look for (DECISIONS D-013).
    """

    __tablename__ = "column_policies"
    __table_args__ = (
        CheckConstraint(
            "policy IN ({})".format(", ".join(f"'{policy}'" for policy in COLUMN_POLICIES)),
            name="policy_valid",
        ),
        Index(
            "uq_column_policies_column",
            "data_source_id",
            "schema_name",
            "table_name",
            "column_name",
            unique=True,
        ),
    )

    id: Mapped[UuidPk]
    org_id: Mapped[OrgId] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False
    )
    schema_name: Mapped[str] = mapped_column(String(255), nullable=False)
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)
    column_name: Mapped[str] = mapped_column(String(255), nullable=False)
    policy: Mapped[str] = mapped_column(String(20), nullable=False)
    mask_type: Mapped[str | None] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[CreatedAt]


class CatalogRelationship(Base):
    """A join the engine declares (architecture Part 5.2).

    Phase 8's honest refusal depends on this table being a faithful record of
    what exists — including, in the pizza fixture, the absence of any path from
    ``orders`` to ``menu_items``. Inferred edges arrive with the profiler that
    can score them; everything here is ``declared`` and confidence 1.0.
    """

    __tablename__ = "catalog_relationships"
    __table_args__ = (
        CheckConstraint(
            "kind IN ({})".format(", ".join(f"'{k}'" for k in RELATIONSHIP_KINDS)),
            name="kind_valid",
        ),
        Index(
            "ix_catalog_relationships_snapshot_id_from_table",
            "snapshot_id",
            "from_schema",
            "from_table",
        ),
    )

    id: Mapped[UuidPk]
    org_id: Mapped[OrgId] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalog_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    constraint_name: Mapped[str] = mapped_column(String(255), nullable=False)
    from_schema: Mapped[str] = mapped_column(String(255), nullable=False)
    from_table: Mapped[str] = mapped_column(String(255), nullable=False)
    from_columns: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    to_schema: Mapped[str] = mapped_column(String(255), nullable=False)
    to_table: Mapped[str] = mapped_column(String(255), nullable=False)
    to_columns: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'declared'"))
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(precision=3, scale=2), nullable=False, server_default=text("1.0")
    )


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


#: What one execution ended as. ``refused`` is not an error: it is this service
#: declining to send a statement, and it is the status the security questions
#: are asked about.
EXECUTION_STATUSES: tuple[str, ...] = ("ok", "error", "refused")


class QueryExecution(Base):
    """Every attempt to read a customer's database (architecture Part 8.2, 10.1).

    Written for successes, for failures, and for statements this service refused
    to send — the last of which is the row that answers "was anything trying to
    get at what it should not", and the one it would be easiest to omit.

    What is *not* here matters as much: no credential, no unmasked value, and no
    result payload. ``sql_text`` is the canonical statement for anything that
    ran and the submitted one for anything refused, and both are safe to store —
    the first is this application's own generated text, and the second is what a
    refusal message may already quote back (architecture Part 7.4).
    """

    __tablename__ = "query_executions"
    __table_args__ = (
        CheckConstraint(
            "status IN ({})".format(", ".join(f"'{s}'" for s in EXECUTION_STATUSES)),
            name="status_valid",
        ),
        CheckConstraint(
            "(status = 'refused') = (violation_code IS NOT NULL)",
            name="violation_code_matches_status",
        ),
        Index("ix_query_executions_org_id_created_at", "org_id", text("created_at DESC")),
        Index("ix_query_executions_org_id_sql_hash", "org_id", "sql_hash"),
    )

    id: Mapped[UuidPk]
    org_id: Mapped[OrgId] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    #: SET NULL on delete: the record of what was read outlives the registration
    #: of the thing it was read from.
    data_source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="SET NULL")
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    #: Phase 7 gives runs a table and this a foreign key.
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    sql_text: Mapped[str] = mapped_column(Text, nullable=False)
    sql_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Fully qualified names, from the AST rather than from the text.
    tables: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    columns: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Set exactly when status is ``refused``, and enforced by a constraint: a
    #: refusal that does not say what it refused is not a record of anything.
    violation_code: Mapped[str | None] = mapped_column(String(40))
    row_count: Mapped[int | None]
    duration_ms: Mapped[int | None]
    #: Sanitized before it arrives here, by the connector or by the validator.
    error: Mapped[str | None] = mapped_column(Text)
    sensitive_accessed: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    created_at: Mapped[CreatedAt]


class ResultArtifact(Base):
    """What a query returned, kept for as long as the customer allows.

    The rows here have been through ``dal/masking.py``. There is no unmasked
    copy of them anywhere in this database, which is the same rule catalog
    samples follow (DECISIONS D-013) and for the same reason: a store that never
    held the value cannot leak it later.
    """

    __tablename__ = "result_artifacts"
    __table_args__ = (
        Index("ix_result_artifacts_execution", "query_execution_id"),
        Index("ix_result_artifacts_expires_at", "expires_at"),
    )

    id: Mapped[UuidPk]
    org_id: Mapped[OrgId] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    query_execution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("query_executions.id", ondelete="CASCADE"), nullable=False
    )
    #: Shape, not content: column names, row count, which columns were masked.
    summary: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    sample_rows: Mapped[list[list[object]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    truncated: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    #: Where the full result lives in the configured store. None when the sample
    #: is the whole result.
    storage_ref: Mapped[str | None] = mapped_column(String(500))
    #: Retention is a promise, so it is a column rather than a habit.
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    created_at: Mapped[CreatedAt]


#: Architecture 4.9's roles and tiers, as the ledger stores them.
#: ``dataagent.llm.base`` holds the live copies that application code uses; this
#: module deliberately does not import them, because the database schema must not
#: depend on the agent package, and ``test_usage_ledger_matches_the_llm_package``
#: asserts the two lists agree.
LLM_ROLES: tuple[str, ...] = ("intake", "observe", "plan", "sql", "critic", "compose")
LLM_TIERS: tuple[str, ...] = ("small", "mid", "strong")

#: A provider call either answered or it did not. There is no third outcome: a
#: model that answers badly has still answered, and what it said is judged by the
#: critic, not by the ledger.
USAGE_STATUSES: tuple[str, ...] = ("ok", "error")


class UsageLedger(Base):
    """One row per call to a model (architecture Part 4.9, 8.3).

    Written by ``llm/meter.py`` on every path, including failures, because a
    provider that failed after generating tokens has still spent them and a
    fallback that is invisible in the ledger is a cost with no explanation.

    ``role`` and ``tier`` are stored rather than derived from ``model``: the map
    between them is configuration and changes, and "what did moving observe to
    the small tier actually save" is the question 8.3's central claim rests on.

    No prompt and no completion. Those belong to the run's event log under its
    own retention; this table is aggregated and kept.
    """

    __tablename__ = "usage_ledger"
    __table_args__ = (
        CheckConstraint(
            "role IN ({})".format(", ".join(f"'{role}'" for role in LLM_ROLES)), name="role_valid"
        ),
        CheckConstraint(
            "tier IN ({})".format(", ".join(f"'{tier}'" for tier in LLM_TIERS)), name="tier_valid"
        ),
        CheckConstraint(
            "status IN ({})".format(", ".join(f"'{s}'" for s in USAGE_STATUSES)),
            name="status_valid",
        ),
        CheckConstraint("(status = 'error') = (error IS NOT NULL)", name="error_matches_status"),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0", name="token_counts_non_negative"
        ),
        Index("ix_usage_ledger_org_id_created_at", "org_id", text("created_at DESC")),
        Index("ix_usage_ledger_org_id_run_id", "org_id", "run_id"),
    )

    id: Mapped[UuidPk]
    org_id: Mapped[OrgId] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    #: Phase 7 gives runs a table and this a foreign key.
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    tier: Mapped[str] = mapped_column(String(10), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    input_tokens: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    #: True when the counts are our own arithmetic rather than the provider's, so
    #: a total can say how much of itself is measured.
    tokens_estimated: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    #: Null means *not priced*, never free — a model nobody has priced must not
    #: contribute zero to a total a quota is enforced from.
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(precision=12, scale=6))
    latency_ms: Mapped[int | None]
    #: The second call of a parse-then-repair pair.
    repaired: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    #: Sanitized before it arrives: providers raise nothing that has not been.
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[CreatedAt]


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
    "column_policies": "org_id",
    "catalog_snapshots": "org_id",
    "catalog_tables": "org_id",
    "catalog_columns": "org_id",
    "catalog_relationships": "org_id",
    "query_executions": "org_id",
    "result_artifacts": "org_id",
    "usage_ledger": "org_id",
}
