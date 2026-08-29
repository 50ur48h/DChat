"""What every model call costs, written down (architecture Part 4.9, 8.3).

Against a real platform database, because the properties worth testing are the
ones a database enforces: the check constraint that a failure must say what
failed, the roles and tiers the schema will accept, and row-level security on a
new tenant table.

The cost tests are about one distinction that the whole quota mechanism depends
on: **unpriced is not free**. A model with no configured price records a NULL,
and a NULL is what a later quota check must treat as unknown.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dataagent.db import engine as engine_module
from dataagent.llm import meter
from dataagent.llm.base import Usage
from dataagent.tenancy import session as session_module
from llm_fixture import build_settings, seed_run

PRICES = {"fake-strong": {"input": 10.0, "output": 30.0}}


@pytest.fixture
async def platform(
    app_database: URL, migrated_database: URL, monkeypatch: pytest.MonkeyPatch
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


async def _org(migrated_database: URL) -> tuple[uuid.UUID, uuid.UUID]:
    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    engine = create_async_engine(migrated_database)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            await connection.execute(
                text("INSERT INTO organizations (id, name) VALUES (:id, 'Metered')"), {"id": org_id}
            )
            await connection.execute(
                text("INSERT INTO users (id, external_subject, email) VALUES (:i, :s, :e)"),
                {"i": user_id, "s": f"sub-{user_id}", "e": "owner@example.com"},
            )
    finally:
        await engine.dispose()
    return org_id, user_id


async def _ledger(url: URL, org_id: uuid.UUID) -> list[dict[str, object]]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, false)"), {"org": str(org_id)}
            )
            result = await connection.execute(
                text("SELECT * FROM usage_ledger ORDER BY created_at, id")
            )
            return [dict(row) for row in result.mappings().all()]
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Cost, and the difference between unpriced and free
# ---------------------------------------------------------------------------


def test_an_unpriced_model_costs_nothing_known_rather_than_nothing() -> None:
    """The distinction the quota mechanism rests on.

    Zero would be a lie that looks like a measurement, and it would be summed
    into a monthly total that somebody enforces a limit from.
    """
    assert meter.estimate_cost("fake-strong", Usage(1000, 1000), build_settings()) is None


def test_a_priced_model_is_costed_per_million_tokens() -> None:
    settings = build_settings(llm_prices=PRICES)

    # 1,000 in at $10/M plus 500 out at $30/M = $0.01 + $0.015.
    cost = meter.estimate_cost("fake-strong", Usage(1000, 500), settings)

    assert cost == Decimal("0.025000")


def test_small_calls_do_not_round_away_to_zero() -> None:
    """Six decimal places, because a cheap call costs a fraction of a cent and a
    million of them must not total nothing."""
    settings = build_settings(llm_prices=PRICES)

    cost = meter.estimate_cost("fake-strong", Usage(1, 1), settings)

    assert cost is not None
    assert cost > 0


def test_recording_the_cached_share_does_not_change_what_a_call_costs() -> None:
    """**The point of revision 0034, stated as a test.**

    The owner's instruction was to record the cached share *before* touching
    pricing, so the size of the overstatement is an observation rather than an
    argument. Two calls identical but for how much of the input was cached must
    therefore still cost the same — this is deliberately the *current, known to
    overstate* behaviour, and it is asserted so that modelling the discount is a
    change somebody makes on purpose and this test is what tells them they did.
    """
    settings = build_settings(llm_prices=PRICES)

    none_cached = meter.estimate_cost("fake-strong", Usage(1000, 500), settings)
    mostly_cached = meter.estimate_cost(
        "fake-strong", Usage(1000, 500, cached_input_tokens=900), settings
    )

    assert none_cached == mostly_cached == Decimal("0.025000")


def test_a_malformed_price_is_treated_as_no_price() -> None:
    """A wrong number that looks authoritative is worse than an absent one."""
    settings = build_settings(llm_prices={"fake-strong": {"input": 1.0}})

    assert meter.estimate_cost("fake-strong", Usage(1000, 1000), settings) is None


# ---------------------------------------------------------------------------
# The row itself
# ---------------------------------------------------------------------------


async def test_a_successful_call_records_role_tier_model_and_tokens(
    platform: URL, migrated_database: URL
) -> None:
    """Role and tier are on the row, not derived from the model.

    They have to be: the map between them is configuration and it changes, and
    "what did moving observe to the small tier actually save" is the question
    architecture 8.3's central claim rests on.
    """
    org_id, user_id = await _org(migrated_database)
    run_id = await seed_run(migrated_database, org_id)

    await meter.record(
        org_id=org_id,
        role="observe",
        tier="small",
        provider="fake",
        model="fake-small",
        usage=Usage(input_tokens=120, output_tokens=34, estimated=True),
        latency_ms=250,
        run_id=run_id,
        actor_user_id=user_id,
        settings=build_settings(),
    )

    (row,) = await _ledger(platform, org_id)
    assert row["role"] == "observe"
    assert row["tier"] == "small"
    assert row["provider"] == "fake"
    assert row["model"] == "fake-small"
    assert row["status"] == "ok"
    assert row["input_tokens"] == 120
    assert row["output_tokens"] == 34
    assert row["tokens_estimated"] is True
    assert row["latency_ms"] == 250
    assert row["run_id"] == run_id
    assert row["actor_user_id"] == user_id
    assert row["cost_usd"] is None
    assert row["error"] is None
    assert row["repaired"] is False


async def test_a_priced_call_stores_its_cost(platform: URL, migrated_database: URL) -> None:
    org_id, _ = await _org(migrated_database)

    await meter.record(
        org_id=org_id,
        role="plan",
        tier="strong",
        provider="fake",
        model="fake-strong",
        usage=Usage(input_tokens=1000, output_tokens=500),
        latency_ms=10,
        settings=build_settings(llm_prices=PRICES),
    )

    (row,) = await _ledger(platform, org_id)
    assert row["cost_usd"] == Decimal("0.025000")


async def test_a_failed_call_is_metered_and_says_why(platform: URL, migrated_database: URL) -> None:
    """A provider that fails has still been called, and with WP6.2's chain the
    error row beside an ok row is what shows a fallback happened."""
    org_id, _ = await _org(migrated_database)

    await meter.record(
        org_id=org_id,
        role="sql",
        tier="strong",
        provider="fake",
        model="fake-strong",
        usage=Usage(),
        latency_ms=5,
        error="the provider returned 429",
        settings=build_settings(),
    )

    (row,) = await _ledger(platform, org_id)
    assert row["status"] == "error"
    assert row["error"] == "the provider returned 429"
    assert row["input_tokens"] == 0


async def test_a_failure_without_a_reason_cannot_be_stored(
    platform: URL, migrated_database: URL
) -> None:
    """The constraint revision 0010 applies to refusals, applied here to errors:
    a row claiming failure without saying what failed records nothing."""
    org_id, _ = await _org(migrated_database)
    engine = create_async_engine(platform)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, false)"), {"org": str(org_id)}
            )
            with pytest.raises(Exception, match="error_matches_status"):
                await connection.execute(
                    text(
                        "INSERT INTO usage_ledger "
                        "(org_id, role, tier, provider, model, status) VALUES "
                        "(:org, 'plan', 'strong', 'fake', 'm', 'error')"
                    ),
                    {"org": org_id},
                )
    finally:
        await engine.dispose()


async def test_a_role_the_product_does_not_have_cannot_be_stored(
    platform: URL, migrated_database: URL
) -> None:
    """The schema's CHECK is the backstop for the two copies of the role list.

    ``llm/base.py`` holds the live one and the database holds its own; this is
    what makes a drift between them fail loudly instead of writing nonsense.
    """
    org_id, _ = await _org(migrated_database)
    engine = create_async_engine(platform)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, false)"), {"org": str(org_id)}
            )
            with pytest.raises(Exception, match="role_valid"):
                await connection.execute(
                    text(
                        "INSERT INTO usage_ledger "
                        "(org_id, role, tier, provider, model, status) VALUES "
                        "(:org, 'planner', 'strong', 'fake', 'm', 'ok')"
                    ),
                    {"org": org_id},
                )
    finally:
        await engine.dispose()


async def test_one_organization_cannot_see_another_s_spend(
    platform: URL, migrated_database: URL
) -> None:
    """Usage is commercially sensitive on its own: how much a competitor spends,
    and how often they ask, is inferable from these rows alone."""
    first, _ = await _org(migrated_database)
    second, _ = await _org(migrated_database)

    await meter.record(
        org_id=first,
        role="plan",
        tier="strong",
        provider="fake",
        model="fake-strong",
        usage=Usage(10, 10),
        latency_ms=1,
        settings=build_settings(),
    )

    assert len(await _ledger(platform, first)) == 1
    assert await _ledger(platform, second) == []
