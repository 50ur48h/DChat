"""Profiling a real database, and the promise that governs it.

The load-bearing test here is
``test_no_raw_personal_data_ever_reaches_the_platform_database``: it profiles a
table of email addresses and then reads the platform database *as text*, whole,
looking for any of them. Masking that happens on the way in is the only kind
that is true, and this is how that is checked rather than asserted.

The rest is the budget. A profiler without one is a tool that reads somebody's
production database until something gives, so "it stopped" is tested as
carefully as "it worked".
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from customer_db import CustomerDatabase
from dataagent.catalog import browse, cards, discovery, policies, profiler
from dataagent.datasources import service as datasources
from dataagent.db import engine as engine_module
from dataagent.secrets.local import LocalSecretsProvider
from dataagent.tenancy import session as session_module

#: Addresses the fixture inserts. Not a pattern — the literal strings, because
#: the test that matters greps the platform database for exactly these.
PLANTED = (
    "ada@example.com",
    "grace@example.com",
    "linus@example.com",
    "ada.lovelace@example.org",
    "grace.hopper@example.org",
)


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


async def _discovered(
    migrated_database: URL, customer: CustomerDatabase
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """An organization with the customer database registered, verified, crawled."""
    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    engine = create_async_engine(migrated_database)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            await connection.execute(
                text("INSERT INTO organizations (id, name) VALUES (:id, 'Profiling')"),
                {"id": org_id},
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
    return org_id, user_id, view.id


async def _column(
    org_id: uuid.UUID, data_source_id: uuid.UUID, table: str, column: str
) -> browse.CatalogColumnView:
    catalog = await browse.active_catalog(org_id, data_source_id)
    found = next(entry for entry in catalog.tables if entry.table_name == table)
    return next(entry for entry in found.columns if entry.name == column)


async def _whole_platform_database_as_text(url: URL, org_id: uuid.UUID) -> str:
    """Every catalog row this organization owns, rendered as one string.

    Deliberately blunt. A test that looked only at the columns it expected to
    hold a leak would pass while a leak sat in a column nobody thought about.
    """
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            dumped: list[str] = []
            for table in (
                "catalog_snapshots",
                "catalog_tables",
                "catalog_columns",
                "catalog_relationships",
                "column_policies",
                "audit_log",
                "data_sources",
            ):
                rows = await connection.execute(text(f"SELECT row_to_json(t)::text FROM {table} t"))
                dumped.extend(str(row[0]) for row in rows.fetchall())
            return "\n".join(dumped)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# The promise
# ---------------------------------------------------------------------------


async def test_no_raw_personal_data_ever_reaches_the_platform_database(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """Architecture M4: samples are masked *at write time*.

    A value hidden by a query later has already been stored, backed up and
    replicated. So this profiles a table of real-shaped addresses and then reads
    everything this organization owns, looking for any of them.
    """
    org_id, user_id, source_id = await _discovered(migrated_database, isolated_customer_database)

    outcome = await profiler.profile(org_id=org_id, actor_user_id=user_id, data_source_id=source_id)

    assert outcome.status == "complete"
    assert outcome.sensitive_columns >= 2, "the addresses and the contact column"

    dumped = await _whole_platform_database_as_text(platform, org_id)
    for planted in PLANTED:
        assert planted not in dumped, f"{planted} reached the platform database in the clear"
    # And the masked form did arrive, so this is not passing because nothing was
    # profiled at all.
    assert "a***@e***.com" in dumped


async def test_a_column_is_caught_by_its_values_not_only_its_name(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """`contact` announces nothing. Its values announce everything."""
    org_id, user_id, source_id = await _discovered(migrated_database, isolated_customer_database)
    await profiler.profile(org_id=org_id, actor_user_id=user_id, data_source_id=source_id)

    contact = await _column(org_id, source_id, "people", "contact")

    assert contact.sensitivity == "suspected"
    assert contact.policy == "mask"


async def test_a_harmless_column_is_left_alone(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, user_id, source_id = await _discovered(migrated_database, isolated_customer_database)
    await profiler.profile(org_id=org_id, actor_user_id=user_id, data_source_id=source_id)

    city = await _column(org_id, source_id, "people", "city")

    assert city.sensitivity == "none"
    assert city.policy == "allow"
    assert city.semantic_role == "dimension"
    # Its top values are readable, which is the entire point of profiling one.
    assert city.top_values is not None
    assert {entry["value"] for entry in city.top_values} == {
        "Wellington",
        "Auckland",
        "Christchurch",
    }


# ---------------------------------------------------------------------------
# The statistics
# ---------------------------------------------------------------------------


async def test_the_profile_describes_the_sample_it_saw(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, user_id, source_id = await _discovered(migrated_database, isolated_customer_database)
    await profiler.profile(org_id=org_id, actor_user_id=user_id, data_source_id=source_id)

    contact = await _column(org_id, source_id, "people", "contact")
    identifier = await _column(org_id, source_id, "people", "id")

    assert contact.sample_rows == 4
    assert contact.null_frac == pytest.approx(0.25), "one of the four is null"
    assert contact.distinct_est == 3
    assert identifier.null_frac == 0.0
    assert identifier.semantic_role == "id"
    assert identifier.min_val == "1"
    assert identifier.max_val == "4"


async def test_a_snapshot_records_that_it_was_profiled(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, user_id, source_id = await _discovered(migrated_database, isolated_customer_database)
    before = await browse.active_catalog(org_id, source_id)
    await profiler.profile(org_id=org_id, actor_user_id=user_id, data_source_id=source_id)
    after = await browse.active_catalog(org_id, source_id)

    assert before.snapshot.id == after.snapshot.id, "profiling does not build a new snapshot"


# ---------------------------------------------------------------------------
# The budget
# ---------------------------------------------------------------------------


async def test_a_wall_clock_of_nothing_stops_before_the_first_table(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """The budget is checked *before* each table, so zero means zero — not one.

    Written as an extreme because the alternative is a test that takes five
    minutes to prove a five-minute budget.
    """
    org_id, user_id, source_id = await _discovered(migrated_database, isolated_customer_database)

    outcome = await profiler.profile(
        org_id=org_id,
        actor_user_id=user_id,
        data_source_id=source_id,
        budget=profiler.Budget(wall_clock_seconds=0.0),
    )

    assert outcome.status == "partial"
    assert outcome.columns_profiled == 0
    assert outcome.tables_skipped == 5
    assert "Stopped early" in outcome.detail


async def test_the_row_cap_is_what_the_sample_is_taken_from(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, user_id, source_id = await _discovered(migrated_database, isolated_customer_database)

    await profiler.profile(
        org_id=org_id,
        actor_user_id=user_id,
        data_source_id=source_id,
        budget=profiler.Budget(max_rows=2),
    )

    city = await _column(org_id, source_id, "people", "city")
    assert city.sample_rows == 2, "the cap is a cap on rows read, not a suggestion"


async def test_profiling_a_source_with_no_catalog_says_what_to_do(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    engine = create_async_engine(migrated_database)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            await connection.execute(
                text("INSERT INTO organizations (id, name) VALUES (:id, 'Empty')"), {"id": org_id}
            )
            await connection.execute(
                text("INSERT INTO users (id, external_subject, email) VALUES (:i, :s, :e)"),
                {"i": user_id, "s": f"sub-{user_id}", "e": "owner@example.com"},
            )
    finally:
        await engine.dispose()

    view = await datasources.create_data_source(
        org_id=org_id,
        actor_user_id=user_id,
        name="Never crawled",
        engine="pg",
        host=isolated_customer_database.host,
        port=isolated_customer_database.port,
        database=isolated_customer_database.database,
        username=isolated_customer_database.reader_username,
        password=isolated_customer_database.reader_password,
        tls_mode="prefer",
    )

    outcome = await profiler.profile(org_id=org_id, actor_user_id=user_id, data_source_id=view.id)

    assert outcome.status == "none"
    assert "Refresh it before profiling" in outcome.detail


# ---------------------------------------------------------------------------
# Policies, and the refresh that must not undo them
# ---------------------------------------------------------------------------


async def test_an_admins_decision_survives_the_next_refresh(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """The reason policies are stored by name (DECISIONS D-013).

    A refresh that reset somebody's decision would be a leak caused by a routine
    operation — and it would be invisible, because nothing failed.
    """
    org_id, user_id, source_id = await _discovered(migrated_database, isolated_customer_database)
    await profiler.profile(org_id=org_id, actor_user_id=user_id, data_source_id=source_id)

    email = await _column(org_id, source_id, "people", "email")
    assert email.policy == "mask"

    await policies.set_policy(
        org_id=org_id,
        actor_user_id=user_id,
        data_source_id=source_id,
        column_id=email.id,
        policy="allow",
        reason="Support needs to see it; reviewed 2026-08-12.",
    )

    # A change elsewhere forces a whole new snapshot, and with it new column ids.
    engine = create_async_engine(isolated_customer_database.url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text("ALTER TABLE shops ADD COLUMN closed_on date"))
    finally:
        await engine.dispose()

    refreshed = await discovery.discover(
        org_id=org_id, actor_user_id=user_id, data_source_id=source_id
    )
    assert refreshed.changed is True

    after = await _column(org_id, source_id, "people", "email")
    assert after.id != email.id, "the refresh really did rebuild the catalog"
    assert after.policy == "allow", "an Admin's decision was undone by a refresh"
    assert after.policy_decided is True


async def test_a_second_profile_does_not_overrule_a_person(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, user_id, source_id = await _discovered(migrated_database, isolated_customer_database)
    await profiler.profile(org_id=org_id, actor_user_id=user_id, data_source_id=source_id)
    email = await _column(org_id, source_id, "people", "email")
    await policies.set_policy(
        org_id=org_id,
        actor_user_id=user_id,
        data_source_id=source_id,
        column_id=email.id,
        policy="allow",
        reason="Reviewed.",
    )

    await profiler.profile(org_id=org_id, actor_user_id=user_id, data_source_id=source_id)

    after = await _column(org_id, source_id, "people", "email")
    assert after.policy == "allow"
    # The classifier still says what it thinks; it just does not get to act.
    assert after.sensitivity == "suspected"


async def test_the_default_is_mask_and_it_is_not_a_persons_decision(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, user_id, source_id = await _discovered(migrated_database, isolated_customer_database)
    await profiler.profile(org_id=org_id, actor_user_id=user_id, data_source_id=source_id)

    email = await _column(org_id, source_id, "people", "email")

    assert email.policy == "mask"
    assert email.policy_decided is False, "nobody has reviewed it, and the screen must say so"


def test_effective_policy_prefers_a_person_over_a_default() -> None:
    assert policies.effective_policy(stored=None, sensitivity="none") == "allow"
    assert policies.effective_policy(stored=None, sensitivity="suspected") == "mask"
    assert policies.effective_policy(stored="allow", sensitivity="suspected") == "allow"
    assert policies.effective_policy(stored="deny", sensitivity="none") == "deny"


async def test_the_row_count_comes_from_the_engine_not_from_the_sample(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """A table of 72,000 rows profiled from a cap of 5,000 must not be described
    as having about 5,000 rows.

    Found by reading a card built from the real pizza database, which said
    exactly that. It is not a floor or an approximation — it is wrong, and a
    number in a card is read by something that cannot tell.
    """
    org_id, user_id, source_id = await _discovered(migrated_database, isolated_customer_database)

    await profiler.profile(
        org_id=org_id,
        actor_user_id=user_id,
        data_source_id=source_id,
        budget=profiler.Budget(max_rows=2),
    )

    catalog = await browse.active_catalog(org_id, source_id)
    people = next(table for table in catalog.tables if table.table_name == "people")
    email = next(column for column in people.columns if column.name == "email")

    assert email.sample_rows == 2, "the cap applied"
    # Nothing has analysed this fixture, so the engine's answer is "unknown" —
    # which stays unknown. PostgreSQL spells it -1, and clamping that to 0 would
    # have a card state that a four-row table is empty.
    assert people.row_estimate is None

    # Once the engine does know, the profile takes its word and not the sample's.
    engine = create_async_engine(isolated_customer_database.url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text("ANALYZE people"))
    finally:
        await engine.dispose()

    await profiler.profile(
        org_id=org_id,
        actor_user_id=user_id,
        data_source_id=source_id,
        budget=profiler.Budget(max_rows=2),
    )

    catalog = await browse.active_catalog(org_id, source_id)
    people = next(table for table in catalog.tables if table.table_name == "people")
    assert people.row_estimate == 4, "four rows in the table, two in the sample"


# ---------------------------------------------------------------------------
# No figure in a card may come from a sample (B-051)
# ---------------------------------------------------------------------------


def test_the_code_that_sees_the_sample_cannot_produce_a_range() -> None:
    """**The guard, stated structurally rather than as a convention.**

    `profile_column` is handed the sampled values. It has no code path from them
    to `min_val`/`max_val` — a range arrives from the engine or not at all — so
    it *could not* publish a sampled range if someone wanted it to. That is a
    stronger thing to be able to say than "we remember not to".
    """
    profile = profiler.profile_column(
        name="order_date",
        data_type="date",
        is_pk=False,
        values=[date(2025, 2, 1), date(2025, 3, 11)],
        sampled=2,
        budget=profiler.Budget(),
    )

    assert profile.min_val is None
    assert profile.max_val is None, "the sample offered a range and it was not taken"


def test_a_range_given_by_the_engine_is_the_one_recorded() -> None:
    profile = profiler.profile_column(
        name="order_date",
        data_type="date",
        is_pk=False,
        values=[date(2025, 2, 1)],
        sampled=1,
        budget=profiler.Budget(),
        value_range=(date(2025, 2, 1), date(2026, 7, 31)),
    )

    assert (profile.min_val, profile.max_val) == ("2025-02-01", "2026-07-31")


def test_only_types_worth_a_range_are_asked_for_one() -> None:
    """An aggregate over free text costs a scan and buys nothing: "email runs
    from a*** to z***" was never information. Those columns get no range rather
    than a sampled one."""
    assert profiler.wants_range("date") is True
    assert profiler.wants_range("timestamp with time zone") is True
    assert profiler.wants_range("numeric(10,2)") is True
    assert profiler.wants_range("integer") is True
    assert profiler.wants_range("text") is False
    assert profiler.wants_range("jsonb") is False
    assert profiler.wants_range("boolean") is False


async def test_a_card_shows_the_columns_true_range_not_the_samples(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """**B-051 in miniature, over the card a reader actually sees.**

    `pg_sample` returns the *first* n rows — it must not sort a production
    table — so with a cap of 2 the sample sees shops opened in 2020 and 2021 and
    never the one opened in 2024. On the live demo catalog this exact shape
    recorded `orders.order_date` as ending sixteen months early, and the agent
    then refused an answerable question on the strength of it: a wrong refusal
    wearing an honest one's clothes.

    Asserted through `build_card` rather than through the profile row, because
    the card is what a model and a person are given, and a figure that is wrong
    only once it is rendered is still wrong.
    """
    org_id, user_id, source_id = await _discovered(migrated_database, isolated_customer_database)

    await profiler.profile(
        org_id=org_id,
        actor_user_id=user_id,
        data_source_id=source_id,
        budget=profiler.Budget(max_rows=2),
    )
    await cards.refresh_cards(org_id, source_id)

    opened = await _column(org_id, source_id, "shops", "opened_on")
    assert opened.sample_rows == 2, "the sample really did see only the first two rows"
    assert opened.max_val == "2024-05-05", "the engine's answer, not the sample's"

    catalog = await browse.active_catalog(org_id, source_id)
    shops = next(table for table in catalog.tables if table.table_name == "shops")
    card = shops.card_text or ""
    stated = next(line for line in card.splitlines() if line.strip().startswith("- opened_on"))

    # The stated range is the engine's. `2021-02-02` may still appear on this
    # line as a labelled example — that is the other test's business, and it is
    # honest because a sampled example really is an example.
    assert "range 2020-01-01 to 2024-05-05" in stated


async def test_a_card_labels_every_figure_it_cannot_stand_behind(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """The other half of B-051's rule, and the half the card already got right.

    A figure a card states **as a fact about the column** must come from the
    engine. A figure that can only come from the sample is allowed only when the
    card *says so* — `distinct in sample` and `examples:` both do, and both are
    honest because a sampled example really is an example.

    The failure this pins is the middle case: a sample-derived number presented
    bare, as `range 2020-01-01 to 2021-02-02` was. Whoever adds the next figure
    has to choose a side, and this test is where that choice is recorded.
    """
    org_id, user_id, source_id = await _discovered(migrated_database, isolated_customer_database)

    await profiler.profile(
        org_id=org_id,
        actor_user_id=user_id,
        data_source_id=source_id,
        budget=profiler.Budget(max_rows=2),
    )
    await cards.refresh_cards(org_id, source_id)

    catalog = await browse.active_catalog(org_id, source_id)
    shops = next(table for table in catalog.tables if table.table_name == "shops")
    card = shops.card_text or ""

    for line in card.splitlines():
        if "distinct" in line:
            assert "in sample" in line, "a sampled count says it is a sampled count"
        if "2021-02-02" in line:
            assert "examples:" in line, (
                "a value only the sample saw may appear as an example and as nothing else"
            )
