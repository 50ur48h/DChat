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

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from dataagent.db.base import ROLES, Base, CreatedAt, OrgId, UuidPk

# Imported for its side effect: the table has to be on Base.metadata or
# Alembic autogenerate would propose dropping it. Not tenant-scoped, so it
# lives outside this module and outside TENANT_TABLES (DECISIONS D-008).
from dataagent.db.security_events import SecurityEvent

__all__ = [
    "CARD_TEXT_CONFIGURATION",
    "CHUNK_TEXT_CONFIGURATION",
    "COLUMN_POLICIES",
    "CONFIDENCE_LEVELS",
    "DATA_SOURCE_ENGINES",
    "DATA_SOURCE_STATUSES",
    "DEFINITION_CHANGES",
    "DEFINITION_KINDS",
    "DEFINITION_STATUSES",
    "DOCUMENT_STATUSES",
    "EMBEDDING_DIMENSIONS",
    "EVENT_TYPES",
    "EXECUTION_STATUSES",
    "LLM_ROLES",
    "LLM_TIERS",
    "MESSAGE_ROLES",
    "PROFILE_STATUSES",
    "RELATIONSHIP_KINDS",
    "RUN_STATUSES",
    "SEMANTIC_ROLES",
    "SENSITIVITY_LEVELS",
    "SNAPSHOT_STATUSES",
    "TABLE_KINDS",
    "TENANT_TABLES",
    "TERMINAL_RUN_STATUSES",
    "USAGE_STATUSES",
    "AgentEvent",
    "AgentRun",
    "AuditLog",
    "Base",
    "CatalogColumn",
    "CatalogRelationship",
    "CatalogSnapshot",
    "CatalogTable",
    "ColumnPolicy",
    "Conversation",
    "DataSource",
    "Finding",
    "Invitation",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "Message",
    "OrgMembership",
    "OrgRecoveryGrant",
    "Organization",
    "QueryExecution",
    "ResultArtifact",
    "SecurityEvent",
    "SemanticDefinition",
    "SemanticDefinitionVersion",
    "UsageLedger",
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

#: The same configuration for knowledge chunks, and the same reasoning: a chunk
#: is prose, so "revenue" should find "revenues". Separate constant rather than
#: a shared one because the two indexes are rebuilt by different migrations, and
#: a single name would make changing one of them look safe.
CHUNK_TEXT_CONFIGURATION = "english"

#: How wide `knowledge_chunks.embedding` is. Fixed by revision 0016 and matched
#: by `Settings.embeddings_dimensions`, which the API asserts at startup — a
#: model returning a different width would have every insert rejected by a
#: constraint nobody was thinking about. 1536 is `text-embedding-3-small`'s,
#: verified against the live account rather than read off a page (B-027).
EMBEDDING_DIMENSIONS = 1536

#: A document is `pending` until it has been extracted, chunked and embedded.
#: `failed` is the value this set exists for: extraction and embedding are the
#: two steps that reach outside the process, so they are the two that fail, and a
#: document silently holding no chunks looks exactly like one nobody uploaded.
DOCUMENT_STATUSES: tuple[str, ...] = ("pending", "indexed", "failed")

#: Architecture 5.4's two kinds (revision 0020). A metric is something you
#: aggregate; a dimension is something you group by.
DEFINITION_KINDS: tuple[str, ...] = ("metric", "dimension")

#: An imported definition constrains generated SQL, so it is a privileged
#: object and arrives as a **proposal** an Admin blesses (B-059, WP10.2d).
#: `retired` rather than deletion, so a run that cited one can still explain
#: itself.
DEFINITION_STATUSES: tuple[str, ...] = ("proposed", "active", "retired")

#: What put a definition into the state a version row records (revision 0022,
#: B-088). `created` and `accepted` are both first versions and are told apart
#: because their provenance differs: one is an Admin's own sentence, the other
#: is the customer's, blessed.
#:
#: `reinstated` is its own word rather than another `updated` (revision 0023,
#: **B-094**): bringing a definition back into force is a decision, not a field
#: edit, and a history that called it an edit would read as though somebody had
#: changed the wording. The gap between retired and active is the thing a reader
#: of the history most needs to see.
DEFINITION_CHANGES: tuple[str, ...] = (
    "created",
    "accepted",
    "updated",
    "retired",
    "reinstated",
)


class Organization(Base):
    """A tenant. Its ``id`` is the ``org_id`` every other tenant row carries."""

    __tablename__ = "organizations"

    id: Mapped[UuidPk]
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    settings: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    #: The database this organization asks questions of, chosen once by an Admin
    #: (revision 0031, **D-045**). Null is not an error: it is every organization
    #: from before that revision and every one whose Admin has not chosen yet,
    #: and such an organization resolves and refuses exactly as it did before.
    #:
    #: ``ON DELETE SET NULL`` rather than a key in ``settings`` above, so that
    #: removing a source degrades the pointer to "none named" — a state the
    #: resolver already handles — instead of leaving an id that resolves to
    #: nothing. It does **not** replace ``conversations.data_source_id``: a
    #: thread still records the source it used (D-022), which is what keeps an
    #: Admin's later change from re-pointing conversations that already ran.
    #:
    #: ``use_alter`` is not cosmetic. ``data_sources.org_id`` points back here, so
    #: this pair is a cycle, and without it SQLAlchemy cannot sort the two tables
    #: — it warns and then **excludes every foreign key on both from
    #: comparison**, which would have left `test_models_and_migrations_do_not_drift`
    #: silently blind to the constraint this column exists for. Naming the
    #: constraint and emitting it as a separate ALTER breaks the cycle for
    #: sorting; the migration already creates it that way.
    active_data_source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "data_sources.id",
            ondelete="SET NULL",
            name="fk_organizations_active_data_source_id",
            use_alter=True,
        )
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


