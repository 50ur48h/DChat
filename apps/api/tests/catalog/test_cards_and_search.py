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


def _coded_column(counts: dict[str, int], sample_rows: int) -> cards.CardColumn:
    """A category column as the profiler would hand it over, counts and all."""
    return cards.CardColumn(
        name="move_type",
        data_type="text",
        nullable=True,
        is_pk=False,
        semantic_role="dimension",
        distinct_est=len(counts),
        top_values=[cards.ValueCount(value=value, count=count) for value, count in counts.items()],
        sample_rows=sample_rows,
    )


def _card_with(column: cards.CardColumn) -> str:
    return cards.build_card(
        cards.CardInput(
            schema_name="public",
            table_name="fact_stock_move",
            kind="table",
            description=None,
            row_estimate=51_356,
            profiled=True,
            columns=[column],
        )
    )


def test_a_code_column_says_which_of_its_values_is_typical() -> None:
    """**B-092, and the whole of it.** The profile counts how often each value
    appeared, stored the counts, and the card used to print `examples: DO, PI,
    UC, CN, GR` — so a code on 0.01% of rows read exactly like one on 78%.

    A live run then answered a purchasing question from a filter matching 7 rows
    in 51,356, and nothing in the prompt suggested that was odd (**B-060**).
    """
    line = _card_with(
        _coded_column({"DO": 3925, "PI": 853, "UC": 208, "CN": 8, "GR": 6}, sample_rows=5000)
    )

    assert "DO 78%" in line
    assert "PI 17%" in line
    # The one that matters: a code on six rows in five thousand must not round
    # to 0% and read as absent, and must not read like the other four.
    assert "GR 0.1%" in line
    assert "examples:" not in line


def test_a_card_says_the_rows_it_read_were_the_tables_first() -> None:
    """The profile is the head of the table, not a random sample — 5.2's
    deliberate choice, since ordering would sort somebody's warehouse. A model
    choosing a filter from this list is entitled to know the list may not be the
    whole vocabulary: on the warehouse B-060 came from, the card advertised five
    codes for a column that has eight, and the third-largest never appeared.
    """
    line = _card_with(_coded_column({"DO": 3925, "PI": 853}, sample_rows=5000))

    assert "in the first 5,000 rows" in line
    assert "the table's first" in line


def test_a_share_below_a_tenth_of_a_percent_is_not_rounded_away() -> None:
    """`0%` would say the value does not occur, which is the opposite of what a
    profile that saw it means."""
    line = _card_with(_coded_column({"COMMON": 9999, "RARE": 1}, sample_rows=10_000))

    assert "RARE <0.1%" in line
    assert "RARE 0%" not in line


def test_a_column_profiled_before_counts_were_kept_still_builds_a_card() -> None:
    """A row written before B-092 has values and no counts. It gets the values
    and no shares — a card that cannot be built would be a worse answer than one
    that says less."""
    column = cards.CardColumn(
        name="move_type",
        data_type="text",
        nullable=True,
        is_pk=False,
        distinct_est=2,
        top_values=[cards.ValueCount(value="DO", count=0), cards.ValueCount(value="PI", count=0)],
        sample_rows=None,
    )

    line = _card_with(column)

    assert "examples: DO, PI" in line
    assert "distinct in sample" in line


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


async def test_a_whole_question_finds_the_table_it_is_about(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """**B-041**, and the reason the M7 gate could not pass.

    ``websearch_to_tsquery`` joins bare words with AND, so a question asks for a
    card containing *every* word in it — including "how", "many" and "were". No
    card contains all of them, so the agent's primary way of finding anything
    returned nothing for the one question the phase is judged on, and the model
    was handed an empty catalog.

    Asserted as a question rather than as a keyword because that is how
    ``search_tables`` is really called: the agent passes the user's words
    through, and a test that searched for "shops" would have gone on passing
    while the product did not work.
    """
    org_id, _, _ = await _catalogued(migrated_database, isolated_customer_database)

    hits = await search.search_cards(org_id, "How many shops opened in 2021?")

    assert hits, "a question about shops must find the shops table"
    assert hits[0].table_name == "shops"


async def test_a_question_still_prefers_the_table_it_names(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """The OR pass widens what matches; it must not flatten what ranks.

    Matching any word would be useless if every card came back level — the
    context builder drops the lowest-ranked cards first, so an order that means
    nothing would drop tables at random.
    """
    org_id, _, _ = await _catalogued(migrated_database, isolated_customer_database)

    # "1999" appears in no card, so the strict pass cannot match and the OR pass
    # is what answers — which is the path being measured here.
    hits = await search.search_cards(org_id, "How did our shops and regions look in 1999?")

    assert len(hits) > 1, "a loose question should offer more than one candidate"
    assert {hit.table_name for hit in hits[:2]} == {"shops", "regions"}


async def test_the_strict_query_wins_when_it_matches_anything(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """AND first, so two words still mean both words.

    The fallback is the answer to "this matched nothing", not a looser search
    everywhere: "trading name" appears in exactly one card, and widening to OR
    would bury it under every card mentioning either word.
    """
    org_id, _, _ = await _catalogued(migrated_database, isolated_customer_database)

    hits = await search.search_cards(org_id, "trading name")

    assert [hit.table_name for hit in hits] == ["shops"], (
        "a phrase that matches strictly must not be widened"
    )


async def test_an_empty_search_is_empty_rather_than_everything(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, _, _ = await _catalogued(migrated_database, isolated_customer_database)

    assert await search.search_cards(org_id, "   ") == []


async def test_a_search_of_nothing_but_stopwords_finds_nothing(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """The OR pass must not turn "the and of" into "everything".

    PostgreSQL drops stopwords itself, which leaves an empty tsquery — so this
    holds the property rather than a hand-maintained word list that would drift
    from the index it is meant to agree with.
    """
    org_id, _, _ = await _catalogued(migrated_database, isolated_customer_database)

    assert await search.search_cards(org_id, "the and of were") == []


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
