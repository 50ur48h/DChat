"""Catalog routes (architecture Part 10.2).

Two verbs, and the split between them is the roles: **refreshing** a catalog
reaches out to a customer's database and is Contributor-or-Admin work;
**browsing** one reads rows this organization already owns, and any member may.

Refreshing and profiling both run inline. The metadata pass is four catalog
queries and takes a fifth of a second against the demo databases; profiling is
bounded by a budget it enforces itself, so the longest either can take is a
number this application chose. A job queue for work that stops on its own would
be infrastructure with nothing to carry — and both answers are a snapshot, so
the shape already allows for one when a nightly schedule needs it.

Setting a column policy is Admin work and audited: it is the one route here that
changes what people are allowed to see.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from dataagent.auth.context import RequestContext
from dataagent.auth.guards import require_admin, require_contributor, require_member
from dataagent.catalog import browse, discovery, policies, profiler, search
from dataagent.dal import policy as dal_policy
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
    id: uuid.UUID
    name: str
    ordinal: int
    data_type: str
    nullable: bool
    is_pk: bool
    description: str | None = None
    null_frac: float | None = None
    distinct_est: int | None = Field(
        default=None,
        description=(
            "Distinct values *in the sample*. A floor rather than an estimate when "
            "the column has more distinct values than rows were sampled."
        ),
    )
    min_val: str | None = None
    max_val: str | None = None
    top_values: list[dict[str, object]] | None = Field(
        default=None,
        description=(
            "Masked before storage for anything sensitive, so this is the only "
            "version that ever existed here."
        ),
    )
    semantic_role: str | None = None
    sensitivity: str = Field(
        default="none", description="none | suspected | confirmed — the classifier's view."
    )
    sample_rows: int | None = None
    policy: str = Field(
        default="allow",
        description="What applies now: an Admin's decision, or mask if suspected.",
    )
    policy_decided: bool = Field(
        default=False, description="True when a person set it rather than the default."
    )


class TableOut(BaseModel):
    schema_name: str
    table_name: str
    kind: str
    description: str | None = None
    row_estimate: int | None = None
    card_text: str | None = Field(
        default=None,
        description=(
            "The description an agent is given instead of the schema. Built from "
            "catalog rows only, so its examples are the masked ones."
        ),
    )
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
                row_estimate=table.row_estimate,
                card_text=table.card_text,
                columns=[
                    ColumnOut(
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
                        policy=column.policy,
                        policy_decided=column.policy_decided,
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


class ProfileOut(BaseModel):
    status: str = Field(description="none | partial | complete — partial is a budget working.")
    detail: str
    tables_profiled: int = 0
    columns_profiled: int = 0
    sensitive_columns: int = 0
    tables_skipped: int = 0
    errors: list[str] = Field(
        default_factory=list[str],
        description="Per-table failures, sanitized. One unreadable table does not end the pass.",
    )


class SetPolicyIn(BaseModel):
    policy: Literal["allow", "mask", "deny"]
    reason: str | None = Field(default=None, max_length=2000)
    mask_type: str | None = Field(default=None, max_length=20)


class PolicyOut(BaseModel):
    schema_name: str
    table_name: str
    column_name: str
    policy: str
    mask_type: str | None
    reason: str | None
    decided_by: uuid.UUID | None
    decided_at: datetime


@router.post(
    "/orgs/{org_id}/data-sources/{data_source_id}/profile",
    response_model=ProfileOut,
    summary="Sample each column and classify what looks sensitive",
)
async def profile_catalog(
    context: Annotated[RequestContext, Depends(require_contributor)],
    data_source_id: DataSourceId,
) -> ProfileOut:
    """Reads rows, unlike a refresh — under a budget it enforces itself.

    Answers 200 for a partial pass. A budget that stopped is not an error, and
    an admin needs to know how far it got either way.
    """
    try:
        outcome = await profiler.profile(
            org_id=context.org_id,
            actor_user_id=context.user_id,
            data_source_id=data_source_id,
        )
    except NotFoundError as error:
        raise _not_found() from error

    return ProfileOut(
        status=outcome.status,
        detail=outcome.detail,
        tables_profiled=outcome.tables_profiled,
        columns_profiled=outcome.columns_profiled,
        sensitive_columns=outcome.sensitive_columns,
        tables_skipped=outcome.tables_skipped,
        errors=list(outcome.errors),
    )


@router.patch(
    "/orgs/{org_id}/data-sources/{data_source_id}/columns/{column_id}/policy",
    response_model=PolicyOut,
    summary="Decide what may be done with one column's values",
)
async def set_column_policy(
    body: SetPolicyIn,
    context: Annotated[RequestContext, Depends(require_admin)],
    data_source_id: DataSourceId,
    column_id: Annotated[uuid.UUID, Path(description="A column of the active catalog")],
) -> PolicyOut:
    """Addressed by the catalog column an Admin was looking at; stored by name,
    so the decision outlives the next refresh (DECISIONS D-013)."""
    try:
        decided = await policies.set_policy(
            org_id=context.org_id,
            actor_user_id=context.user_id,
            data_source_id=data_source_id,
            column_id=column_id,
            policy=body.policy,
            reason=body.reason,
            mask_type=body.mask_type,
        )
    except policies.ColumnNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    # The DAL caches this data source's policies for a few seconds. An Admin who
    # has just denied a column expects the next query to be refused, not the one
    # after the TTL, so the decision drops the entry rather than waiting it out.
    dal_policy.invalidate_source(context.org_id, data_source_id)

    return PolicyOut(
        schema_name=decided.schema_name,
        table_name=decided.table_name,
        column_name=decided.column_name,
        policy=decided.policy,
        mask_type=decided.mask_type,
        reason=decided.reason,
        decided_by=decided.decided_by,
        decided_at=decided.decided_at,
    )


class CardHitOut(BaseModel):
    data_source_id: uuid.UUID
    schema_name: str
    table_name: str
    card_text: str
    rank: float = Field(description="Comparable within one result set only, not across queries.")


@router.get(
    "/orgs/{org_id}/catalog/search",
    response_model=list[CardHitOut],
    summary="Find tables by describing what you are looking for",
)
async def search_catalog(
    context: Annotated[RequestContext, Depends(require_member)],
    q: Annotated[str, Query(description="Words to look for. Search-engine syntax works.")],
    data_source_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=search.MAX_LIMIT)] = search.DEFAULT_LIMIT,
) -> list[CardHitOut]:
    """Any member may search: a card describes structure, and its examples were
    masked before they were stored."""
    hits = await search.search_cards(context.org_id, q, data_source_id=data_source_id, limit=limit)
    return [
        CardHitOut(
            data_source_id=hit.data_source_id,
            schema_name=hit.schema_name,
            table_name=hit.table_name,
            card_text=hit.card_text,
            rank=hit.rank,
        )
        for hit in hits
    ]
