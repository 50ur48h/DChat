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

import pytest
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from customer_db import CustomerDatabase
from dataagent.catalog import browse, discovery, policies, profiler
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
