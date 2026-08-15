"""What a card says, and whether the right one comes back.

The card is the text a language model will be handed instead of a schema, so
these tests are mostly about *content*: that it names the joins, that it says
when there are none, that it never quotes a value that was masked, and that a
person searching in ordinary words finds the table they meant.

``test_searching_revenue_returns_the_orders_card_first`` is the Phase 4 gate
criterion, asserted here against a fixture so that the browser demo is a
confirmation rather than the only evidence.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import customer_db
from customer_db import CustomerDatabase
from dataagent.catalog import browse, cards, discovery, policies, profiler, search
from dataagent.datasources import service as datasources
from dataagent.db import engine as engine_module
from dataagent.secrets.local import LocalSecretsProvider
from dataagent.tenancy import session as session_module


@pytest.fixture
async def platform(
    app_database: URL,
    migrated_database: URL,
    secrets_provider: LocalSecretsProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[URL]:
    owner = create_async_engine(migrated_database)
    app_engine = create_async_engine(app_database)
    monkeypatch.setattr(engine_module, "get_engine", lambda: owner)
    monkeypatch.setattr(
        session_module,
        "_session_factory",
        lambda: async_sessionmaker(app_engine, expire_on_commit=False, autoflush=False),
    )
    try:
        yield app_database
    finally:
        await owner.dispose()
        await app_engine.dispose()


async def _catalogued(
    migrated_database: URL, customer: CustomerDatabase, *, profile: bool = True
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    engine = create_async_engine(migrated_database)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            await connection.execute(
                text("INSERT INTO organizations (id, name) VALUES (:id, 'Cards')"), {"id": org_id}
            )
            await connection.execute(
                text("INSERT INTO users (id, external_subject, email) VALUES (:i, :s, :e)"),
                {"i": user_id, "s": f"sub-{user_id}", "e": "owner@example.com"},
            )
            await connection.execute(
                text(
                    "INSERT INTO org_memberships (org_id, user_id, role) VALUES (:o, :u, 'admin')"
                ),
                {"o": org_id, "u": user_id},
            )
    finally:
        await engine.dispose()

    view = await datasources.create_data_source(
        org_id=org_id,
        actor_user_id=user_id,
        name="Customer",
        engine="pg",
        host=customer.host,
        port=customer.port,
        database=customer.database,
        username=customer.reader_username,
        password=customer.reader_password,
        tls_mode="prefer",
    )
    await datasources.test_data_source(org_id=org_id, actor_user_id=user_id, data_source_id=view.id)
    await discovery.discover(org_id=org_id, actor_user_id=user_id, data_source_id=view.id)
    if profile:
        await profiler.profile(org_id=org_id, actor_user_id=user_id, data_source_id=view.id)
    return org_id, user_id, view.id


async def _card(org_id: uuid.UUID, source_id: uuid.UUID, table: str) -> str:
    catalog = await browse.active_catalog(org_id, source_id)
    found = next(entry for entry in catalog.tables if entry.table_name == table)
    assert found.card_text is not None
    return found.card_text


# ---------------------------------------------------------------------------
# What a card says
# ---------------------------------------------------------------------------


async def test_a_card_describes_the_table_in_words(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, _, source_id = await _catalogued(migrated_database, isolated_customer_database)

    card = await _card(org_id, source_id, "shops")

    # The bare name first, then the qualified one (B-039). A card that named its
    # table only as `public.shops` could not be found by searching for "shops".
    assert card.startswith("shops (public.shops) is a table")
    assert "Columns (4):" in card
    assert "- name (text, required)" in card
    assert "Trading name." in card, "a column comment is the best sentence in a card"
    assert "region_id references regions(id)" in card


async def test_a_card_names_the_tables_that_point_at_it(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, _, source_id = await _catalogued(migrated_database, isolated_customer_database)

    card = await _card(org_id, source_id, "regions")

    assert "shops(region_id) references this table(id)" in card


async def test_a_card_says_plainly_when_a_table_joins_to_nothing(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """Phase 8's honest refusal is built on this sentence existing.

    An agent asked "which products sell best" has to be able to read that the
    join it wants is absent, rather than infer it from an empty section.
    """
    org_id, _, source_id = await _catalogued(migrated_database, isolated_customer_database)

    card = await _card(org_id, source_id, "products")

    assert "No foreign keys connect this table to any other" in card
    assert "cannot be joined" in card


async def test_a_card_never_quotes_a_value_that_was_masked(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """The card is built from catalog rows, which were masked on the way in — so
    this is a property of the pipeline, checked at its last stop."""
    org_id, _, source_id = await _catalogued(migrated_database, isolated_customer_database)

    card = await _card(org_id, source_id, "people")

    assert "ada@example.com" not in card
    assert "ada.lovelace@example.org" not in card
    assert "values are masked" in card
    assert "Personal or otherwise sensitive: email, contact." in card
    # …and the harmless column is still described usefully.
    assert "Wellington" in card


async def test_a_card_says_when_nothing_has_been_profiled(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, _, source_id = await _catalogued(
        migrated_database, isolated_customer_database, profile=False
    )

    card = await _card(org_id, source_id, "shops")

    assert "has not been profiled" in card


async def test_a_card_reflects_an_admins_decision_to_allow(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """A card describes the policy in force, not the classifier's opinion."""
    org_id, user_id, source_id = await _catalogued(migrated_database, isolated_customer_database)
    catalog = await browse.active_catalog(org_id, source_id)
    people = next(table for table in catalog.tables if table.table_name == "people")
    email = next(column for column in people.columns if column.name == "email")

    await policies.set_policy(
        org_id=org_id,
        actor_user_id=user_id,
        data_source_id=source_id,
        column_id=email.id,
        policy="deny",
        reason="Nobody needs these.",
    )
    await cards.refresh_cards(org_id, source_id)

    card = await _card(org_id, source_id, "people")
    assert "values are not available to queries" in card