class OrgRecoveryGrant(Base):
    """A way back into an organization whose Admins can no longer sign in (**B-017**).

    An Admin arms one in advance and keeps the token somewhere outside the
    product; whoever holds it can make themselves Admin of that one organization.
    That is a bearer credential and is treated like one — hashed at rest, shown
    once, single-use, revocable, and listed on the members screen so it cannot be
    quietly forgotten.

    **Not an invitation, though it is nearly one.** `accept_invitation` adds a
    membership only when there is not one already, so an existing Reader
    redeeming an Admin invitation stays a Reader — and the locked-out person is
    usually already a member. Invitations also expire in a week, which is no use
    for a credential whose entire purpose is to be waiting years later.

    **No DELETE grant** (revision 0027). That a recovery happened, when, and by
    whom is exactly what an organization needs to be able to show afterwards.
    """

    __tablename__ = "org_recovery_grants"
    __table_args__ = (Index("ix_org_recovery_grants_org_id", "org_id"),)

    id: Mapped[UuidPk]
    org_id: Mapped[OrgId] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    #: What this grant is for, in the Admin's own words. A list of identical rows
    #: is a list nobody audits.
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[CreatedAt]
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    used_at: Mapped[datetime | None]
    used_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    revoked_at: Mapped[datetime | None]


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
        # The backfill's work list (revision 0018, **B-018**): a card with text
        # and no vector. Partial, so "what still needs embedding" never scans.
        Index(
            "ix_catalog_tables_unembedded",
            "org_id",
            "snapshot_id",
            postgresql_where=text("embedding IS NULL AND card_text IS NOT NULL"),
        ),
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
    #: The card's text as a vector (revision 0018, **B-018**). Nullable, and the
    #: null state is a real one: the card is lexically searchable the moment it
    #: is written and semantically searchable once the provider has been called.
    #: Unlike `card_tsv` this is **not** generated — a vector costs a network
    #: round trip and money, so it is written by code that can be told not to.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))


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
    #: What was measured, for an ``inferred`` edge (revision 0032, D-050).
    #: **Null for a declared key and that is the honest value** — a foreign key
    #: the engine states was read, not measured, and ``{}`` would suggest an
    #: empty measurement rather than no measurement.
    evidence: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)


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
    #: SET NULL for the same reason ``data_source_id`` is (D-016): the record of
    #: what was read outlives the run it was read for. Revision 0012 attached it.
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
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
LLM_ROLES: tuple[str, ...] = ("intake", "observe", "plan", "sql", "critic", "compose", "embed")
#: `embed` is its own tier rather than `small` (revision 0017). D-018 says a
#: tier is "how much model this job is worth" on a ladder, and embeddings have
#: no ladder — one model, chosen by EMBEDDINGS_MODEL, that is not a cheaper
#: version of anything. Filing it under `small` would put embedding tokens in
#: the same bucket as intake calls and make any spend-by-tier query wrong.
LLM_TIERS: tuple[str, ...] = ("small", "mid", "strong", "embed")

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
    #: SET NULL rather than CASCADE: what a question cost is still true after the
    #: run row is gone. Revision 0012 attached it.
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
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


