"""knowledge_documents and knowledge_chunks — what an organization has written down

WP10.1, architecture Part 5.5 and 10.1. Two tenant tables, and between them they
hold the half of the agent's understanding that no amount of schema discovery can
produce: what a term means *here*. The catalog says `orders.total_amount` is a
numeric column; only a document says net revenue excludes cancelled orders.

Four things are deliberate.

**The vector's width is fixed here, and configuration must agree with it.**
``embedding vector(1536)`` is what architecture 10.1 specifies and what
`text-embedding-3-small` returns — verified against the live account on
2026-08-17 rather than read off a documentation page (B-027's habit), because a
model that returns 3072 would have every insert rejected by a constraint nobody
was thinking about. `EMBEDDINGS_DIMENSIONS` exists so the mismatch is an error at
startup with both numbers in it, rather than a write failure under load.

**The embedding is nullable and that is a state, not an oversight.** A chunk
exists as soon as its text does; it becomes searchable by vector when the
provider has been called, which is a network round trip that can fail, be rate
limited, or simply not have been reached yet. A NOT NULL column would mean
ingest either blocks on the provider or loses the text, and both are worse than a
chunk that is lexically searchable now and semantically searchable shortly.
``ix_knowledge_chunks_unembedded`` is a partial index over exactly those rows, so
the backfill's work list is a cheap query rather than a table scan.

**``tsv`` is generated, for revision 0009's reason.** A trigger-maintained or
application-maintained column can disagree with the text it indexes; a generated
one cannot, because there is no moment at which the two are written separately.
The configuration is pinned to ``english`` explicitly — a default that moves with
the database's own setting would silently re-tokenize every chunk.

**Deleting a document deletes its chunks, and that is the point.** Architecture
5.5 makes retrieval provenance-carrying: a chunk without its document is text
with no answer to "where does this come from", which is exactly the property that
makes retrieved material safe to show. ``ON DELETE CASCADE`` rather than
``SET NULL``, which is the opposite of D-016's rule for `run_id` — because that
rule is about *records of acts*, which outlive their subject, and this is a
derived copy of a document's own words, which does not.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "dataagent_app"
POLICY = "org_isolation"

DOCUMENTS = "knowledge_documents"
CHUNKS = "knowledge_chunks"

#: Architecture 10.1's width, and `text-embedding-3-small`'s. Written as a
#: constant so the two places this revision mentions it cannot drift.
EMBEDDING_DIMENSIONS = 1536

#: md | txt | pdf for V1 (plan WP10.1). A CHECK rather than a lookup table: the
#: set is small, closed, and changing it is a migration either way.
DOCUMENT_STATUSES = ("pending", "indexed", "failed")


def _in_list(column: str, values: Sequence[str]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{value}'" for value in values))


def upgrade() -> None:
    # The image is `pgvector/pgvector:pg16`, so the extension is present and
    # merely has to be created. Stated here rather than assumed of the server,
    # because a deployment that forgot it would fail on the CREATE TABLE below
    # with a message about an unknown type rather than about a missing
    # extension.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        DOCUMENTS,
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        # Where the original went. Local files now, Blob in Phase 12 behind the
        # same `ArtifactStore` interface WP5.2b introduced — so this is a key
        # rather than a path, and it is org-prefixed for the reason that
        # interface already checks.
        sa.Column("blob_path", sa.String(500), nullable=False),
        sa.Column("mime", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        # Why an indexing attempt failed, in words a person can act on. Null on
        # the happy path; a document that failed with no reason is a support
        # ticket with nothing in it.
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        # `SET NULL` per D-016: removing a person from a deployment must not
        # delete the record of what their organization uploaded.
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(_in_list("status", DOCUMENT_STATUSES), name="status_valid"),
        # A document that failed says why, and one that did not carries no
        # excuse — the same shape `query_executions` uses for a refusal's
        # violation code (WP5.2b), and for the same reason: a state that can
        # exist without its explanation will eventually exist without it.
        sa.CheckConstraint(
            "(status = 'failed') = (failure_reason IS NOT NULL)",
            name="failure_reason_matches_status",
        ),
    )
    op.create_index(
        "ix_knowledge_documents_org_id_created_at",
        DOCUMENTS,
        ["org_id", sa.text("created_at DESC")],
    )

    op.create_table(
        CHUNKS,
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Position within the document, 0-based and gap-free. It is what lets a
        # citation say "the third chunk of the revenue policy" and what makes
        # re-indexing idempotent: the old chunks are deleted and rewritten from
        # zero rather than appended to.
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        # The heading trail this chunk sits under, outermost first —
        # `["Revenue policy", "Exclusions"]`. Carried separately from the text so
        # retrieval can show provenance without the chunk having to repeat its
        # own context, and so a heading-only match is possible later.
        sa.Column(
            "headings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("token_estimate", sa.Integer(), nullable=False, server_default=sa.text("0")),
        # Nullable on purpose — see the module docstring. A chunk is lexically
        # searchable the moment it is written and semantically searchable once
        # the provider has been called.
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], [f"{DOCUMENTS}.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("document_id", "seq", name="uq_knowledge_chunks_document_id_seq"),
    )
    # A **generated** tsvector, for revision 0009's reason: a column maintained
    # by a trigger or by application code can disagree with the text it indexes,
    # and one the database derives cannot. Added as DDL because `GENERATED ALWAYS
    # AS ... STORED` is beyond what `create_table` expresses.
    op.execute(f"""
        ALTER TABLE {CHUNKS}
        ADD COLUMN tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
    """)
    op.create_index("ix_knowledge_chunks_org_id_document_id", CHUNKS, ["org_id", "document_id"])
    op.execute(f"CREATE INDEX ix_knowledge_chunks_tsv ON {CHUNKS} USING gin (tsv)")
    # Partial, over exactly the rows the backfill has to visit. Without it,
    # "what still needs embedding" is a scan of every chunk in the deployment.
    op.execute(f"""
        CREATE INDEX ix_knowledge_chunks_unembedded ON {CHUNKS} (org_id, document_id)
        WHERE embedding IS NULL
    """)
    # **No vector index, deliberately.** An IVFFlat index must be built against
    # rows that already exist — building it on an empty table produces lists that
    # describe nothing and searches that miss — and HNSW's build cost is real
    # money on a B-series server. At the volumes V1 carries, an exact scan over
    # one organization's chunks is fast and, unlike an approximate index, cannot
    # silently fail to return the best match. The index is a Phase 12 decision
    # with a measurement behind it, which is B-071.
    for table in (DOCUMENTS, CHUNKS):
        # Revision 0002's ALTER DEFAULT PRIVILEGES already covers tables created
        # afterwards by the same owner. Stated again so this revision is true on
        # its own, as 0004, 0007, 0010, 0011 and 0012 do.
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {POLICY} ON {table}
            USING (org_id = current_setting('app.org_id')::uuid)
            WITH CHECK (org_id = current_setting('app.org_id')::uuid)
        """)


def downgrade() -> None:
    op.drop_table(CHUNKS)
    op.drop_table(DOCUMENTS)
    # The extension is left alone. Another table may come to use it, and
    # dropping a database-wide extension because one revision is being reversed
    # is a wider act than the revision was.