# ---------------------------------------------------------------------------
# Finding one
# ---------------------------------------------------------------------------


async def test_searching_finds_a_table_by_what_it_holds(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, _, _ = await _catalogued(migrated_database, isolated_customer_database)

    hits = await search.search_cards(org_id, "trading name")

    assert hits
    assert hits[0].table_name == "shops"
    assert hits[0].rank > 0


async def test_searching_revenue_returns_the_orders_card_first(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """The Phase 4 gate criterion, in miniature.

    The fixture has no `orders`, so this plants the same shape the pizza
    database has — a table whose column comment is the only place the word
    "revenue" appears — and asserts that searching for the word finds it above
    everything else.
    """
    engine = create_async_engine(isolated_customer_database.url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("CREATE TABLE orders (id integer PRIMARY KEY, total numeric(8, 2) NOT NULL)")
            )
            await connection.execute(
                text(
                    "COMMENT ON COLUMN orders.total IS "
                    "'Order value in local currency. Revenue excludes cancelled orders.'"
                )
            )
            # A table created after the fixture's grants is invisible to the
            # read-only login until somebody grants it — which is true of a real
            # customer database too, and is why discovery filters on privilege.
            await connection.execute(text(f"GRANT SELECT ON orders TO {customer_db.READER_ROLE}"))
    finally:
        await engine.dispose()

    org_id, _, _ = await _catalogued(migrated_database, isolated_customer_database)

    hits = await search.search_cards(org_id, "revenue")

    assert hits, "the word appears in exactly one card and must be found"
    assert hits[0].table_name == "orders"


async def test_search_understands_how_people_actually_type(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """websearch_to_tsquery: a quoted phrase means a phrase, and a stray
    operator is not a 500."""
    org_id, _, _ = await _catalogued(migrated_database, isolated_customer_database)

    quoted = await search.search_cards(org_id, '"sales regions"')
    nonsense = await search.search_cards(org_id, "and or ((")

    assert quoted and quoted[0].table_name == "regions"
    assert nonsense == []


async def test_an_empty_search_is_empty_rather_than_everything(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, _, _ = await _catalogued(migrated_database, isolated_customer_database)

    assert await search.search_cards(org_id, "   ") == []


async def test_search_can_be_narrowed_to_one_data_source(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, _, source_id = await _catalogued(migrated_database, isolated_customer_database)

    mine = await search.search_cards(org_id, "shops", data_source_id=source_id)
    elsewhere = await search.search_cards(org_id, "shops", data_source_id=uuid.uuid4())

    assert mine
    assert elsewhere == []


async def test_another_organization_finds_nothing(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """Row-level security, from the outside: not a filtered result, an absent one."""
    org_id, _, _ = await _catalogued(migrated_database, isolated_customer_database)
    other = uuid.uuid4()
    engine = create_async_engine(migrated_database)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(other)}
            )
            await connection.execute(
                text("INSERT INTO organizations (id, name) VALUES (:id, 'Elsewhere')"),
                {"id": other},
            )
    finally:
        await engine.dispose()

    assert await search.search_cards(org_id, "shops")
    assert await search.search_cards(other, "shops") == []


async def test_a_superseded_catalog_is_not_searched(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """A search must answer about the database as it is, not as it was."""
    org_id, user_id, source_id = await _catalogued(migrated_database, isolated_customer_database)

    engine = create_async_engine(isolated_customer_database.url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text("DROP TABLE products"))
    finally:
        await engine.dispose()
    await discovery.discover(org_id=org_id, actor_user_id=user_id, data_source_id=source_id)

    hits = await search.search_cards(org_id, "Deliberately unrelated")

    assert hits == [], "the dropped table's card is still in a superseded snapshot"


# ---------------------------------------------------------------------------
# The card builder on its own
# ---------------------------------------------------------------------------


def test_a_card_is_deterministic() -> None:
    """The same catalog must render the same words, or every refresh would
    rewrite every card and re-embedding would never converge."""
    card = cards.CardInput(
        schema_name="public",
        table_name="orders",
        kind="table",
        description="One row per order.",
        row_estimate=1200,
        profiled=True,
        columns=[
            cards.CardColumn(
                name="id", data_type="bigint", nullable=False, is_pk=True, semantic_role="id"
            )
        ],
    )

    assert cards.build_card(card) == cards.build_card(card)


def test_a_long_table_does_not_produce_an_unusable_card() -> None:
    columns = [
        cards.CardColumn(name=f"c{index}", data_type="text", nullable=True, is_pk=False)
        for index in range(cards.MAX_LISTED_COLUMNS + 10)
    ]
    card = cards.build_card(
        cards.CardInput(
            schema_name="public",
            table_name="wide",
            kind="table",
            description=None,
            row_estimate=None,
            profiled=True,
            columns=columns,
        )
    )

    assert f"Columns ({len(columns)}):" in card
    assert "and 10 more column(s)" in card
    assert "- c39 (text)" in card
    assert "- c40 (text)" not in card
