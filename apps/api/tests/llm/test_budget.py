"""The per-run spending ceiling.

Against a real platform database, because the ceiling is computed from the same
``usage_ledger`` rows the meter writes — a test with an in-memory counter would
prove that the counter works, not that the ledger and the ceiling agree about
what a run has cost.

The case that matters most is the unpriced one. A model with no configured price
records ``cost_usd = NULL``; summed, that is a total smaller than the truth and a
ceiling that is never reached. A cap that cannot see what it is spending is not
a cap, and these tests are what stop that from being a silent property.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from llm_fixture import build_settings
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dataagent.db import engine as engine_module
from dataagent.llm import budget, meter
from dataagent.llm.base import Usage
from dataagent.llm.budget import RunCostExceededError
from dataagent.tenancy import session as session_module

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


async def _org(migrated_database: URL) -> uuid.UUID:
    org_id = uuid.uuid4()
    engine = create_async_engine(migrated_database)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            await connection.execute(
                text("INSERT INTO organizations (id, name) VALUES (:id, 'Capped')"), {"id": org_id}
            )
    finally:
        await engine.dispose()
    return org_id


async def _spend(org_id: uuid.UUID, run_id: uuid.UUID, *, model: str, tokens: int) -> None:
    """Put a real metered call on the ledger, priced the real way."""
    await meter.record(
        org_id=org_id,
        role="plan",
        tier="strong",
        provider="fake",
        model=model,
        usage=Usage(input_tokens=tokens, output_tokens=0),
        latency_ms=1,
        run_id=run_id,
        settings=build_settings(llm_prices=PRICES),
    )


# ---------------------------------------------------------------------------
# When the ceiling applies at all
# ---------------------------------------------------------------------------


async def test_no_configured_ceiling_means_no_check(platform: URL, migrated_database: URL) -> None:
    """The default. Right for a person asking one question, wrong for a sweep —
    which is why the eval and gate configs set one."""
    org_id = await _org(migrated_database)

    await budget.assert_within_limit(
        org_id=org_id,
        run_id=uuid.uuid4(),
        model="fake-strong",
        settings=build_settings(llm_prices=PRICES),
    )


async def test_a_call_with_no_run_is_not_capped(platform: URL, migrated_database: URL) -> None:
    """Stated as a test because it is a real hole rather than an oversight:
    there is nothing to accumulate against without a run id. Everything that
    spends in a loop has one; Phase 8's BudgetState closes this properly."""
    org_id = await _org(migrated_database)

    await budget.assert_within_limit(
        org_id=org_id,
        run_id=None,
        model="fake-strong",
        settings=build_settings(llm_prices=PRICES, llm_run_cost_limit_usd=0.000001),
    )


# ---------------------------------------------------------------------------
# The ceiling itself
# ---------------------------------------------------------------------------


async def test_a_run_under_its_ceiling_may_keep_going(
    platform: URL, migrated_database: URL
) -> None:
    org_id, run_id = await _org(migrated_database), uuid.uuid4()
    await _spend(org_id, run_id, model="fake-strong", tokens=1000)  # $0.01

    await budget.assert_within_limit(
        org_id=org_id,
        run_id=run_id,
        model="fake-strong",
        settings=build_settings(llm_prices=PRICES, llm_run_cost_limit_usd=1.0),
    )


async def test_a_run_at_its_ceiling_is_stopped(platform: URL, migrated_database: URL) -> None:
    org_id, run_id = await _org(migrated_database), uuid.uuid4()
    await _spend(org_id, run_id, model="fake-strong", tokens=1_000_000)  # $10.00

    with pytest.raises(RunCostExceededError) as raised:
        await budget.assert_within_limit(
            org_id=org_id,
            run_id=run_id,
            model="fake-strong",
            settings=build_settings(llm_prices=PRICES, llm_run_cost_limit_usd=1.0),
        )

    message = str(raised.value)
    assert "10.0000" in message and "1.00" in message
    assert not raised.value.retryable  # spending more is not a way to succeed


async def test_only_this_run_counts_towards_this_run_s_ceiling(
    platform: URL, migrated_database: URL
) -> None:
    """An organization's total spend is B-025's business. A ceiling that counted
    a neighbouring run would stop work for reasons its own trace cannot explain."""
    org_id = await _org(migrated_database)
    expensive, cheap = uuid.uuid4(), uuid.uuid4()
    await _spend(org_id, expensive, model="fake-strong", tokens=1_000_000)  # $10.00

    await budget.assert_within_limit(
        org_id=org_id,
        run_id=cheap,
        model="fake-strong",
        settings=build_settings(llm_prices=PRICES, llm_run_cost_limit_usd=1.0),
    )


async def test_spend_is_read_back_with_its_unpriced_count(
    platform: URL, migrated_database: URL
) -> None:
    org_id, run_id = await _org(migrated_database), uuid.uuid4()
    await _spend(org_id, run_id, model="fake-strong", tokens=1000)  # priced: $0.01
    await _spend(org_id, run_id, model="mystery-model", tokens=1000)  # unpriced: NULL

    spend = await budget.spent_on_run(org_id, run_id)

    assert spend.usd == Decimal("0.010000")
    assert spend.unpriced_calls == 1


# ---------------------------------------------------------------------------
# Unpriced models: the case the whole design turns on
# ---------------------------------------------------------------------------


async def test_an_unpriced_model_is_refused_under_a_ceiling(
    platform: URL, migrated_database: URL
) -> None:
    """Its cost would be recorded as NULL, sail past every check, and spend
    without limit. A ceiling that cannot see what it is spending is not one."""
    org_id = await _org(migrated_database)

    with pytest.raises(RunCostExceededError, match="LLM_PRICES"):
        await budget.assert_within_limit(
            org_id=org_id,
            run_id=uuid.uuid4(),
            model="mystery-model",
            settings=build_settings(llm_prices=PRICES, llm_run_cost_limit_usd=1.0),
        )


async def test_a_run_holding_unpriced_calls_cannot_be_capped_honestly(
    platform: URL, migrated_database: URL
) -> None:
    """Even for a priced model: part of this run's spend is unknown, so the
    remaining headroom is unknown too."""
    org_id, run_id = await _org(migrated_database), uuid.uuid4()
    await _spend(org_id, run_id, model="mystery-model", tokens=1000)

    with pytest.raises(RunCostExceededError, match="unknown cost"):
        await budget.assert_within_limit(
            org_id=org_id,
            run_id=run_id,
            model="fake-strong",
            settings=build_settings(llm_prices=PRICES, llm_run_cost_limit_usd=1.0),
        )


async def test_the_unpriced_risk_can_be_accepted_knowingly(
    platform: URL, migrated_database: URL
) -> None:
    """Off by default, available on purpose — but it is a choice someone makes,
    not a default they inherit."""
    org_id = await _org(migrated_database)

    await budget.assert_within_limit(
        org_id=org_id,
        run_id=uuid.uuid4(),
        model="mystery-model",
        settings=build_settings(
            llm_prices=PRICES,
            llm_run_cost_limit_usd=1.0,
            llm_refuse_unpriced_when_capped=False,
        ),
    )