#: Architecture 10.1's run status domain. ``validating`` is the critic's window
#: (Phase 9); ``budget_exhausted`` is not a failure — it is a run that spent its
#: allowance and still owes the user an answer with caveats (arch 4.4).
RUN_STATUSES: tuple[str, ...] = (
    "queued",
    "running",
    "validating",
    "completed",
    "interrupted",
    "failed",
    "budget_exhausted",
)

#: The endings. A run in one of these is finished and cannot move again, which is
#: what ``runs/service.py`` enforces and what the ``finished_at`` constraint
#: assumes.
TERMINAL_RUN_STATUSES: tuple[str, ...] = (
    "completed",
    "interrupted",
    "failed",
    "budget_exhausted",
)

MESSAGE_ROLES: tuple[str, ...] = ("user", "assistant")

#: Architecture 10.3's event vocabulary, closed on purpose: the trace UI has to
#: render every one of them, so a type nobody has designed a row for is a bug.
#: A CHECK constraint holds the database to the same list, and
#: ``test_event_types_match_the_migration`` asserts the two copies agree.
EVENT_TYPES: tuple[str, ...] = (
    "run_started",
    "intent_classified",
    "context_selected",
    "capability_checked",
    "plan_created",
    "step_started",
    "tool_called",
    "knowledge_consulted",
    "sql_validated",
    "sql_rejected",
    "query_executed",
    "result_summarized",
    "finding_added",
    "hypothesis_updated",
    "reflection",
    "critic_verdict",
    "budget_warning",
    "budget_exhausted",
    "answer_composed",
    "run_finished",
    "error",
)

#: How sure the agent is of a finding, in architecture 10.3's own words.
CONFIDENCE_LEVELS: tuple[str, ...] = ("high", "medium", "low")


class Conversation(Base):
    """A thread of questions, and the runs they started (architecture 10.1).

    ``user_id`` is nullable and set to NULL when a user row goes away: removing
    somebody from a deployment must not delete the record of what was asked in
    their organization, which is the rule the audit trail follows for the same
    reason.

    ``data_source_id`` is the database this thread is about (revision 0014,
    DECISIONS **D-022**). It lives on the conversation rather than on a message
    because a follow-up question has to reach the same source as the question it
    follows — two answers in one thread drawn from different databases would be
    incomparable and nothing would say so. Null is a conversation that named
    nothing, and it is not an error: the run resolves the organization's single
    source, or refuses and names the choices. ``ON DELETE SET NULL`` for D-016's
    reason — removing a source must not remove the record of what was asked
    about it.
    """

    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_org_id_created_at", "org_id", text("created_at DESC")),
        Index("ix_conversations_data_source_id", "data_source_id"),
        Index("ix_conversations_user_archived", "user_id", "archived_at"),
    )

    id: Mapped[UuidPk]
    org_id: Mapped[OrgId] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    data_source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="SET NULL")
    )
    title: Mapped[str | None] = mapped_column(String(300))
    #: When this thread was put away, or NULL while it is still in the list
    #: (revision 0026, **D-039**). An archive rather than a delete, because a
    #: conversation is the root of its runs, their events and their executions —
    #: the trace architecture 0.2.4 makes durable and `agent_events` holds
    #: append-only by grant. Destroying that from a list screen would remove the
    #: evidence behind answers somebody may already have acted on. A timestamp
    #: rather than a flag: the question worth answering later is *when*.
    archived_at: Mapped[datetime | None]
    created_at: Mapped[CreatedAt]


