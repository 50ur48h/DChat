"""The metadata pass: what one crawl of a customer database found.

Catalog queries only — the four ``list_*`` calls a connector already has, which
read the engine's own catalog and never touch a customer's rows. Profiling comes
in WP4.2 and is where the budgets and sampling live; this pass is seconds' work
against any schema.

**A crawl that finds no change writes nothing** (DECISIONS D-012). Every table is
reduced to a ``structural_hash`` over its shape; if every hash matches the active
snapshot and no table has appeared or vanished, the crawl records that it looked
and stops. Only a real change builds a new snapshot, and unchanged tables are
copied into it rather than re-inspected — so the expensive work WP4.2 and WP4.3
will hang off a table (profiles, cards, embeddings) is inherited instead of
repeated.

Three orderings here are deliberate:

* **The customer's database is read outside any transaction of ours.** Holding a
  platform transaction open across a network call to somebody else's machine is
  how a slow third party becomes a lock queue on your own — the same rule
  ``datasources.service`` follows for connection tests.
* **The new snapshot is built, then activated in one transaction.** A crawl that
  dies half-written leaves a ``building`` snapshot that nothing reads, and the
  previous ``active`` one still serving.
* **Failure is recorded, not raised away.** A snapshot that could not be built
  stays as ``failed`` with a sanitized message, because "the last refresh failed
  and here is why" is a thing a screen must be able to say.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dataagent.catalog import cards
from dataagent.catalog.inference import InferredKey, infer_relationships
from dataagent.connectors.base import ColumnInfo, Connector, ConnectorError, ForeignKey, TableRef
from dataagent.connectors.introspection import POSTGRES, TSQL
from dataagent.datasources import service as datasources
from dataagent.db.models import (
    CatalogColumn,
    CatalogRelationship,
    CatalogSnapshot,
    CatalogTable,
)
from dataagent.knowledge.embeddings import Embedder
from dataagent.orgs.service import audit
from dataagent.tenancy.session import org_session

__all__ = [
    "DiscoveryOutcome",
    "SnapshotView",
    "discover",
    "structural_hash",
]

STATUS_BUILDING = "building"
STATUS_ACTIVE = "active"
STATUS_FAILED = "failed"
STATUS_SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class SnapshotView:
    """A snapshot as the rest of the application may know it."""

    id: uuid.UUID
    data_source_id: uuid.UUID
    version: int
    status: str
    captured_at: datetime
    completed_at: datetime | None
    object_count: int
    error: str | None


@dataclass(frozen=True, slots=True)
class DiscoveryOutcome:
    """What a refresh did, in the words a screen needs.

    ``changed`` is the honest answer to "did anything happen": false means the
    database looks exactly as it did, and the active snapshot is the one that was
    already there — not a new identical copy.
    """

    snapshot: SnapshotView | None
    changed: bool
    detail: str
    tables: int = 0
    columns: int = 0
    relationships: int = 0


def structural_hash(table: TableRef, columns: Sequence[ColumnInfo]) -> str:
    """A digest of one table's *shape*.

    Everything WP4.1 stores about a table goes in, and nothing else does — so
    two crawls that agree on this hash agree on every catalog row, and a refresh
    can skip the table without hoping. Column order is the engine's ordinal,
    which is part of the shape rather than an accident of iteration.

    Deliberately not a hash of row counts or data: this answers "has the schema
    moved", and a table whose contents changed has not changed shape.
    """
    digest = hashlib.sha256()
    digest.update(
        f"{table.schema}.{table.name}\x00{table.kind}\x00{table.comment or ''}\x00".encode()
    )
    for column in sorted(columns, key=lambda item: item.ordinal):
        digest.update(
            "\x00".join(
                (
                    column.name,
                    str(column.ordinal),
                    column.data_type,
                    "null" if column.nullable else "notnull",
                    "pk" if column.is_primary_key else "-",
                    column.comment or "",
                )
            ).encode()
        )
        digest.update(b"\x1e")
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class _Crawl:
    """Everything the customer's database told us, before any of it is stored."""

    tables: tuple[TableRef, ...]
    columns_by_table: dict[tuple[str, str], tuple[ColumnInfo, ...]]
    relationships: tuple[ForeignKey, ...]
    hashes: dict[tuple[str, str], str]
    #: Joins the engine does not declare, measured rather than guessed (D-050).
    #: Empty when the database declares its keys properly, which is the case
    #: this costs nothing in.
    inferred: tuple[InferredKey, ...] = ()


