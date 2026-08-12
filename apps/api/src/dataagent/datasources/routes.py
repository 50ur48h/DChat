"""Data-source routes (architecture Part 10.2).

Managing data sources is Admin-only (Part 6.2). Listing them is not: every member
needs to know which databases their questions can reach, and the list carries
nothing a Reader may not see.

The response models are the enforcement point for "credentials are never echoed".
There is no field on ``DataSourceOut`` that could carry one — not optional, not
nullable, absent — so echoing a password would take a schema change and a review,
not a slip. ``test_no_response_model_exposes_a_credential`` walks the OpenAPI
schema and fails if one ever appears.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field, SecretStr

from dataagent.auth.context import RequestContext
from dataagent.auth.guards import require_admin, require_member
from dataagent.datasources import service
from dataagent.datasources.service import DataSourceView, NotFoundError
from dataagent.orgs.service import ConflictError

router = APIRouter(prefix="/v1", tags=["data sources"])

#: Mirrors ``models.DATA_SOURCE_ENGINES``; a test asserts the two agree. Spelled
#: as a Literal so an unknown engine is a 422 with a list of what is accepted,
#: rather than a 500 from a CHECK constraint at the far end.
Engine = Literal["pg", "mssql"]

DataSourceId = Annotated[uuid.UUID, Path(description="Data source within this organization")]


class CreateDataSourceIn(BaseModel):
    """Everything needed to register a database.

    ``password`` is a ``SecretStr``: pydantic renders it as ``**********`` in any
    repr, which is what a traceback, a validation error and a debug log all print.
    """

    name: str = Field(min_length=1, max_length=200)
    engine: Engine
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    database: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=1024)


class UpdateDataSourceIn(BaseModel):
    """Every field optional: this is a rename, a re-address, or a rotation."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = Field(default=None, min_length=1, max_length=128)
    username: str | None = Field(default=None, min_length=1, max_length=128)
    password: SecretStr | None = Field(default=None, min_length=1, max_length=1024)


class DataSourceOut(BaseModel):
    id: uuid.UUID
    name: str
    engine: str
    host: str
    port: int
    database: str
    host_display: str
    status: str
    secret_ref: str = Field(
        description=(
            "Where the credentials are kept, not the credentials. Useless without "
            "the secrets backend it names."
        )
    )
    username_last4: str = Field(
        description="The last four characters of the connecting username, and no more."
    )
    created_by: uuid.UUID | None
    created_at: datetime


class TestResultOut(BaseModel):
    reachable: bool
    detail: str = Field(description="Sanitized: connection strings and addresses are stripped.")
    checked_at: datetime


def _out(view: DataSourceView) -> DataSourceOut:
    return DataSourceOut(
        id=view.id,
        name=view.name,
        engine=view.engine,
        host=view.host,
        port=view.port,
        database=view.database,
        host_display=view.host_display,
        status=view.status,
        secret_ref=view.secret_ref,
        username_last4=view.username_last4,
        created_by=view.created_by,
        created_at=view.created_at,
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="No such data source in this organization"
    )


@router.get(
    "/orgs/{org_id}/data-sources",
    response_model=list[DataSourceOut],
    summary="List this organization's data sources",
)
async def list_data_sources(
    context: Annotated[RequestContext, Depends(require_member)],
) -> list[DataSourceOut]:
    return [_out(view) for view in await service.list_data_sources(context.org_id)]


@router.post(
    "/orgs/{org_id}/data-sources",
    response_model=DataSourceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a database; credentials go to the secrets store",
)
async def create_data_source(
    body: CreateDataSourceIn, context: Annotated[RequestContext, Depends(require_admin)]
) -> DataSourceOut:
    try:
        view = await service.create_data_source(
            org_id=context.org_id,
            actor_user_id=context.user_id,
            name=body.name,
            engine=body.engine,
            host=body.host,
            port=body.port,
            database=body.database,
            username=body.username,
            password=body.password.get_secret_value(),
        )
    except ConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return _out(view)


@router.get(
    "/orgs/{org_id}/data-sources/{data_source_id}",
    response_model=DataSourceOut,
    summary="One data source",
)
async def get_data_source(
    context: Annotated[RequestContext, Depends(require_member)], data_source_id: DataSourceId
) -> DataSourceOut:
    try:
        return _out(await service.get_data_source(context.org_id, data_source_id))
    except NotFoundError as error:
        raise _not_found() from error


@router.patch(
    "/orgs/{org_id}/data-sources/{data_source_id}",
    response_model=DataSourceOut,
    summary="Rename, re-address, or rotate credentials",
)
async def update_data_source(
    body: UpdateDataSourceIn,
    context: Annotated[RequestContext, Depends(require_admin)],
    data_source_id: DataSourceId,
) -> DataSourceOut:
    try:
        view = await service.update_data_source(
            org_id=context.org_id,
            actor_user_id=context.user_id,
            data_source_id=data_source_id,
            name=body.name,
            host=body.host,
            port=body.port,
            database=body.database,
            username=body.username,
            password=body.password.get_secret_value() if body.password is not None else None,
        )
    except NotFoundError as error:
        raise _not_found() from error
    return _out(view)


@router.delete(
    "/orgs/{org_id}/data-sources/{data_source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a data source and its stored credentials",
)
async def delete_data_source(
    context: Annotated[RequestContext, Depends(require_admin)], data_source_id: DataSourceId
) -> None:
    try:
        await service.delete_data_source(
            org_id=context.org_id,
            actor_user_id=context.user_id,
            data_source_id=data_source_id,
        )
    except NotFoundError as error:
        raise _not_found() from error


@router.post(
    "/orgs/{org_id}/data-sources/{data_source_id}/test",
    response_model=TestResultOut,
    summary="Check that the recorded address answers",
)
async def test_data_source(
    context: Annotated[RequestContext, Depends(require_admin)], data_source_id: DataSourceId
) -> TestResultOut:
    """Transport-level only in this work package.

    Whether the credentials work, and whether they are genuinely read-only, is
    checked by the connector in WP3.2 and recorded on the row as ``verified``.
    Until then this answers the question that fails most often — is the address
    reachable from here at all — and answers it without sending a credential.
    """
    try:
        result = await service.test_data_source(
            org_id=context.org_id,
            actor_user_id=context.user_id,
            data_source_id=data_source_id,
        )
    except NotFoundError as error:
        raise _not_found() from error
    return TestResultOut(
        reachable=result.reachable, detail=result.detail, checked_at=result.checked_at
    )