#: The three endings a finished run can have (**D-044**). Mirrors revision
#: 0030's `OUTCOME_STATES` and `composer.RUN_STATES`;
#: `tests/agent/test_outcome_state.py` is what keeps the three honest.
RUN_OUTCOME_STATES: tuple[str, ...] = ("answered", "partly", "refused")


class AgentRun(Base):
    """One question, from asked to answered (architecture Part 10.1, 4.4).

    The row every other record in the product points at: ``query_executions`` and
    ``usage_ledger`` have carried a ``run_id`` since Phases 5 and 6 and got their
    foreign key to this table in revision 0012, so "what did this question cost,
    and what did it read" is a join rather than a guess.

    ``question`` repeats the user's message deliberately (architecture 10.1 puts
    it here): the trace, the eval harness and a support question all read a run on
    its own, and none of them should need a join to learn what was being answered.

    ``budget`` and ``state`` are Phase 8's — the allowance and the resumable
    checkpoint. They exist from here so a run row never changes shape mid-phase.
    """

    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ({})".format(", ".join(f"'{s}'" for s in RUN_STATUSES)), name="status_valid"
        ),
        # "Finished but with no finish time" is the shape a crashed transition
        # leaves behind, and without this it would stay invisible until a screen
        # rendered a blank where a duration belonged.
        CheckConstraint(
            "(status IN ({})) = (finished_at IS NOT NULL)".format(
                ", ".join(f"'{s}'" for s in TERMINAL_RUN_STATUSES)
            ),
            name="finished_at_matches_status",
        ),
        # **Declared here as well as in revision 0030**, because
        # `test_models_and_migrations_do_not_drift` compares the two and a
        # constraint that exists only in a migration is one autogenerate will
        # propose dropping the next time somebody runs it.
        CheckConstraint(
            "outcome_state IS NULL OR outcome_state IN ({})".format(
                ", ".join(f"'{state}'" for state in RUN_OUTCOME_STATES)
            ),
            name="outcome_state_valid",
        ),
        # The property the card depends on, enforced where it cannot be edited
        # away: `partly` without the missing half is a badge that says less than
        # the wrong one did (**D-044**).
        CheckConstraint(
            "outcome_state <> 'partly' OR (unanswered IS NOT NULL AND unanswered <> '')",
            name="partly_names_what_is_missing",
        ),
        Index(
            "ix_agent_runs_org_id_conversation_id_created_at",
            "org_id",
            "conversation_id",
            text("created_at DESC"),
        ),
    )

    id: Mapped[UuidPk]
    org_id: Mapped[OrgId] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'queued'"))
    question: Mapped[str] = mapped_column(Text, nullable=False)
    budget: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    state: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    #: A rollup for the trace UI; ``usage_ledger`` stays authoritative.
    model_usage: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    #: Null means unpriced, never free — the same rule ``cost_usd`` follows.
    cost_estimate: Mapped[Decimal | None] = mapped_column(Numeric(precision=10, scale=4))
    #: What this answer does not establish, in plain words: a budget that stopped
    #: the search, a critic warning, a period the data does not cover. A column
    #: rather than a field inside ``state`` because a limitation is part of the
    #: answer, and the answer card should not have to read the agent's own
    #: scratchpad to decide what to show a person.
    #: The chart this answer carries, or the reason it carries none (WP11.1,
    #: revision 0024). `{"spec": …}` or `{"declined": …, "code": …}`, and never
    #: both — a refusal is an outcome, and one that had nowhere to live would
    #: reproduce the silence the chart tool exists to prevent. NULL means no
    #: chart was ever asked for, which is most runs.
    chart: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    limitations: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    #: One line on how the answer was reached, for the reader who will not open
    #: the SQL (architecture 4.2's fourth part of an answer; **B-100**, revision
    #: 0025). Built deterministically from the run's own counts by
    #: `composer.method_note` — never from a model's account of its reasoning,
    #: which would be a story about the work rather than a record of it. NULL for
    #: runs composed before the column existed, because a sentence invented for
    #: them now would be a claim nobody made.
    method: Mapped[str | None] = mapped_column(Text)
    #: How the run ended: `answered` | `partly` | `refused` (**B-134**, **D-044**,
    #: revision 0030).
    #:
    #: **Not derivable from `status`**: WP7.2b's rule is that a run which could not
    #: answer *completes*, so `completed` covers all three. And **not a boolean**,
    #: which is what revision 0029 got wrong one day earlier: a question can be
    #: half-answered, and `false` denies the part that was answered while `true`
    #: denies the part that was not.
    #:
    #: **Derived by the platform, never chosen by a model** — `composer.run_state`
    #: reads whether the run produced a verified citation and whether it named
    #: something it could not do. NULL for runs that ended before 0030.
    outcome_state: Mapped[str | None] = mapped_column(Text)
    #: The part of the question the run could not answer, in the composer's words.
    #: A CHECK makes `partly` impossible without it, so the card always has the
    #: missing half to name and *"partly answered"* on its own is unreachable.
    unanswered: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    #: Sanitized before it arrives, like every other error column here.
    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[CreatedAt]


