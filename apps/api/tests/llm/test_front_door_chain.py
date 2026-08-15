"""The chain and the ceiling, seen from the front door.

``test_fallback.py`` proves the walking logic and ``test_budget.py`` the
arithmetic. This file proves they are actually wired into ``llm.complete`` — the
gap where a correct component and a correct caller can still add up to nothing.

The load-bearing assertion is on the ledger. A fallback that produced the right
answer but only one row would have hidden which provider served it, and "which
model answered" is the question the ledger exists to answer.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from llm_fixture import FAKE_MODELS, build_settings, seed_run
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dataagent.db import engine as engine_module
from dataagent.llm import registry, service
from dataagent.llm.base import LLMError, Message
from dataagent.llm.budget import RunCostExceededError
from dataagent.llm.fake import FakeLLM
from dataagent.llm.retry import RetryPolicy
from dataagent.tenancy import session as session_module

QUESTION = [Message(role="system", content="platform rules"), Message(role="user", content="ask")]

TWO_PROVIDERS = {
    **FAKE_MODELS,
    "backup": {"small": "backup-small", "mid": "backup-mid", "strong": "backup-strong"},
}

#: No waiting: the backoff arithmetic is tested in test_retry.py, and a suite
#: that sleeps for real is a suite people stop running.
NO_WAIT = RetryPolicy(attempts=2, base_delay=0.0)


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
                text("INSERT INTO organizations (id, name) VALUES (:id, 'Chained')"), {"id": org_id}
            )
    finally:
        await engine.dispose()
    return org_id


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


async def test_a_rate_limited_primary_falls_back_and_the_ledger_shows_both(
    platform: URL, migrated_database: URL
) -> None:
    """The gate's fallback criterion, end to end.

    Two rows, not one: an `error` row naming the provider that refused and an
    `ok` row naming the one that answered. A single success row would be a
    fallback that happened invisibly, which is worse than one that did not.
    """
    org_id = await _org(migrated_database)
    primary = FakeLLM().script(raises=LLMError("the provider returned 429", retryable=True))
    backup = FakeLLM().script("the backup answered")

    registry.register_provider("fake", lambda: primary)
    registry.register_provider("backup", lambda: backup)
    try:
        completion = await service.complete(
            role="plan",
            org_id=org_id,
            messages=QUESTION,
            retry=NO_WAIT,
            settings=build_settings(llm_providers=("fake", "backup"), llm_models=TWO_PROVIDERS),
        )
    finally:
        registry.clear_provider_cache()

    assert completion.text == "the backup answered"
    assert completion.provider == "fake"  # FakeLLM always names itself
    assert backup.count() == 1

    rows = await _ledger(platform, org_id)
    assert [(row["provider"], row["status"]) for row in rows] == [
        ("fake", "error"),
        ("fake", "error"),  # retried on the primary before moving on
        ("backup", "ok"),
    ]
    assert all(row["error"] == "the provider returned 429" for row in rows[:2])
    assert rows[-1]["model"] == "backup-strong"


async def test_a_bad_request_does_not_walk_the_chain(platform: URL, migrated_database: URL) -> None:
    """Non-retryable means our bug. Trying the backup would bill for a second
    copy of the same error and bury the cause."""
    org_id = await _org(migrated_database)
    primary = FakeLLM().script(raises=LLMError("the provider returned 400", retryable=False))
    backup = FakeLLM().script("must not be reached")

    registry.register_provider("fake", lambda: primary)
    registry.register_provider("backup", lambda: backup)
    try:
        with pytest.raises(LLMError, match="400"):
            await service.complete(
                role="plan",
                org_id=org_id,
                messages=QUESTION,
                retry=NO_WAIT,
                settings=build_settings(llm_providers=("fake", "backup"), llm_models=TWO_PROVIDERS),
            )
    finally:
        registry.clear_provider_cache()

    assert backup.calls == ()
    rows = await _ledger(platform, org_id)
    assert [(row["provider"], row["status"]) for row in rows] == [("fake", "error")]


async def test_the_ceiling_stops_the_call_before_the_provider_is_asked(
    platform: URL, migrated_database: URL, fake_llm: FakeLLM
) -> None:
    """Enforced *before* the call. A limit checked afterwards is a report, and
    the tokens are already spent by the time it prints."""
    org_id = await _org(migrated_database)
    run_id = await seed_run(migrated_database, org_id)
    fake_llm.script("expensive", contains="ask")
    settings = build_settings(
        llm_prices={"fake-strong": {"input": 1000.0, "output": 1000.0}},
        llm_run_cost_limit_usd=0.001,
    )

    # First call lands under the ceiling and is recorded.
    await service.complete(
        role="plan", org_id=org_id, messages=QUESTION, run_id=run_id, settings=settings
    )
    calls_after_first = fake_llm.count()

    with pytest.raises(RunCostExceededError, match="ceiling"):
        await service.complete(
            role="plan", org_id=org_id, messages=QUESTION, run_id=run_id, settings=settings
        )

    # The provider was never asked the second time, and nothing was recorded for
    # a call that did not happen.
    assert fake_llm.count() == calls_after_first
    assert len(await _ledger(platform, org_id)) == 1
