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

from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from dataagent.agent.tools.base import MAX_RENDERED_CHARS, Tool, ToolContext, ToolError
from dataagent.catalog.browse import NoCatalogError
from dataagent.connectors.base import ConnectorError
from dataagent.dal import run as dal_run
from dataagent.dal.artifacts import encodable
from dataagent.dal.errors import PolicyViolation
from dataagent.dal.masking import MaskedFrame
from dataagent.dal.service import Execution
from dataagent.datasources.service import NotFoundError

#: `preview_rows` is public because it is the rule, not an implementation
#: detail: how much of a result reaches a model decides what that model can
#: answer, and B-113 is what happens when it is wrong. It is asserted
#: directly rather than inferred from a tool call.
__all__ = ["PREVIEW_CHARS", "PREVIEW_ROWS_MIN", "RUN_SQL", "preview_rows"]

#: What a preview may cost the prompt, measured on the payload that is actually
#: rendered (**B-113**).
#:
#: **A budget by shape, because characters are the resource being spent.** This
#: was `PREVIEW_ROWS = 20` — a fixed count, chosen for a reason that is still
#: true: the DAL's cap is far higher because the artifact keeps the whole result,
#: and what reaches a prompt has to stay small enough that raw values do not
#: become the context (architecture 4.4, *summaries flow forward*). A row count
#: is simply the wrong unit for that rule. Twenty rows of three short columns
#: cost a fifth of twenty rows of twelve wide ones, so a fixed count either
#: starves the narrow case or floods the wide one — and it starved the narrow
#: case: eighteen months of revenue by channel is 54 rows the model never saw,
#: and a question the data answered in full came back refused.
#:
#: **Derived from `MAX_RENDERED_CHARS`, not chosen.** That is the ceiling
#: `ToolResult.render` cuts at, and the margin below it is the frame it wraps
#: this payload in. Deriving rather than picking a number buys a property worth
#: having: a preview the budget governs cannot be truncated by `render`, so it
#: never hits **B-112**'s unannounced cut.
#:
#: **That holds everywhere the budget wins, and the floor below can beat it.** A
#: result so wide that `PREVIEW_ROWS_MIN` rows exceed the ceiling is rendered
#: anyway and cut by `render` — measured at 18,297 characters for four rows of
#: 6,000. So this narrows B-112's exposure to the widest results and does not
#: close it, which is the honest claim: B-112 stays open, still with no flag when
#: a cut happens, and that case is now the one place this tool can reach it.
#:
#: Deliberately **no row ceiling on top**. A second limit would be a second
#: cliff chosen from nothing, and the reason a bigger constant was refused is
#: that it moves the cliff rather than removing it. This one corresponds to a
#: real resource, which is what makes it defensible.
PREVIEW_CHARS = MAX_RENDERED_CHARS - 100

#: The fewest rows worth showing, whatever they cost. A result so wide that one
#: row exceeds the budget still has to arrive as *something*: a model shown zero
#: rows and a row count cannot tell an empty result from an expensive one.
#:
#: This is the one place the budget above does not hold, and it is a deliberate
#: trade rather than an oversight — three wide rows cut by `render` is a better
#: failure than no rows at all, because the second invites the model to invent
#: what the result contained and the first does not.
PREVIEW_ROWS_MIN = 3


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
        # address or a credential.
        #
        # **Repairable exactly when the statement was at fault.** This used to
        # be unconditionally false, and the comment said "not repairable by
        # rewriting the SQL" — true of a database that cannot be reached, false
        # of one that rejected the SQL and said why. A live run ended after a
        # single query on `function round(double precision, integer) does not
        # exist`, whose own HINT was *"You might need to add explicit type
        # casts"*: the fix was in the error message and the loop was told not to
        # try. The connector decides, because the evidence is dialect-specific.
        raise ToolError(
            str(error), code="engine_error", repairable=error.statement_fault
        ) from error

    frame = execution.frame

    def rendered(candidate: list[list[object]]) -> str:
        """The payload those rows would produce, exactly as the model sees it."""
        return _out(execution, frame, candidate).model_dump_json(indent=2)

    shown = preview_rows(frame.rows, rendered)
    return _out(execution, frame, shown)


def _out(execution: Execution, frame: MaskedFrame, rows: list[list[object]]) -> RunSqlOut:
    """One payload, built once, so the measurement and the result cannot differ.

    The budget is only meaningful if what it measured is what gets sent. Two
    constructions that drifted would put the check on one object and the model in
    front of another — which is the shape of defect this file has now produced
    twice, and is worth one small function to make impossible.
    """
    return RunSqlOut(
        execution_id=str(execution.execution_id) if execution.execution_id else "",
        columns=list(frame.columns),
        rows=rows,
        row_count=execution.row_count,
        # True when the DAL capped the result *or* when the budget did. The model
        # is owed the fact that it is holding part of something, not which of our
        # two limits produced it.
        truncated=execution.truncated or len(rows) < len(frame.rows),
        masked_columns=list(frame.masked_columns),
        duration_ms=execution.duration_ms,
        # From the validator rather than from the SQL text: it resolved every
        # name against the catalog, so this is what was *read*, whatever alias
        # or casing the model wrote (B-093).
        tables=[str(table) for table in execution.validated.tables],
    )


def preview_rows(
    rows: Sequence[Sequence[object]],
    render: Callable[[list[list[object]]], str],
) -> list[list[object]]:
    """As many rows as the budget pays for, measured on what will be sent.

    **`render` is passed in so the measurement is the payload itself.** The first
    version of this counted `json.dumps(row)` compactly, which is a different
    number from what `model_dump_json(indent=2)` produces — 1,944 characters
    against 3,566 for the same 54 rows, an under-count of 1.8 times. Budgeting against
    a proxy for the thing you are protecting is how a limit ends up not limiting;
    the same mistake, measured rather than assumed, is what **B-109**'s entry
    records under *a measurement built on a reconstruction*.

    Rows are added whole and dropped from the end. A result sliced through the
    middle of a row is one the model has to parse around, which is exactly what
    the character budget exists to stop happening arbitrarily further down.
    """
    encoded = [[encodable(value) for value in row] for row in rows]
    if not encoded or len(render(encoded)) <= PREVIEW_CHARS:
        return encoded

    # Proportional first guess, then step down. Two or three renders rather than
    # one per row, and the floor is the only thing that stops it reaching zero.
    over = len(render(encoded))
    keep = max(PREVIEW_ROWS_MIN, len(encoded) * PREVIEW_CHARS // over)
    while keep > PREVIEW_ROWS_MIN and len(render(encoded[:keep])) > PREVIEW_CHARS:
        keep = max(PREVIEW_ROWS_MIN, keep * 4 // 5)
    return encoded[:keep]


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