class Message(Base):
    """What was said, by the person and by the agent (architecture 10.1).

    ``idempotency_key`` is beyond 10.1's column list and required by 10.2's
    contract for ``POST …/messages``: a key in the body has to be stored to be
    honoured. It lives here rather than on the run because it identifies the
    *client's* message — a retried POST is the same question, not a second one —
    and it is null for anything the agent writes.
    """

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ({})".format(", ".join(f"'{r}'" for r in MESSAGE_ROLES)), name="role_valid"
        ),
        Index(
            "ix_messages_org_id_conversation_id_created_at",
            "org_id",
            "conversation_id",
            "created_at",
        ),
        # Partial, so the agent's own messages — which carry no key — are not all
        # competing for one NULL slot per conversation.
        Index(
            "uq_messages_idempotency_key",
            "org_id",
            "conversation_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[UuidPk]
    org_id: Mapped[OrgId] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[CreatedAt]


class AgentEvent(Base):
    """One step of one run, as the trace will show it (architecture 10.3).

    Append-only: revision 0012 revokes UPDATE and DELETE on this table from the
    application role, exactly as revision 0002 does for ``audit_log``. It matters
    more here, because this table is the product's honesty claim — a trace that
    could be edited afterwards would be a story rather than a record.

    ``seq`` is 1-based and gap-free within a run, which is what makes
    ``?after=seq`` a complete replay contract. ``runs/events.py`` assigns it under
    the run row's own lock; the unique constraint is what makes a mistake there
    an error rather than a silently duplicated position.
    """

    __tablename__ = "agent_events"
    __table_args__ = (
        CheckConstraint(
            "type IN ({})".format(", ".join(f"'{t}'" for t in EVENT_TYPES)), name="type_valid"
        ),
        CheckConstraint("seq > 0", name="seq_positive"),
        UniqueConstraint("run_id", "seq", name="uq_agent_events_run_id_seq"),
        Index("ix_agent_events_org_id_run_id_seq", "org_id", "run_id", "seq"),
    )

    # bigserial per architecture 10.1: one row per step of every run is the
    # fastest-growing table in the product.
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    org_id: Mapped[OrgId] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(nullable=False)
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    #: Built for eyes (arch 10.3): short public strings out of structured tool
    #: output. Never raw model reasoning, and never an unmasked value.
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    ts: Mapped[CreatedAt]


class Finding(Base):
    """Something the run concluded, and what backs it up (architecture 10.1).

    ``support`` is a list of ``query_executions.id`` values, so a claim in an
    answer can be walked back to the SQL that produced it — which is the citation
    the M7 gate is about.
    """

    __tablename__ = "findings"
    __table_args__ = (
        CheckConstraint(
            "confidence IN ({})".format(", ".join(f"'{c}'" for c in CONFIDENCE_LEVELS)),
            name="confidence_valid",
        ),
        Index("ix_findings_org_id_run_id", "org_id", "run_id"),
    )

    id: Mapped[UuidPk]
    org_id: Mapped[OrgId] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    support: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    confidence: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'medium'")
    )
    #: True when the composed answer rests on this finding. A run reaches several
    #: and an answer uses some; the card shows the used ones as evidence and
    #: leaves the rest in the trace, where they are the investigation's working.
    cited: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[CreatedAt]


