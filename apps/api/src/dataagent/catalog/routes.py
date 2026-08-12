"""Catalog routes (architecture Part 10.2).

Two verbs, and the split between them is the roles: **refreshing** a catalog
reaches out to a customer's database and is Contributor-or-Admin work;
**browsing** one reads rows this organization already owns, and any member may.

The refresh runs inline. Architecture Part 5.2 calls the metadata pass "seconds"
— it is four catalog queries — so a request that waits for it is honest, and a
job queue for something this size would be infrastructure with nothing to carry.
When WP4.2 adds profiling, which is minutes and budgeted, that is the work that
needs a background runner and a pollable status; this endpoint's shape already
allows for it, because the answer is a snapshot rather than a stream.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field

from dataagent.auth.context import RequestContext
from dataagent.auth.guards import require_contributor, require_member
from dataagent.catalog import browse, discovery
from dataagent.datasources.service import NotFoundError

router = APIRouter(prefix="/v1", tags=["catalog"])

DataSourceId = Annotated[uuid.UUID, Path(description="Data source within this organization")]


class SnapshotOut(BaseModel):
    id: uuid.UUID
    version: int = Field(description="Monotonic per data source. A no-change refresh spends none.")
    status: str = Field(description="building | active | failed | superseded")
    captured_at: datetime
    completed_at: datetime | None
    object_count: int
    error: str | None = Field(
        default=None, description="Sanitized: names what failed, never an address or a credential."
    )


class RefreshOut(BaseModel):
    changed: bool = Field(
        description=(
            "False when the database looks exactly as it did. The active snapshot "
            "is then the one that was already there, not a new identical copy."
        )
    )
    detail: str
    tables: int = 0
    columns: int = 0
    relationships: int = 0
    snapshot: SnapshotOut | None = None


class ColumnOut(BaseModel):
    name: str
    ordinal: int
    data_type: str
    nullable: bool
    is_pk: bool
    description: str | None = None


class TableOut(BaseModel):
    schema_name: str
    table_name: str
    kind: str
    description: str | None = None
    columns: list[ColumnOut] = Field(default_factory=list[ColumnOut])


class RelationshipOut(BaseModel):
    constraint_name: str
    from_schema: str
    from_table: str
    from_columns: list[str]
    to_schema: str
    to_table: str
    to_columns: list[str]
    kind: str
    confidence: float


class CatalogOut(BaseModel):
    """One snapshot, whole. The shape Phase 4.3's browser and Phase 5's
    grounding both read."""

    snapshot: SnapshotOut
    tables: list[TableOut]
    relationships: list[RelationshipOut]


def _snapshot_out(snapshot: discovery.SnapshotView) -> SnapshotOut:
    return SnapshotOut(
        id=snapshot.id,
        version=snapshot.version,
        status=snapshot.status,
        captured_at=snapshot.captured_at,
        completed_at=snapshot.completed_at,
        object_count=snapshot.object_count,
        error=snapshot.error,
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="No such data source in this organization"
    )


@router.post(
    "/orgs/{org_id}/data-sources/{data_source_id}/refresh",
    response_model=RefreshOut,
    summary="Re-read a database's structure into a catalog snapshot",
)
async def refresh_catalog(
    context: Annotated[RequestContext, Depends(require_contributor)],
    data_source_id: DataSourceId,
) -> RefreshOut:
    """Answers 200 for a refresh that found nothing to do, and for one that
    failed: both are outcomes an admin needs the detail of, not request errors.
    """
    try:
        outcome = await discovery.discover(
            org_id=context.org_id,
            actor_user_id=context.user_id,
            data_source_id=data_source_id,
        )
    except NotFoundError as error:
        raise _not_found() from error

    return RefreshOut(
        changed=outcome.changed,
        detail=outcome.detail,
        tables=outcome.tables,
        columns=outcome.columns,
        relationships=outcome.relationships,
        snapshot=_snapshot_out(outcome.snapshot) if outcome.snapshot is not None else None,
    )


@router.get(
    "/orgs/{org_id}/data-sources/{data_source_id}/catalog",
    response_model=CatalogOut,
    summary="The current catalog for a data source",
)
async def get_catalog(
    context: Annotated[RequestContext, Depends(require_member)],
    data_source_id: DataSourceId,
) -> CatalogOut:
    try:
        catalog = await browse.active_catalog(context.org_id, data_source_id)
    except NotFoundError as error:
        raise _not_found() from error
    except browse.NoCatalogError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    return CatalogOut(
        snapshot=_snapshot_out(catalog.snapshot),
        tables=[
            TableOut(
                schema_name=table.schema_name,
                table_name=table.table_name,
                kind=table.kind,
                description=table.description,
                columns=[
                    ColumnOut(
                        name=column.name,
                        ordinal=column.ordinal,
                        data_type=column.data_type,
                        nullable=column.nullable,
                        is_pk=column.is_pk,
                        description=column.description,
                    )
                    for column in table.columns
                ],
            )
            for table in catalog.tables
        ],
        relationships=[
            RelationshipOut(
                constraint_name=edge.constraint_name,
                from_schema=edge.from_schema,
                from_table=edge.from_table,
                from_columns=list(edge.from_columns),
                to_schema=edge.to_schema,
                to_table=edge.to_table,
                to_columns=list(edge.to_columns),
                kind=edge.kind,
                confidence=edge.confidence,
            )
            for edge in catalog.relationships
        ],
    )