async def _crawl(connector: Connector, *, dialect: str = POSTGRES) -> _Crawl:
    """Four catalog reads, the hashes that make a refresh cheap, and — where the
    database declares nothing — a measured look for the joins it does have."""
    schemas = await connector.list_schemas()
    tables = await connector.list_tables(schemas)
    columns = await connector.list_columns(schemas)
    relationships = await connector.list_foreign_keys(schemas)

    by_table: dict[tuple[str, str], list[ColumnInfo]] = {}
    for column in columns:
        by_table.setdefault((column.schema, column.table), []).append(column)

    columns_by_table = {key: tuple(value) for key, value in by_table.items()}
    hashes = {
        (table.schema, table.name): structural_hash(
            table, columns_by_table.get((table.schema, table.name), ())
        )
        for table in tables
    }
    # **Only where there is something to find.** A database that declares its
    # keys needs none of this, and the scans are not free — so inference runs
    # when the engine offered nothing at all, which is the case that was
    # refusing every real question (B-145).
    inferred: tuple[InferredKey, ...] = ()
    if not relationships and tables:
        report = await infer_relationships(
            connector,
            tables,
            columns_by_table,
            declared=(),
            dialect=dialect,
        )
        inferred = report.keys

    return _Crawl(
        inferred=inferred,
        tables=tuple(tables),
        columns_by_table=columns_by_table,
        relationships=tuple(relationships),
        hashes=hashes,
    )


async def discover(
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    data_source_id: uuid.UUID,
    embedder: Embedder | None = None,
) -> DiscoveryOutcome:
    """Refresh one data source's catalog. Never raises for a database's sake.

    A source that cannot be reached, or whose credentials were never proven
    read-only, is an answer this returns rather than an exception a route has to
    translate.

    ``embedder`` is passed on to `cards.embed_cards` (**B-018**) and is a
    parameter rather than something resolved here for B-040's reason: a function
    that reached for a spending credential on its own would be one no caller —
    and no test — could stop.
    """
    view = await datasources.get_data_source(org_id, data_source_id)

    if not view.readonly_verified:
        # Discovery is read-only work, but "we never proved these credentials
        # cannot write" is a state to fix before pointing anything at a
        # customer's database on a schedule.
        return DiscoveryOutcome(
            snapshot=None,
            changed=False,
            detail=(
                "This data source has not been proven read-only. Test the "
                "connection first — a catalog is only worth building on "
                "credentials that cannot change the database."
            ),
        )

    try:
        crawl = await _read_the_database(view)
    except ConnectorError as error:
        return await _record_failure(
            org_id=org_id,
            actor_user_id=actor_user_id,
            data_source_id=data_source_id,
            detail=str(error),
        )

    outcome = await _store(
        org_id=org_id,
        actor_user_id=actor_user_id,
        data_source_id=data_source_id,
        crawl=crawl,
    )
    if outcome.changed:
        # A new snapshot's tables have no cards yet, and a catalog nobody can
        # search is half a catalog. Written here rather than left to the caller
        # so there is no path that produces one without the other — and since
        # B-018 that applies to both halves of "searchable": the words and the
        # vector. `embed_cards` is idempotent and a no-op without an embedder.
        await cards.refresh_cards(org_id, data_source_id)
        await cards.embed_cards(org_id, data_source_id, embedder=embedder)
    return outcome


