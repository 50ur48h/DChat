"""Importing definitions a database already carries (**B-059**, WP10.2d).

The F&B trial is why this exists: that warehouse arrived with eighteen metrics
already written down, every one of them invisible to the agent, and the run that
should have used them answered **0 units** with correct SQL. A product that only
accepts definitions retyped will mostly be given none.

What these tests hold is the part that could quietly go wrong. An import reads a
customer's table, so it must go **through the DAL** like every other read of
customer data — not through a connector this module opened for itself. And an
imported definition **constrains generated SQL**, so it must arrive as a proposal
that binds nothing until an Admin has looked at it: a crawler that could write an
active definition would be a path by which a customer's own metadata table
decided what the platform enforces.

The fixture's `metric_book` table stands in for `meta_metric`. It is created here
rather than in `customer_db.py` because it is this WP's shape, and the point of
the mapping is that nothing guesses which table is authoritative.
"""

from __future__ import annotations

import pytest
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import create_async_engine

from customer_db import CustomerDatabase
from dataagent.agent.tools.base import ToolContext
from dataagent.catalog import cards, discovery
from dataagent.semantic import definitions as semantic
from dataagent.semantic import proposals
from dataagent.semantic.definitions import DefinitionError

BOOK = "metric_book"


async def _with_metric_table(customer: CustomerDatabase, context: ToolContext) -> None:
    """Give the customer's database a metric table, and re-catalog it.

    Re-catalogued because the DAL grounds every statement against the catalog:
    a table the crawler has never seen is a table the import cannot read, which
    is the same protection every other query gets and is worth exercising here.
    """
    engine = create_async_engine(customer.url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {BOOK} ("
                    "  metric_key text PRIMARY KEY,"
                    "  definition_text text,"
                    "  formula text,"
                    "  also_called text)"
                )
            )
            await connection.execute(text(f"DELETE FROM {BOOK}"))
            await connection.execute(
                text(
                    f"INSERT INTO {BOOK} (metric_key, definition_text, formula, also_called) "
                    "VALUES "
                    "('stock_value', 'Total price of everything we list, excluding samples.',"
                    " 'sum(products.price)', 'stock value, listed value'),"
                    "('shop_count', 'How many trading shops we have.',"
                    " 'count(shops.id)', 'shops open'),"
                    # A blank line in a metric table is ordinary. It must not
                    # fail the import, and it must not become a definition.
                    "('', '', '', '')"
                )
            )
            await connection.execute(text(f"GRANT SELECT ON {BOOK} TO {customer.reader_username}"))
    finally:
        await engine.dispose()

    source_id = context.data_source_id
    assert source_id is not None
    await discovery.discover(
        org_id=context.org_id, actor_user_id=context.actor_user_id, data_source_id=source_id
    )
    await cards.refresh_cards(context.org_id, source_id)


def _mapping() -> proposals.ColumnMapping:
    return proposals.ColumnMapping(
        name="metric_key",
        description="definition_text",
        expression="formula",
        synonyms="also_called",
    )


async def _import(context: ToolContext) -> tuple[proposals.Proposal, ...]:
    source_id = context.data_source_id
    assert source_id is not None
    return await proposals.propose_from_table(
        org_id=context.org_id,
        data_source_id=source_id,
        table=BOOK,
        mapping=_mapping(),
        actor_user_id=context.actor_user_id,
    )


# ---------------------------------------------------------------------------
# What an import produces
# ---------------------------------------------------------------------------


async def test_a_metric_table_becomes_proposals(
    context: ToolContext, wired: URL, isolated_customer_database: CustomerDatabase
) -> None:
    await _with_metric_table(isolated_customer_database, context)

    created = await _import(context)

    assert {proposal.name for proposal in created} == {"stock_value", "shop_count"}
    stock = next(item for item in created if item.name == "stock_value")
    assert "excluding samples" in stock.description
    assert stock.expression == "sum(products.price)"
    assert "listed value" in stock.synonyms


async def test_a_row_missing_a_name_or_a_definition_is_not_one(
    context: ToolContext, wired: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """A blank line in a metric table is ordinary. Failing the whole import over
    it would make the feature unusable on the databases it exists for; importing
    it would put a nameless definition in front of an Admin."""
    await _with_metric_table(isolated_customer_database, context)

    created = await _import(context)

    assert all(proposal.name and proposal.description for proposal in created)
    assert len(created) == 2


async def test_nothing_imported_binds_until_an_admin_accepts_it(
    context: ToolContext, wired: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """**The property this whole design turns on.** An imported definition
    constrains generated SQL, so a crawler that could write an active one would
    let a customer's own metadata table decide what the platform enforces."""
    await _with_metric_table(isolated_customer_database, context)
    source_id = context.data_source_id
    assert source_id is not None

    created = await _import(context)

    assert await semantic.definitions_for(context.org_id, source_id) == ()
    assert {p.name for p in await proposals.proposals_for(context.org_id, source_id)} == {
        p.name for p in created
    }


async def test_provenance_records_where_it_came_from(
    context: ToolContext, wired: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """So that when the customer's own table moves on, the drift is visible
    rather than silently stale."""
    await _with_metric_table(isolated_customer_database, context)

    created = await _import(context)

    provenance = created[0].provenance
    assert provenance["kind"] == "import"
    assert provenance["table"] == f"public.{BOOK}"
    assert provenance["columns"] == {
        "name": "metric_key",
        "description": "definition_text",
        "expression": "formula",
    }


async def test_the_read_goes_through_the_dal_and_is_recorded(
    context: ToolContext,
    wired: URL,
    app_database: URL,
    isolated_customer_database: CustomerDatabase,
) -> None:
    """An import is a read of customer data and is recorded as one. Asserted on
    `query_executions` rather than on a mock, because what matters is that the
    row exists for somebody auditing what this platform read."""
    await _with_metric_table(isolated_customer_database, context)

    await _import(context)

    engine = create_async_engine(app_database)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, false)"), {"org": str(context.org_id)}
            )
            reads = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM query_executions "
                        "WHERE sql_text ILIKE '%metric_book%' AND status = 'ok'"
                    )
                )
            ).scalar_one()
    finally:
        await engine.dispose()

    assert reads >= 1, "the import did not leave a query_executions row"


