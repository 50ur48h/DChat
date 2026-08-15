"""Finding a table, and looking at one (architecture Part 4.6).

Both tools read the **catalog**, never a customer's database. That is the whole
reason they exist: architecture 7.5 refuses `information_schema` and `pg_catalog`
in the DAL, so an agent that wants to know what columns a table has must ask this
service rather than write a query about it. Metadata questions therefore never
open a socket to a customer database, and a model cannot go fishing through the
schema by writing SQL.

What comes back is masked by construction. Cards are prose built from catalog
rows whose samples were masked on the way in (D-013), and a column's ``policy``
is reported so the model can avoid writing SQL the DAL will refuse.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from dataagent.agent.tools.base import Tool, ToolContext, ToolError
from dataagent.catalog.browse import NoCatalogError, active_catalog
from dataagent.catalog.search import MAX_LIMIT, search_cards
from dataagent.datasources.service import NotFoundError

__all__ = ["DESCRIBE_TABLE", "SEARCH_TABLES"]


class SearchTablesIn(BaseModel):
    """``extra="forbid"`` and no optional-without-default fields, so a provider
    that supports natively-constrained decoding can enforce this schema rather
    than merely suggest it (B-033)."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1, max_length=400, description="What you are looking for, in words."
    )
    limit: int = Field(default=5, ge=1, le=MAX_LIMIT)


class TableMatch(BaseModel):
    schema_name: str
    table_name: str
    card: str


class SearchTablesOut(BaseModel):
    matches: list[TableMatch]
    note: str = ""


class DescribeTableIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_name: str = Field(min_length=1, max_length=255)
    table_name: str = Field(min_length=1, max_length=255)


class ColumnOut(BaseModel):
    name: str
    data_type: str
    nullable: bool
    is_pk: bool
    policy: str = Field(description="allow | mask | deny — deny means it may not appear at all.")
    semantic_role: str | None = None


class JoinOut(BaseModel):
    from_columns: list[str]
    to_table: str
    to_columns: list[str]


class DescribeTableOut(BaseModel):
    schema_name: str
    table_name: str
    row_estimate: int | None = None
    columns: list[ColumnOut]
    joins: list[JoinOut]
    card: str | None = None


def _require_source(context: ToolContext) -> uuid.UUID:
    """The selected source, or a refusal that says why there is nothing to read.

    Returns the id rather than checking and leaving the caller to re-read the
    attribute, which is what previously needed an ``assert`` to convince the type
    checker of something the code had already established.
    """
    if context.data_source_id is None:
        raise ToolError(
            "No data source is selected for this run, so there is nothing to read.",
            code="no_data_source",
        )
    return context.data_source_id


async def _search_tables(context: ToolContext, params: BaseModel) -> BaseModel:
    args = params if isinstance(params, SearchTablesIn) else SearchTablesIn.model_validate(params)
    hits = await search_cards(
        context.org_id, args.query, data_source_id=context.data_source_id, limit=args.limit
    )
    matches = [
        TableMatch(schema_name=hit.schema_name, table_name=hit.table_name, card=hit.card_text or "")
        for hit in hits
    ]
    # An empty result is an answer, and saying so beats an empty list the model
    # reads as a transport failure and retries. Search is lexical until B-018,
    # so "no match" often means "different words", and the note says that.
    note = (
        ""
        if matches
        else (
            "Nothing matched those words. Search is over the words in each table's "
            "description, so try the names a person would use for the data."
        )
    )
    return SearchTablesOut(matches=matches, note=note)


async def _describe_table(context: ToolContext, params: BaseModel) -> BaseModel:
    args = params if isinstance(params, DescribeTableIn) else DescribeTableIn.model_validate(params)
    data_source_id = _require_source(context)

    try:
        catalog = await active_catalog(context.org_id, data_source_id)
    except (NotFoundError, NoCatalogError) as error:
        raise ToolError(str(error), code="no_catalog") from error

    table = next(
        (
            candidate
            for candidate in catalog.tables
            if candidate.schema_name == args.schema_name and candidate.table_name == args.table_name
        ),
        None,
    )
    if table is None:
        # Repairable, and the message lists what does exist: this is the single
        # commonest model mistake, and a bare "not found" makes the next attempt
        # a guess rather than a correction.
        known = ", ".join(
            f"{candidate.schema_name}.{candidate.table_name}" for candidate in catalog.tables[:20]
        )
        raise ToolError(
            f"There is no table {args.schema_name}.{args.table_name}. Tables here: {known}",
            code="unknown_table",
            repairable=True,
        )

    joins = [
        JoinOut(
            from_columns=list(relationship.from_columns),
            to_table=f"{relationship.to_schema}.{relationship.to_table}",
            to_columns=list(relationship.to_columns),
        )
        for relationship in catalog.relationships
        if relationship.from_schema == table.schema_name
        and relationship.from_table == table.table_name
    ]
    return DescribeTableOut(
        schema_name=table.schema_name,
        table_name=table.table_name,
        row_estimate=table.row_estimate,
        columns=[
            ColumnOut(
                name=column.name,
                data_type=column.data_type,
                nullable=column.nullable,
                is_pk=column.is_pk,
                policy=column.policy,
                semantic_role=column.semantic_role,
            )
            for column in table.columns
        ],
        joins=joins,
        card=table.card_text,
    )


SEARCH_TABLES = Tool(
    name="search_tables",
    description=(
        "Find tables by describing what you are looking for in ordinary words. "
        "Returns each table's description. Reads the catalog, not the database."
    ),
    params=SearchTablesIn,
    handler=_search_tables,
)

DESCRIBE_TABLE = Tool(
    name="describe_table",
    description=(
        "List one table's columns, their types, their policy, and the tables it "
        "joins to. Use it before writing SQL; do not guess column names."
    ),
    params=DescribeTableIn,
    handler=_describe_table,
)