async def _read_the_database(view: datasources.DataSourceView) -> _Crawl:
    """Open a connector, ask it four questions, and close it again.

    Outside any platform transaction, deliberately — see the module docstring.
    """
    connector = await datasources.connector_for_view(view)
    try:
        # `pg`/`mssql` are how a data source names its engine; `postgres`/`tsql`
        # are how `introspection` names a SQL dialect. Two vocabularies, and the
        # constants are used rather than the strings so a rename cannot quietly
        # send T-SQL to Postgres.
        return await _crawl(connector, dialect=POSTGRES if view.engine == "pg" else TSQL)
    finally:
        await connector.aclose()


async def _store(
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    data_source_id: uuid.UUID,
    crawl: _Crawl,
) -> DiscoveryOutcome:
    async with org_session(org_id) as session:
        active = (
            (
                await session.execute(
                    select(CatalogSnapshot).where(
                        CatalogSnapshot.data_source_id == data_source_id,
                        CatalogSnapshot.status == STATUS_ACTIVE,
                    )
                )
            )
            .scalars()
            .one_or_none()
        )

        if active is not None and await _unchanged(session, active, crawl):
            audit(
                session,
                org_id=org_id,
                actor_user_id=actor_user_id,
                action="catalog.refreshed",
                object_type="data_source",
                object_id=str(data_source_id),
                details={"changed": False, "version": active.version},
            )
            return DiscoveryOutcome(
                snapshot=_view(active),
                changed=False,
                detail=(
                    f"No change. The catalog is still version {active.version}, "
                    f"describing {active.object_count} table(s)."
                ),
                tables=active.object_count,
            )

        snapshot = CatalogSnapshot(
            org_id=org_id,
            data_source_id=data_source_id,
            version=(active.version + 1) if active is not None else 1,
            status=STATUS_BUILDING,
        )
        session.add(snapshot)
        await session.flush()

        counts = await _write_catalog(session, org_id=org_id, snapshot_id=snapshot.id, crawl=crawl)

        # The previous catalog is superseded, not deleted: a run that is still
        # going is entitled to the snapshot it started with.
        if active is not None:
            await session.execute(
                update(CatalogSnapshot)
                .where(CatalogSnapshot.id == active.id)
                .values(status=STATUS_SUPERSEDED)
            )

        snapshot.status = STATUS_ACTIVE
        snapshot.completed_at = datetime.now(UTC)
        snapshot.object_count = counts[0]

        audit(
            session,
            org_id=org_id,
            actor_user_id=actor_user_id,
            action="catalog.refreshed",
            object_type="data_source",
            object_id=str(data_source_id),
            details={
                "changed": True,
                "version": snapshot.version,
                "tables": counts[0],
                "columns": counts[1],
                "relationships": counts[2],
            },
        )

        return DiscoveryOutcome(
            snapshot=_view(snapshot),
            changed=True,
            detail=(
                f"Catalog version {snapshot.version}: {counts[0]} table(s), "
                f"{counts[1]} column(s), {counts[2]} relationship(s)."
            ),
            tables=counts[0],
            columns=counts[1],
            relationships=counts[2],
        )


async def _unchanged(session: AsyncSession, active: CatalogSnapshot, crawl: _Crawl) -> bool:
    """Does the active snapshot already describe exactly this database?

    Compared as whole maps, so it answers in both directions: a dropped table
    changes nothing about the tables that remain, and a catalog that still lists
    one would be wrong in the way an agent would never notice.
    """
    rows = (
        await session.execute(
            select(
                CatalogTable.schema_name,
                CatalogTable.table_name,
                CatalogTable.structural_hash,
            ).where(CatalogTable.snapshot_id == active.id)
        )
    ).all()
    stored = {(schema, table): digest for schema, table, digest in rows}
    return stored == crawl.hashes


