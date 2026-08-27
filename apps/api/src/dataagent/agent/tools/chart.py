"""The chart tool: a picture of a result this run already has (WP11.1, B-048).

Architecture Part 3 removed the code sandbox from V1 and replaced it with
**spec-based charts** — the agent emits a validated Vega-Lite spec, the browser
renders it, and nothing executes server-side. This is the tool the model calls;
`agent/charts.py` is the judgement it delegates to.

**It charts a result, never a claim.** The only thing it accepts is an
`execution_id` this run produced, and the values come from the stored artifact —
already masked by the DAL on the way in. So a chart cannot show a number no query
returned, which is the same rule citations follow and for the same reason: a
picture nobody can trace back to a query is decoration that looks like evidence
(**B-048**).

**Another run's execution is not chartable.** The lookup is scoped to this
organization by row-level security and to this run by `run_id`, so a model that
names an id from somewhere else gets a refusal rather than somebody else's data
in a picture.

**A refusal is a result, not an error.** `ToolError` is for a tool that could not
do its job; declining to draw is the tool doing exactly its job. The model sees
the reason and may ask for a different chart, and the reader is told why there is
no picture — which is the whole point of `charts.decide` returning a sentence
rather than None.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field
from sqlalchemy import select

from dataagent.agent.charts import Chart, ChartRequest, Frame, decide
from dataagent.agent.shape import shape_of
from dataagent.agent.tools.base import Tool, ToolContext, ToolError
from dataagent.dal.artifacts import artifact_store
from dataagent.db.models import QueryExecution, ResultArtifact
from dataagent.tenancy.session import org_session

__all__ = ["CREATE_CHART_SPEC", "CreateChartIn", "CreateChartOut"]


class CreateChartIn(BaseModel):
    """Which result to draw, and how."""

    execution_id: str = Field(
        description="A query this run already ran. The chart shows those rows and no others."
    )
    mark: str = Field(description="bar | line | point | area")
    x: str = Field(description="Column for the horizontal axis, as the result names it")
    y: str = Field(description="Column for the vertical axis. Must hold numbers.")
    series: str | None = Field(
        default=None, description="Optional column to split the marks by colour"
    )
    title: str | None = Field(default=None, max_length=200)


class CreateChartOut(BaseModel):
    """A spec to render, or the reason there is none. Exactly one is set.

    The reason is written for a reader rather than for the model: it is the
    sentence the answer card shows where the chart would have been, so a picture
    that does not appear never looks like a broken page.
    """

    spec: dict[str, object] | None = None
    declined: str | None = None
    code: str | None = None

    @classmethod
    def of(cls, chart: Chart) -> CreateChartOut:
        return cls(spec=chart.spec, declined=chart.declined, code=chart.code)


async def _frame_for(
    *, org_id: uuid.UUID, run_id: uuid.UUID, execution_id: uuid.UUID
) -> Frame | None:
    """The stored result for one of this run's executions, or None.

    None covers every way a result can be absent — an id from another run, an
    artifact whose retention has passed, a query that never stored one — because
    the caller says the same thing to a reader in all three cases and a picture
    is not the place to explain a retention policy.
    """
    async with org_session(org_id) as session:
        row = (
            await session.execute(
                select(ResultArtifact, QueryExecution)
                .join(QueryExecution, QueryExecution.id == ResultArtifact.query_execution_id)
                .where(
                    ResultArtifact.query_execution_id == execution_id,
                    QueryExecution.run_id == run_id,
                )
            )
        ).one_or_none()
        if row is None:
            return None
        artifact, _ = row
        reference = artifact.storage_ref
        truncated = artifact.truncated

    if not reference:
        return None
    payload = await artifact_store().get(org_id=org_id, reference=reference)
    if payload is None:
        return None

    stored = json.loads(payload)
    columns = tuple(str(name) for name in stored.get("columns", []))
    rows = tuple(tuple(row) for row in stored.get("rows", []))
    # **B-103.** A `Decimal` cannot be a JSON number without either a raw-text
    # injection the standard library will not do or a `float` that rounds a
    # customer's money, so it is stored as a string — and the writer records
    # what the column held. Rebuilding the Decimal from that string is exact at
    # any size and precision, and it is *reading a declaration* rather than
    # deciding that text which looks numeric is a number, which would turn a
    # postcode into a measure.
    #
    # Absent for anything written before the type was recorded. Those stay
    # strings and the chart declines — see `_stale_numeric` for why that is the
    # honest outcome rather than an unlucky one.
    types = tuple(str(kind) for kind in stored.get("column_types", ()))
    if types:
        rows = tuple(
            tuple(
                _as_number(value) if index < len(types) and types[index] == "number" else value
                for index, value in enumerate(row)
            )
            for row in rows
        )
    return Frame(columns=columns, rows=rows, truncated=truncated, column_types=types)


def _as_number(value: object) -> object:
    """A stored numeric cell, back as a number. Exact, or left alone.

    `Decimal` rather than `float`: this is money as often as not, and the whole
    reason the value travelled as a string is that rounding it was unacceptable.
    A value that will not parse is returned untouched rather than dropped — the
    column's type says a number was intended, and a cell that is not one is a
    fact the chart layer should see rather than a `None` it cannot explain.
    """
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation:
            return value
    return value


async def _create_chart_spec(context: ToolContext, params: BaseModel) -> BaseModel:
    args = params if isinstance(params, CreateChartIn) else CreateChartIn.model_validate(params)

    try:
        execution_id = uuid.UUID(args.execution_id)
    except ValueError:
        # Not a refusal about the data: the model named something that is not an
        # execution id at all, and telling it so is what lets it correct itself.
        raise ToolError(
            f"{args.execution_id!r} is not a query id from this run.",
            code="no_such_execution",
            repairable=True,
        ) from None

    frame = await _frame_for(
        org_id=context.org_id, run_id=context.run_id, execution_id=execution_id
    )
    if frame is None:
        return CreateChartOut(
            declined=(
                "No chart was drawn: the rows behind that query are not available to draw "
                "from. Charts are built from a query this run ran and still holds."
            ),
            code="no_result",
        )

    # **The platform chooses the form; the model chose the result** (WP13.20).
    # `shape_of` reads the frame that actually came back and says what it should
    # be drawn as — a date against a number is a line, a category against a
    # number is a bar, and a spare category becomes a *series* rather than a
    # second answer. Decided here because this is where the frame is already
    # loaded, and reading it twice for a picture would be a round trip for
    # nothing.
    #
    # **Overridden, not removed.** `ChartAsk` keeps its fields: narrowing a
    # schema that is `extra="forbid"` is D-044's trap, and the cost of leaving
    # them is a few tokens the model spends being ignored.
    shape = shape_of(frame)
    if not shape.draws:
        return CreateChartOut(
            declined=f"No chart was drawn: {shape.reason}.",
            code="unchartable_shape",
        )
    return CreateChartOut.of(
        decide(
            frame,
            ChartRequest(
                mark=shape.mark,
                x=shape.x,
                y=shape.y,
                series=shape.series or None,
                title=args.title,
            ),
        )
    )


CREATE_CHART_SPEC = Tool(
    name="create_chart_spec",
    description=(
        "Draw a chart of a result this run already produced. Give the query's "
        "execution id and the columns for each axis. Returns a spec the browser "
        "renders, or a plain reason the data cannot support that chart — a "
        "category with thousands of values, a measure that holds words, or an "
        "axis of dates that are not dates."
    ),
    params=CreateChartIn,
    handler=_create_chart_spec,
    # It reads a result this run already paid for and touches no customer
    # database, so it costs a step rather than a query.
    budget_cost=1,
)
