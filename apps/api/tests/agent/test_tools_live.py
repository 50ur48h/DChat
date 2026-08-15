"""The tools against a real catalog and a real customer database.

The registry's own behaviour is tested with a fake tool; this file is about the
three real ones, and about the two claims that only a real database can support:

* **``run_sql`` is the only tool that touches customer data**, and it inherits
  every DAL property by having no way not to — a hallucinated column comes back
  as a repairable refusal with the violation's own code, and the refusal is on
  ``query_executions`` whether or not anyone retries;
* **the metadata tools never open a socket to the customer's database.**
  Architecture 7.5 refuses ``information_schema`` in the DAL, so if
  ``describe_table`` did not exist the agent could not learn a column name at
  all. That is the trade, and it only holds if these read the catalog.
"""

from __future__ import annotations

import uuid

from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import create_async_engine

from dataagent.agent.tools.base import ToolContext
from dataagent.agent.tools.catalog import DescribeTableOut, SearchTablesOut
from dataagent.agent.tools.registry import default_registry
from dataagent.agent.tools.sql import RunSqlOut
from dataagent.runs.events import EventWriter, read_events

# ---------------------------------------------------------------------------
# Finding and describing, without touching the database
# ---------------------------------------------------------------------------


async def test_search_tables_finds_tables_by_ordinary_words(context: ToolContext) -> None:
    result = await default_registry().call(context, "search_tables", {"query": "shops"})

    assert result.ok
    assert isinstance(result.data, SearchTablesOut)
    assert result.data.matches, "lexical search over cards found nothing at all"
    assert all(match.card for match in result.data.matches)


async def test_a_table_is_findable_by_its_own_name(context: ToolContext) -> None:
    """The opposite of the test this replaces (**B-039**, closed).

    A card used to name its table only as ``public.shops``, and PostgreSQL's
    English parser reads ``word.word`` as a single *host* token — so
    ``to_tsvector('english','public.shops')`` was ``'public.shops'`` while
    ``websearch_to_tsquery('english','shops')`` is ``'shop'``, and the two never
    met. Six of thirteen cards in the demo catalogs were unfindable by their own
    name, ``menu_items`` among them; the rest worked only because the word
    appeared again in their prose.

    Cards now open with the bare name, so this holds for every table rather than
    for the lucky ones.
    """
    for table in ("shops", "regions", "products", "people", "busy_shops"):
        result = await default_registry().call(context, "search_tables", {"query": table})

        assert isinstance(result.data, SearchTablesOut)
        assert table in {match.table_name for match in result.data.matches}, (
            f"{table} cannot be found by its own name"
        )


async def test_the_best_match_for_a_name_is_the_table_with_that_name(
    context: ToolContext,
) -> None:
    """Findable is not enough if it ranks below three tables that merely mention
    it — the agent is given the top few, so position is what decides."""
    result = await default_registry().call(context, "search_tables", {"query": "regions"})

    assert isinstance(result.data, SearchTablesOut)
    assert result.data.matches[0].table_name == "regions"


async def test_a_search_that_matches_nothing_says_so_rather_than_returning_silence(
    context: ToolContext,
) -> None:
    """An empty list reads to a model like a transport failure, and it retries."""
    result = await default_registry().call(
        context, "search_tables", {"query": "cryptocurrency arbitrage"}
    )

    assert result.ok
    assert isinstance(result.data, SearchTablesOut)
    assert result.data.matches == []
    assert result.data.note


async def test_describe_table_returns_columns_joins_and_policy(context: ToolContext) -> None:
    result = await default_registry().call(
        context, "describe_table", {"schema_name": "public", "table_name": "shops"}
    )

    assert result.ok
    assert isinstance(result.data, DescribeTableOut)
    assert [column.name for column in result.data.columns] == [
        "id",
        "region_id",
        "name",
        "opened_on",
    ]
    assert all(column.policy in {"allow", "mask", "deny"} for column in result.data.columns)
    assert result.data.joins[0].to_table == "public.regions"


async def test_describing_a_table_that_does_not_exist_names_the_ones_that_do(
    context: ToolContext,
) -> None:
    """The commonest model mistake. A bare "not found" makes the next attempt a
    guess; naming the real tables makes it a correction."""
    result = await default_registry().call(
        context, "describe_table", {"schema_name": "public", "table_name": "orders"}
    )

    assert result.ok is False
    assert result.code == "unknown_table"
    assert result.repairable is True
    assert "public.shops" in str(result.error)


# ---------------------------------------------------------------------------
# The one tool that reads customer data
# ---------------------------------------------------------------------------


async def test_run_sql_returns_rows_and_the_execution_id_a_citation_needs(
    context: ToolContext,
) -> None:
    result = await default_registry().call(
        context,
        "run_sql",
        {"sql": "SELECT name FROM shops ORDER BY name", "purpose": "list the shops"},
    )

    assert result.ok, result.error
    assert isinstance(result.data, RunSqlOut)
    assert result.data.row_count > 0
    # The id a finding will cite, and it must be a real row rather than a guess.
    assert uuid.UUID(result.data.execution_id)


