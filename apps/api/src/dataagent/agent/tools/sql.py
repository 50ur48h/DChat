"""``run_sql`` — the only tool that touches customer data (architecture 4.6, 7.1).

It is deliberately thin, and the thinness is the design. Everything that decides
whether a statement may run, what it may read, how much comes back and what is
recorded lives in ``dal.run``; this module turns a model's argument into that
call and turns the outcome into an envelope. There is no parameter here that
softens any of it — no "skip validation", no "raw", no second path.

Two consequences worth stating.

**A refusal is a normal outcome, and it is repairable.** The DAL refuses a
statement it cannot ground in the catalog, which is exactly what a hallucinated
column produces. That comes back as a `ToolResult` carrying the violation code
and message rather than as an exception, because the next thing that should
happen is one corrected attempt (WP7.2b) — and the refusal has already been
recorded in ``query_executions`` whether or not anybody retries.

**The model never sees more than the DAL returned.** Rows arrive already masked
and already capped; this renders a bounded preview of them plus the shape. The
full result stays in the artifact store, addressed by the execution id that the
citation will point at.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dataagent.agent.tools.base import Tool, ToolContext, ToolError
from dataagent.catalog.browse import NoCatalogError
from dataagent.connectors.base import ConnectorError
from dataagent.dal import run as dal_run
from dataagent.dal.errors import PolicyViolation
from dataagent.datasources.service import NotFoundError

__all__ = ["RUN_SQL"]

#: Rows put in front of the model. The DAL's own cap is far higher because the
#: artifact keeps the whole result; what reaches a prompt is a sample big enough
#: to reason about and small enough that raw values do not become the context.
PREVIEW_ROWS = 20


class RunSqlIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str = Field(
        min_length=1,
        max_length=20_000,
        description=(
            "One SELECT statement, using only tables and columns from the catalog. "
            "No semicolons beyond the single statement, no DDL, no system schemas."
        ),
    )
    purpose: str = Field(
        min_length=1,
        max_length=300,
        description="One line on what this query is for. It appears in the run's trace.",
    )


class RunSqlOut(BaseModel):
    """What came back, and the id a citation will name."""

    execution_id: str
    columns: list[str]
    rows: list[list[object]] = Field(
        description="Masked and capped. A preview, not the whole result."
    )
    row_count: int
    truncated: bool = False
    masked_columns: list[str] = Field(
        default_factory=list[str],
        description="Columns whose values were obscured by policy before you saw them.",
    )
    duration_ms: int | None = None
    tables: list[str] = Field(
        default_factory=list[str],
        description=(
            "The tables this statement read, as the validator resolved them "
            "against the catalog — not as the model spelled them (B-093)."
        ),
    )


async def _run_sql(context: ToolContext, params: BaseModel) -> BaseModel:
    args = params if isinstance(params, RunSqlIn) else RunSqlIn.model_validate(params)
    if context.data_source_id is None:
        raise ToolError(
            "No data source is selected for this run, so there is nothing to query.",
            code="no_data_source",
        )

    try:
        execution = await dal_run(
            org_id=context.org_id,
            data_source_id=context.data_source_id,
            sql=args.sql,
            actor_user_id=context.actor_user_id,
            run_id=context.run_id,
        )
    except PolicyViolation as violation:
        # The refusal the whole design is for. Repairable: the message names the
        # identifier or the rule, which is what one corrected attempt needs.
        raise ToolError(str(violation), code=str(violation.code), repairable=True) from violation
    except (NotFoundError, NoCatalogError) as error:
        raise ToolError(str(error), code="no_catalog") from error
    except ConnectorError as error:
        # Already sanitized by the connector: names what failed, never an
        # address or a credential. Not repairable by rewriting the SQL.
        raise ToolError(str(error), code="engine_error") from error

    frame = execution.frame
    return RunSqlOut(
        execution_id=str(execution.execution_id) if execution.execution_id else "",
        columns=list(frame.columns),
        rows=[[_plain(value) for value in row] for row in frame.rows[:PREVIEW_ROWS]],
        row_count=execution.row_count,
        truncated=execution.truncated or len(frame.rows) > PREVIEW_ROWS,
        masked_columns=list(frame.masked_columns),
        duration_ms=execution.duration_ms,
        # From the validator rather than from the SQL text: it resolved every
        # name against the catalog, so this is what was *read*, whatever alias
        # or casing the model wrote (B-093).
        tables=[str(table) for table in execution.validated.tables],
    )


def _plain(value: object) -> object:
    """JSON-safe, and a string when in doubt.

    Dates and decimals are the common case; the model reads them as text either
    way, and a repr that pydantic cannot serialise would fail at the envelope
    rather than here.
    """
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


RUN_SQL = Tool(
    name="run_sql",
    description=(
        "Run one read-only SELECT against the selected data source and get back "
        "the rows. Every statement is checked against the catalog and the column "
        "policy first; anything ungrounded is refused with a reason."
    ),
    params=RunSqlIn,
    handler=_run_sql,
    budget_cost=1,
)