async def test_a_table_the_catalog_has_never_seen_cannot_be_imported(
    context: ToolContext, wired: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """The DAL's grounding, reached through this path. An import that could read
    an uncatalogued table would be a way around the check every other query
    gets."""
    from dataagent.dal.errors import PolicyViolation

    source_id = context.data_source_id
    assert source_id is not None

    with pytest.raises(PolicyViolation):
        await proposals.propose_from_table(
            org_id=context.org_id,
            data_source_id=source_id,
            table="not_a_real_table",
            mapping=_mapping(),
        )


async def test_an_identifier_that_is_not_a_bare_name_is_refused(
    context: ToolContext, wired: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """The cheap half of 5.10's two layers. The DAL would refuse this too, but
    building SQL by concatenation and relying on a downstream check is how the
    downstream check eventually gets moved — and the message here names the
    mistake instead of reporting a policy violation about a statement the Admin
    did not write."""
    source_id = context.data_source_id
    assert source_id is not None

    with pytest.raises(ValueError, match="not a valid table name"):
        await proposals.propose_from_table(
            org_id=context.org_id,
            data_source_id=source_id,
            table="products; DROP TABLE products",
            mapping=_mapping(),
        )


# ---------------------------------------------------------------------------
# The Admin's decision
# ---------------------------------------------------------------------------


async def test_accepting_a_proposal_makes_it_bind(
    context: ToolContext, wired: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """**Where prose becomes a constraint** (D-033). The imported sentences
    informed the model and bound nothing; the filters an Admin adds here are what
    the critic can enforce."""
    await _with_metric_table(isolated_customer_database, context)
    source_id = context.data_source_id
    assert source_id is not None
    created = await _import(context)
    stock = next(item for item in created if item.name == "stock_value")

    accepted = await proposals.accept(
        org_id=context.org_id,
        definition_id=stock.id,
        required_filters=[
            {"table": "products", "column": "name", "op": "not_in", "values": ["sample"]}
        ],
        actor_user_id=context.actor_user_id,
    )

    assert accepted.required_filters[0].column == "name"
    active = await semantic.definitions_for(context.org_id, source_id)
    assert [item.name for item in active] == ["stock_value"]
    assert active[0].required_filters[0].values == ("sample",)


async def test_a_filter_naming_a_column_that_does_not_exist_is_refused(
    context: ToolContext, wired: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """Refused at acceptance rather than during somebody's run, where it would
    arrive as a critic finding nobody could act on."""
    await _with_metric_table(isolated_customer_database, context)
    created = await _import(context)

    with pytest.raises(DefinitionError, match="no column called"):
        await proposals.accept(
            org_id=context.org_id,
            definition_id=created[0].id,
            required_filters=[
                {"table": "products", "column": "nonexistent", "op": "eq", "values": ["x"]}
            ],
        )


async def test_a_rejected_proposal_is_not_offered_again(
    context: ToolContext, wired: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """Retired rather than deleted, so "we looked at this and said no" is
    answerable — and so a second import does not re-propose it."""
    await _with_metric_table(isolated_customer_database, context)
    source_id = context.data_source_id
    assert source_id is not None
    created = await _import(context)

    await proposals.reject(org_id=context.org_id, definition_id=created[0].id)
    again = await _import(context)

    waiting = {item.name for item in await proposals.proposals_for(context.org_id, source_id)}
    assert created[0].name not in waiting
    assert created[0].name not in {item.name for item in again}


async def test_importing_twice_does_not_duplicate_a_definition(
    context: ToolContext, wired: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """`(data_source_id, name)` is unique, so a second import must skip rather
    than collide — and it must not overwrite a definition an Admin has already
    blessed with filters."""
    await _with_metric_table(isolated_customer_database, context)
    source_id = context.data_source_id
    assert source_id is not None
    first = await _import(context)
    await proposals.accept(
        org_id=context.org_id,
        definition_id=next(i for i in first if i.name == "stock_value").id,
        required_filters=[
            {"table": "products", "column": "name", "op": "not_in", "values": ["sample"]}
        ],
    )

    second = await _import(context)

    assert second == ()
    active = await semantic.definitions_for(context.org_id, source_id)
    assert len(active[0].required_filters) == 1, "a re-import flattened a blessed definition"