async def _write_catalog(
    session: AsyncSession, *, org_id: uuid.UUID, snapshot_id: uuid.UUID, crawl: _Crawl
) -> tuple[int, int, int]:
    """Every row of one snapshot. Returns (tables, columns, relationships)."""
    rows = {
        (table.schema, table.name): CatalogTable(
            org_id=org_id,
            snapshot_id=snapshot_id,
            schema_name=table.schema,
            table_name=table.name,
            kind=table.kind,
            structural_hash=crawl.hashes[(table.schema, table.name)],
            description=table.comment,
        )
        for table in crawl.tables
    }
    session.add_all(rows.values())
    # Flushed here so every table has an id for its columns to point at. One
    # round trip for the whole snapshot, not one per table.
    await session.flush()

    columns_written = 0
    for key, row in rows.items():
        columns = crawl.columns_by_table.get(key, ())
        session.add_all(
            CatalogColumn(
                org_id=org_id,
                table_id=row.id,
                name=column.name,
                ordinal=column.ordinal,
                data_type=column.data_type,
                nullable=column.nullable,
                is_pk=column.is_primary_key,
                description=column.comment,
            )
            for column in columns
        )
        columns_written += len(columns)

    session.add_all(
        CatalogRelationship(
            org_id=org_id,
            snapshot_id=snapshot_id,
            constraint_name=key.constraint_name,
            from_schema=key.from_schema,
            from_table=key.from_table,
            from_columns=list(key.from_columns),
            to_schema=key.to_schema,
            to_table=key.to_table,
            to_columns=list(key.to_columns),
        )
        for key in crawl.relationships
    )

    # Inferred edges, each carrying the numbers that justify it. `kind` is what
    # keeps them separable everywhere downstream: the capability check relies on
    # them, and an answer that rests on one is entitled to say so.
    session.add_all(
        CatalogRelationship(
            org_id=org_id,
            snapshot_id=snapshot_id,
            constraint_name=key.constraint_name,
            from_schema=key.from_schema,
            from_table=key.from_table,
            from_columns=[key.from_column],
            to_schema=key.to_schema,
            to_table=key.to_table,
            to_columns=[key.to_column],
            kind="inferred",
            confidence=Decimal(str(key.confidence)),
            evidence=key.evidence,
        )
        for key in crawl.inferred
    )

    return (
        len(crawl.tables),
        columns_written,
        len(crawl.relationships) + len(crawl.inferred),
    )


async def _record_failure(
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    data_source_id: uuid.UUID,
    detail: str,
) -> DiscoveryOutcome:
    """A crawl that could not read the database, remembered rather than lost."""
    async with org_session(org_id) as session:
        previous = (
            (
                await session.execute(
                    select(CatalogSnapshot.version)
                    .where(CatalogSnapshot.data_source_id == data_source_id)
                    .order_by(CatalogSnapshot.version.desc())
                    .limit(1)
                )
            )
            .scalars()
            .one_or_none()
        )
        snapshot = CatalogSnapshot(
            org_id=org_id,
            data_source_id=data_source_id,
            version=(previous or 0) + 1,
            status=STATUS_FAILED,
            completed_at=datetime.now(UTC),
            # Already sanitized by the connector: it names what failed, never a
            # DSN, an address or a credential.
            error=detail,
        )
        session.add(snapshot)
        await session.flush()

        audit(
            session,
            org_id=org_id,
            actor_user_id=actor_user_id,
            action="catalog.refresh_failed",
            object_type="data_source",
            object_id=str(data_source_id),
            details={"detail": detail},
        )
        return DiscoveryOutcome(snapshot=_view(snapshot), changed=False, detail=detail)


def _view(snapshot: CatalogSnapshot) -> SnapshotView:
    return SnapshotView(
        id=snapshot.id,
        data_source_id=snapshot.data_source_id,
        version=snapshot.version,
        status=snapshot.status,
        captured_at=snapshot.captured_at,
        completed_at=snapshot.completed_at,
        object_count=snapshot.object_count,
        error=snapshot.error,
    )
