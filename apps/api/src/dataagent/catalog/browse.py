"""Reading a catalog back out (architecture Part 5.3).

The counterpart to ``discovery``: that module talks to a customer's database and
writes rows; this one only ever reads the rows, and never opens a connector. A
member browsing the catalog should not be able to cause a connection to anyone's
database — that is Contributor work, and it lives on the other side of this line.

Everything here reads the **active** snapshot. Superseded ones are kept for runs
that are still going (DECISIONS D-012) and are not reachable from these
functions; when Phase 7 gives a run a snapshot to pin, it will ask for that
snapshot by id.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dataagent.catalog.discovery import STATUS_ACTIVE, SnapshotView
from dataagent.catalog.policies import effective_policy
from dataagent.db.models import (
    CatalogColumn,
    CatalogRelationship,
    CatalogSnapshot,
    CatalogTable,
    ColumnPolicy,
)
from dataagent.tenancy.session import org_session

__all__ = ["Catalog", "CatalogColumnView", "CatalogTableView", "NoCatalogError", "active_catalog"]


class NoCatalogError(Exception):
    """This data source has never been discovered successfully."""


@dataclass(frozen=True, slots=True)
class CatalogColumnView:
    id: uuid.UUID
    name: str
    ordinal: int
    data_type: str
    nullable: bool
    is_pk: bool
    description: str | None
    #: None until the profiler has run over this snapshot (WP4.2).
    null_frac: float | None = None
    distinct_est: int | None = None
    min_val: str | None = None
    max_val: str | None = None
    top_values: list[dict[str, object]] | None = None
    semantic_role: str | None = None
    sensitivity: str = "none"
    sample_rows: int | None = None
    #: What applies right now: an Admin's decision, or the safe default that
    #: suspicion implies. Never null — "nobody decided" is still an answer.
    policy: str = "allow"
    #: True when a person set it, rather than the classifier's default.
    policy_decided: bool = False


@dataclass(frozen=True, slots=True)
class CatalogTableView:
    schema_name: str
    table_name: str
    kind: str
    description: str | None
    columns: tuple[CatalogColumnView, ...]
    row_estimate: int | None = None
    #: The prose an agent reads instead of the schema (WP4.3).
    card_text: str | None = None


@dataclass(frozen=True, slots=True)
class RelationshipView:
    constraint_name: str
    from_schema: str
    from_table: str
    from_columns: tuple[str, ...]
    to_schema: str
    to_table: str
    to_columns: tuple[str, ...]
    kind: str
    confidence: float


@dataclass(frozen=True, slots=True)
class Catalog:
    snapshot: SnapshotView
    tables: tuple[CatalogTableView, ...]
    relationships: tuple[RelationshipView, ...]


async def active_catalog(org_id: uuid.UUID, data_source_id: uuid.UUID) -> Catalog:
    """The current catalog for one data source, whole.

    Three queries rather than one join: a table with fifty columns would
    otherwise arrive fifty times, and the assembly is a dictionary lookup.
    """
    async with org_session(org_id) as session:
        snapshot = (
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
        if snapshot is None:
            raise NoCatalogError(
                "This data source has no catalog yet. Refresh it to read its structure."
            )

        return Catalog(
            snapshot=SnapshotView(
                id=snapshot.id,
                data_source_id=snapshot.data_source_id,
                version=snapshot.version,
                status=snapshot.status,
                captured_at=snapshot.captured_at,
                completed_at=snapshot.completed_at,
                object_count=snapshot.object_count,
                error=snapshot.error,
            ),
            tables=await _tables(session, snapshot.id, data_source_id),
            relationships=await _relationships(session, snapshot.id),
        )


async def _tables(
    session: AsyncSession, snapshot_id: uuid.UUID, data_source_id: uuid.UUID
) -> tuple[CatalogTableView, ...]:
    tables = (
        (
            await session.execute(
                select(CatalogTable)
                .where(CatalogTable.snapshot_id == snapshot_id)
                .order_by(CatalogTable.schema_name, CatalogTable.table_name)
            )
        )
        .scalars()
        .all()
    )
    columns = (
        (
            await session.execute(
                select(CatalogColumn)
                .join(CatalogTable, CatalogTable.id == CatalogColumn.table_id)
                .where(CatalogTable.snapshot_id == snapshot_id)
                .order_by(CatalogColumn.ordinal)
            )
        )
        .scalars()
        .all()
    )

    # Policies are keyed by name, not by catalog row, so they are fetched once
    # for the data source and looked up — that is the whole point of them
    # outliving a snapshot (D-013).
    decisions = {
        (policy.schema_name, policy.table_name, policy.column_name): policy
        for policy in (
            (
                await session.execute(
                    select(ColumnPolicy).where(ColumnPolicy.data_source_id == data_source_id)
                )
            )
            .scalars()
            .all()
        )
    }
    named = {table.id: (table.schema_name, table.table_name) for table in tables}

    by_table: dict[uuid.UUID, list[CatalogColumnView]] = {}
    for column in columns:
        schema_name, table_name = named[column.table_id]
        decision = decisions.get((schema_name, table_name, column.name))
        by_table.setdefault(column.table_id, []).append(
            CatalogColumnView(
                id=column.id,
                name=column.name,
                ordinal=column.ordinal,
                data_type=column.data_type,
                nullable=column.nullable,
                is_pk=column.is_pk,
                description=column.description,
                null_frac=column.null_frac,
                distinct_est=column.distinct_est,
                min_val=column.min_val,
                max_val=column.max_val,
                top_values=column.top_values,
                semantic_role=column.semantic_role,
                sensitivity=column.sensitivity,
                sample_rows=column.sample_rows,
                policy=effective_policy(
                    stored=decision.policy if decision else None,
                    sensitivity=column.sensitivity,
                ),
                policy_decided=decision is not None and decision.decided_by is not None,
            )
        )

    return tuple(
        CatalogTableView(
            schema_name=table.schema_name,
            table_name=table.table_name,
            kind=table.kind,
            description=table.description,
            columns=tuple(by_table.get(table.id, ())),
            row_estimate=table.row_estimate,
            card_text=table.card_text,
        )
        for table in tables
    )


async def _relationships(
    session: AsyncSession, snapshot_id: uuid.UUID
) -> tuple[RelationshipView, ...]:
    edges = (
        (
            await session.execute(
                select(CatalogRelationship)
                .where(CatalogRelationship.snapshot_id == snapshot_id)
                .order_by(
                    CatalogRelationship.from_schema,
                    CatalogRelationship.from_table,
                    CatalogRelationship.constraint_name,
                )
            )
        )
        .scalars()
        .all()
    )
    return tuple(
        RelationshipView(
            constraint_name=edge.constraint_name,
            from_schema=edge.from_schema,
            from_table=edge.from_table,
            from_columns=tuple(edge.from_columns),
            to_schema=edge.to_schema,
            to_table=edge.to_table,
            to_columns=tuple(edge.to_columns),
            kind=edge.kind,
            confidence=float(edge.confidence),
        )
        for edge in edges
    )