class KnowledgeDocument(Base):
    """Something an organization wrote down (architecture 5.5, 10.1).

    The other half of the agent's understanding. The catalog can discover that
    `orders.total_amount` is numeric; only a document can say that net revenue
    excludes cancelled orders — and 5.5's division of labour rests on that:
    **RAG answers "what does this term mean here", the database answers "what is
    the value"**.

    ``status`` has three values and ``failed`` is the one this table exists to
    make visible. Extraction and embedding are the two steps that reach outside
    the process, so they are the two that fail, and a document that silently
    holds no chunks looks identical to one nobody uploaded. The CHECK constraint
    makes a failure without a reason impossible, the same shape WP5.2b gave a
    refusal without a violation code.
    """

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        CheckConstraint(
            "status IN ({})".format(", ".join(f"'{s}'" for s in DOCUMENT_STATUSES)),
            name="status_valid",
        ),
        CheckConstraint(
            "(status = 'failed') = (failure_reason IS NOT NULL)",
            name="failure_reason_matches_status",
        ),
        Index("ix_knowledge_documents_org_id_created_at", "org_id", text("created_at DESC")),
    )

    id: Mapped[UuidPk]
    org_id: Mapped[OrgId] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    #: An `ArtifactStore` key, org-prefixed and checked, not a filesystem path —
    #: the same interface WP5.2b introduced, so Phase 12's move to Blob is a
    #: backend swap rather than a schema change.
    blob_path: Mapped[str] = mapped_column(String(500), nullable=False)
    mime: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'")
    )
    failure_reason: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    #: ``SET NULL`` per D-016: removing a person must not delete the record of
    #: what their organization uploaded.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[CreatedAt]
    indexed_at: Mapped[datetime | None]


class KnowledgeChunk(Base):
    """One passage of a document, as retrieval will find it (architecture 5.5).

    **``embedding`` is nullable, and that is a state rather than an oversight.**
    A chunk exists as soon as its text does and is lexically searchable
    immediately; it becomes semantically searchable once the provider has been
    called, which is a network round trip that can fail or be rate limited. NOT
    NULL would force ingest either to block on the provider or to lose the text.

    **``tsv`` is generated**, for revision 0009's reason: a column the database
    derives cannot disagree with the text it indexes, while one maintained by
    application code eventually will.

    ``seq`` is 0-based and gap-free within a document, which is what makes
    re-indexing idempotent — the old chunks are deleted and rewritten from zero
    rather than appended to — and what lets a citation name a position.
    """

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "seq", name="uq_knowledge_chunks_document_id_seq"),
        Index("ix_knowledge_chunks_org_id_document_id", "org_id", "document_id"),
        Index("ix_knowledge_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_knowledge_chunks_unembedded",
            "org_id",
            "document_id",
            postgresql_where=text("embedding IS NULL"),
        ),
    )

    id: Mapped[UuidPk]
    org_id: Mapped[OrgId] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    #: ``CASCADE``, and deliberately not D-016's ``SET NULL``. That rule is about
    #: records of *acts*, which outlive their subject; a chunk is a derived copy
    #: of a document's own words, and one without its document is text with no
    #: answer to "where is this from" — which is the property that makes
    #: retrieved material safe to show at all (5.5).
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(nullable=False)
    text_: Mapped[str] = mapped_column("text", Text, nullable=False)
    #: The heading trail, outermost first: `["Revenue policy", "Exclusions"]`.
    #: Kept beside the text rather than inside it so provenance can be shown
    #: without the chunk repeating its own context.
    headings: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    token_estimate: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    #: Generated by the database. Never assigned in Python — writing ``text`` is
    #: what updates it (revision 0016).
    tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(f"to_tsvector('{CHUNK_TEXT_CONFIGURATION}', text)", persisted=True),
    )
    created_at: Mapped[CreatedAt]