async def test_the_execution_id_names_a_row_that_is_really_there(
    context: ToolContext, wired: URL
) -> None:
    """Architecture 4.2: findings may only cite real ``query_execution`` rows.
    A citation nobody can resolve is decoration."""
    result = await default_registry().call(
        context, "run_sql", {"sql": "SELECT id FROM shops", "purpose": "ids"}
    )
    assert isinstance(result.data, RunSqlOut)

    engine = create_async_engine(wired)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, false)"),
                {"org": str(context.org_id)},
            )
            row = (
                await connection.execute(
                    text("SELECT status, run_id FROM query_executions WHERE id = :id"),
                    {"id": result.data.execution_id},
                )
            ).one()
    finally:
        await engine.dispose()

    assert row.status == "ok"
    assert row.run_id == context.run_id, "the execution was not attributed to this run"


async def test_a_hallucinated_column_is_a_repairable_refusal_carrying_its_code(
    context: ToolContext,
) -> None:
    """The path the single repair attempt exists for (WP7.2b). It comes back as
    an envelope rather than an exception, and it names what was wrong."""
    result = await default_registry().call(
        context,
        "run_sql",
        {"sql": "SELECT revenue_total FROM shops", "purpose": "invent a column"},
    )

    assert result.ok is False
    assert result.repairable is True
    assert result.code == "unknown_column"
    assert "revenue_total" in str(result.error)


async def test_a_refused_statement_is_recorded_before_anyone_decides_to_retry(
    context: ToolContext, wired: URL
) -> None:
    """The row WP5.2b exists for: a statement that reached no engine is visible
    nowhere else, and whether the agent retries is not the audit trail's business."""
    await default_registry().call(
        context, "run_sql", {"sql": "DROP TABLE shops", "purpose": "should never run"}
    )

    engine = create_async_engine(wired)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, false)"),
                {"org": str(context.org_id)},
            )
            rows = (
                await connection.execute(
                    text(
                        "SELECT status, violation_code FROM query_executions "
                        "WHERE run_id = :run AND status = 'refused'"
                    ),
                    {"run": context.run_id},
                )
            ).all()
    finally:
        await engine.dispose()

    assert len(rows) == 1
    assert rows[0].violation_code


async def test_a_system_schema_is_refused_so_the_catalog_stays_the_only_way_in(
    context: ToolContext,
) -> None:
    """Which is exactly why ``describe_table`` has to exist."""
    result = await default_registry().call(
        context,
        "run_sql",
        {"sql": "SELECT tablename FROM pg_catalog.pg_tables", "purpose": "enumerate"},
    )

    assert result.ok is False
    assert result.code == "system_schema"


async def test_run_sql_without_a_selected_source_refuses_rather_than_guessing(
    context: ToolContext,
) -> None:
    from dataclasses import replace

    result = await default_registry().call(
        replace(context, data_source_id=None),
        "run_sql",
        {"sql": "SELECT 1", "purpose": "nowhere to send this"},
    )

    assert result.ok is False
    assert result.code == "no_data_source"


# ---------------------------------------------------------------------------
# What the trace sees
# ---------------------------------------------------------------------------


async def test_a_tool_call_is_announced_in_the_trace_before_it_runs(
    context: ToolContext,
) -> None:
    """Emitted before dispatch, so a call that hangs or crashes is still visible
    as something that was attempted rather than something that never happened."""
    writer = EventWriter(org_id=context.org_id, run_id=context.run_id)

    await default_registry().call(
        context,
        "run_sql",
        {"sql": "SELECT id FROM shops", "purpose": "count them"},
        events=writer,
    )

    events = await read_events(org_id=context.org_id, run_id=context.run_id)
    assert [event.type for event in events] == ["tool_called"]
    assert events[0].payload["tool"] == "run_sql"
    assert str(events[0].payload["safe_args"]).count("count them") == 1


async def test_a_refusal_reaches_the_trace_as_an_error_with_a_safe_message(
    context: ToolContext,
) -> None:
    writer = EventWriter(org_id=context.org_id, run_id=context.run_id)

    await default_registry().call(
        context,
        "run_sql",
        {"sql": "SELECT revenue_total FROM shops", "purpose": "invent a column"},
        events=writer,
    )

    events = await read_events(org_id=context.org_id, run_id=context.run_id)
    assert [event.type for event in events] == ["tool_called", "error"]
    assert events[1].payload["category"] == "unknown_column"
    assert "revenue_total" in str(events[1].payload["safe_message"])


async def test_an_unknown_tool_is_recorded_without_pretending_it_was_called(
    context: ToolContext,
) -> None:
    """No `tool_called` for something that was never dispatched — a trace that
    said otherwise would be describing a call that did not happen."""
    writer = EventWriter(org_id=context.org_id, run_id=context.run_id)

    await default_registry().call(context, "delete_everything", {}, events=writer)

    events = await read_events(org_id=context.org_id, run_id=context.run_id)
    assert [event.type for event in events] == ["error"]
    assert events[0].payload["category"] == "unknown_tool"