#: Every tenant-scoped table mapped to the column that identifies its tenant.
#: ``organizations`` is the exception worth spelling out: it *is* the tenant, so
#: its key is ``id``, not ``org_id`` — a policy written against ``org_id`` there
#: would silently fail to compile, or worse, be quietly skipped.
#:
#: WP1.2 builds the RLS policies from this map and WP1.3's proof suite asserts
#: every entry is covered, so a new tenant table cannot be added without
#: isolation and without a test that proves it.


#: `active` is shown to the planner; `retired` is kept and shown to nobody.
#: Retired rather than deleted, so an answer grounded in an example last month
#: is still explainable this month.
VERIFIED_STATUSES = ("active", "retired")


class SemanticDefinition(Base):
    """What a metric means here, in a form a check can read (arch 5.4, D-033).

    Two halves and they do different jobs. ``description`` and ``expression`` are
    **prose for the prompt** — what makes a model use the metric correctly in the
    first place. ``required_filters`` is **structure for the critic** — what
    catches it when it does not. B-078 is why they are separate: a definition the
    model only *read* was one it reasoned its way back out of two iterations
    later, and nothing could object because a paragraph carries no filters to
    compare a statement against.

    Scoped to a **data source**, because a definition names columns and columns
    belong to a database. One organization's two warehouses can each have a
    `net_revenue` and they are not the same metric.
    """

    __tablename__ = "semantic_definitions"
    __table_args__ = (
        CheckConstraint(
            "kind IN ({})".format(", ".join(f"'{k}'" for k in DEFINITION_KINDS)), name="kind_valid"
        ),
        CheckConstraint(
            "status IN ({})".format(", ".join(f"'{s}'" for s in DEFINITION_STATUSES)),
            name="status_valid",
        ),
        UniqueConstraint(
            "data_source_id", "name", name="uq_semantic_definitions_data_source_id_name"
        ),
        Index("ix_semantic_definitions_org_id_data_source_id", "org_id", "data_source_id"),
    )

    id: Mapped[UuidPk]
    org_id: Mapped[OrgId] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False
    )
    #: Lowercased by the service: "Net Revenue" and "net_revenue" are one metric,
    #: and a catalog of near-duplicates is worse than no catalog.
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'metric'"))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    #: `sum(orders.total_amount)`. Rendered into the prompt, **not** parsed and
    #: not enforced — claiming to check an expression nothing compares would be
    #: worse than saying plainly that only the filters bind.
    expression: Mapped[str | None] = mapped_column(Text)
    #: `[{"table": …, "column": …, "op": …, "values": [...]}]`, the half the
    #: critic can act on.
    required_filters: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    #: The words a person might use. Matching the bare name alone would miss
    #: every question a human actually types.
    synonyms: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    #: What this definition makes the **answer** say, or None (revision 0033).
    #: `description` reaches the prompt and `required_filters` reaches the
    #: critic; before this there was nowhere for a limit on what may be *claimed*
    #: to go. MiseQ's own words are the case: *"never label SUM(value_myr) as
    #: total restaurant waste cost"* is not a filter and not a formula, and a
    #: model that dropped it would have been contradicted by nothing.
    #:
    #: Null for most definitions, deliberately. A metric that is simply a formula
    #: should not be made to sound uncertain by a column that always wants
    #: filling — an empty list of caveats is the common case and a good one.
    caveat: Mapped[str | None] = mapped_column(Text)
    #: Where it came from, when it was not typed (B-059, WP10.2d), so drift in
    #: the customer's own table is visible rather than silently stale.
    provenance: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'active'"))
    #: Which row of ``semantic_definition_versions`` this one currently is
    #: (revision 0022, B-088). On the live row so that a reader of this table
    #: alone can say which version bound a query, without joining history.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[CreatedAt]
    #: Bumped by the service on every edit. There is no `UpdatedAt`
    #: annotation in `db/base.py` because this is the first table that
    #: needs one — a definition is edited, unlike almost everything else
    #: here, which is appended.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class SemanticDefinitionVersion(Base):
    """Every state a definition has been in force in (revision 0022, **B-088**).

    A definition **binds**: its ``required_filters`` are enforced against the AST
    of generated SQL. So *"what did this metric require when that answer was
    written"* is a question about whether an answer was right, and an edit that
    overwrites makes it unanswerable. Architecture 5.4 already asked for this —
    definitions are *"validated against the catalog at save time and versioned"*
    — and nothing versioned anything until editing existed to need it (D-036).

    **The whole state, not a diff.** A diff is smaller and cannot answer the
    question without replaying every row before it, which is a reconstruction
    nobody performs while looking at an answer they distrust.

    **A proposal is not a version.** It binds nothing while it waits, and
    numbering sentences an Admin has not agreed to would make version 1 mean two
    different things. The first version is the one that took effect.

    Append-only in the database, not merely by convention: the app role is
    granted SELECT and INSERT and had UPDATE and DELETE revoked, the same shape
    ``audit_log`` uses and for the same reason.
    """

    __tablename__ = "semantic_definition_versions"
    __table_args__ = (
        CheckConstraint(
            "change IN ({})".format(", ".join(f"'{c}'" for c in DEFINITION_CHANGES)),
            name="change_valid",
        ),
        UniqueConstraint(
            "definition_id",
            "version",
            name="uq_semantic_definition_versions_definition_id_version",
        ),
        Index("ix_semantic_definition_versions_org_id_definition_id", "org_id", "definition_id"),
    )

    id: Mapped[UuidPk]
    org_id: Mapped[OrgId] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("semantic_definitions.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    expression: Mapped[str | None] = mapped_column(Text)
    required_filters: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    synonyms: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    #: Mirrored from the live row (revision 0033), because a version that could
    #: not say what the answer was told is not a record of what bound a query.
    caveat: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    #: What put it into this state: created, accepted, updated or retired.
    change: Mapped[str] = mapped_column(String(20), nullable=False)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[CreatedAt]


class VerifiedQuery(Base):
    """An Admin-approved question and the SQL that answers it (arch 5.4).

    A definition says what a word means; a verified query shows what a good
    answer **looks like** in this database — which join, which grain, which date
    column, which of four plausible tables people actually use. Architecture 5.4
    calls it the highest-leverage accuracy feature per dollar, and the reason is
    that a worked example carries judgement that no amount of schema does.

    **It informs and does not bind, and that is the whole reason it is not a
    column on `SemanticDefinition`.** A definition's `required_filters` are
    checked against the AST and a statement ignoring them is blocked; an example
    is shown to the planner and nothing checks that the model followed it.
    Demanding it did would be a false block on every question that merely
    resembles the example — and standing note 5 calls a false block this
    component's characteristic failure.

    ``sql`` is validated at write time by the same validator that guards
    execution, so an Admin cannot bless a statement the platform would refuse.
    An approved example naming a table that does not exist would be a worked
    demonstration of hallucination sitting in the prompt.
    """

    __tablename__ = "verified_queries"
    __table_args__ = (
        CheckConstraint(
            "status IN ({})".format(", ".join(f"'{s}'" for s in VERIFIED_STATUSES)),
            name="status_valid",
        ),
        UniqueConstraint(
            "data_source_id", "question", name="uq_verified_queries_data_source_id_question"
        ),
        Index("ix_verified_queries_org_id_data_source_id", "org_id", "data_source_id"),
    )

    id: Mapped[UuidPk]
    org_id: Mapped[OrgId] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False
    )
    #: In the words a person would ask it. Matched against a new question by
    #: overlap, so the phrasing is load-bearing rather than a label.
    question: Mapped[str] = mapped_column(Text, nullable=False)
    sql: Mapped[str] = mapped_column(Text, nullable=False)
    #: Why this shape is the right one. An example without its reason teaches
    #: the SQL and not the judgement.
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'active'"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


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
    "conversations": "org_id",
    "agent_runs": "org_id",
    "messages": "org_id",
    "agent_events": "org_id",
    "findings": "org_id",
    "knowledge_documents": "org_id",
    "knowledge_chunks": "org_id",
    "semantic_definitions": "org_id",
    "semantic_definition_versions": "org_id",
    "verified_queries": "org_id",
    "org_recovery_grants": "org_id",
}
