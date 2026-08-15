"""The front door: resolve, call, meter, parse, repair once.

The property that matters most here is the one the DAL's front door has for
customer data — **there is no path that spends tokens without leaving a row**.
So every test that makes a call also counts the ledger, including the ones about
failures and the ones about repairs, where it would be easiest to lose a row.

Against a real platform database for the same reason ``tests/dal`` is: the
guarantee is "a row exists", and only a database can answer that.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from llm_fixture import build_settings, seed_run
from pydantic import BaseModel
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dataagent.db import engine as engine_module
from dataagent.llm import registry, service
from dataagent.llm.base import LLMError, Message, ProviderCaps, Usage
from dataagent.llm.fake import FakeLLM
from dataagent.llm.retry import RetryPolicy
from dataagent.llm.structured import StructuredOutputError
from dataagent.tenancy import session as session_module


class Plan(BaseModel):
    steps: list[str]


QUESTION = [Message(role="system", content="platform rules"), Message(role="user", content="ask")]


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
                text("INSERT INTO organizations (id, name) VALUES (:id, 'Front door')"),
                {"id": org_id},
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


# ---------------------------------------------------------------------------
# Resolution and metering
# ---------------------------------------------------------------------------


async def test_the_role_picks_the_model_and_the_call_is_metered(
    platform: URL, migrated_database: URL, fake_llm: FakeLLM
) -> None:
    """No call site names a model: ``plan`` is strong, so the strong model is
    what the provider is asked for and what the ledger records."""
    org_id = await _org(migrated_database)
    fake_llm.script("about 400 orders")

    completion = await service.complete(
        role="plan", org_id=org_id, messages=QUESTION, settings=build_settings()
    )

    assert completion.text == "about 400 orders"
    assert fake_llm.last_call().model == "fake-strong"

    (row,) = await _ledger(platform, org_id)
    assert row["role"] == "plan"
    assert row["tier"] == "strong"
    assert row["model"] == "fake-strong"
    assert row["status"] == "ok"
    assert row["repaired"] is False


async def test_a_cheap_role_is_billed_against_the_small_model(
    platform: URL, migrated_database: URL, fake_llm: FakeLLM
) -> None:
    org_id = await _org(migrated_database)
    fake_llm.script("smalltalk")

    await service.complete(
        role="intake", org_id=org_id, messages=QUESTION, settings=build_settings()
    )

    (row,) = await _ledger(platform, org_id)
    assert (row["tier"], row["model"]) == ("small", "fake-small")


async def test_the_run_and_the_actor_reach_the_row(
    platform: URL, migrated_database: URL, fake_llm: FakeLLM
) -> None:
    """ "What did this run cost" is a question about a run, so the run is on the
    row rather than inferred from a time window."""
    org_id = await _org(migrated_database)
    run_id = await seed_run(migrated_database, org_id)
    fake_llm.script("ok")

    await service.complete(
        role="critic",
        org_id=org_id,
        messages=QUESTION,
        run_id=run_id,
        settings=build_settings(),
    )

    (row,) = await _ledger(platform, org_id)
    assert row["run_id"] == run_id


async def test_a_provider_failure_is_metered_and_re_raised(
    platform: URL, migrated_database: URL, fake_llm: FakeLLM
) -> None:
    """The caller gets the same error it would have got without any of this, and
    the ledger has the row either way — the DAL's rule, applied to tokens."""
    org_id = await _org(migrated_database)
    fake_llm.script(raises=LLMError("the provider returned 503", retryable=True))

    with pytest.raises(LLMError, match="503"):
        await service.complete(
            role="sql", org_id=org_id, messages=QUESTION, settings=build_settings()
        )

    # Three rows, not one: architecture 8.5 retries a 503 before giving up, and
    # every attempt is a real call that has to be visible. A single row here
    # would mean two attempts went unrecorded.
    rows = await _ledger(platform, org_id)
    assert len(rows) == 3
    assert all(row["status"] == "error" for row in rows)
    assert all(row["error"] == "the provider returned 503" for row in rows)
    assert all(row["input_tokens"] == 0 for row in rows)


async def test_an_unresolvable_role_never_reaches_a_provider(
    platform: URL, migrated_database: URL, fake_llm: FakeLLM
) -> None:
    """Configuration failures are not calls: nothing was spent, so nothing is
    recorded, and the fake can prove it was never asked."""
    org_id = await _org(migrated_database)

    with pytest.raises(registry.ProviderNotConfiguredError):
        await service.complete(
            role="plan",
            org_id=org_id,
            messages=QUESTION,
            settings=build_settings(llm_models={"fake": {"small": "fake-small"}}),
        )

    assert fake_llm.calls == ()
    assert await _ledger(platform, org_id) == []


# ---------------------------------------------------------------------------
# Structured output
# ---------------------------------------------------------------------------


async def test_a_schema_returns_a_typed_object(
    platform: URL, migrated_database: URL, fake_llm: FakeLLM
) -> None:
    org_id = await _org(migrated_database)
    fake_llm.script_json(Plan(steps=["count orders"]))

    completion = await service.complete(
        role="plan", org_id=org_id, messages=QUESTION, schema=Plan, settings=build_settings()
    )

    assert completion.parsed_as(Plan).steps == ["count orders"]
    assert not completion.repaired
    assert len(await _ledger(platform, org_id)) == 1


async def test_the_schema_instruction_goes_after_the_platform_rules(
    platform: URL, migrated_database: URL, fake_llm: FakeLLM
) -> None:
    """Instruction layers are ordered and L0 wins every conflict (arch 4.8), so
    a formatting instruction must not be placed above the platform's rules."""
    org_id = await _org(migrated_database)
    fake_llm.script_json(Plan(steps=["a"]))

    await service.complete(
        role="plan", org_id=org_id, messages=QUESTION, schema=Plan, settings=build_settings()
    )

    sent = fake_llm.last_call().request.messages
    assert sent[0].content == "platform rules"
    assert "JSON Schema" in sent[1].content
    assert sent[2].content == "ask"


async def test_no_schema_means_no_json_instruction(
    platform: URL, migrated_database: URL, fake_llm: FakeLLM
) -> None:
    org_id = await _org(migrated_database)
    fake_llm.script("prose is fine here")

    await service.complete(
        role="compose", org_id=org_id, messages=QUESTION, settings=build_settings()
    )

    assert list(fake_llm.last_call().request.messages) == QUESTION


async def test_a_provider_with_native_schema_support_is_not_given_the_instruction(
    platform: URL, migrated_database: URL
) -> None:
    """The prompt is a fallback for providers that cannot constrain decoding.
    Sending it to one that can wastes tokens on every structured call."""

    class Native(FakeLLM):
        def capabilities(self) -> ProviderCaps:
            return ProviderCaps(name="native", supports_response_schema=True, is_stub=True)

    org_id = await _org(migrated_database)
    native = Native().script_json(Plan(steps=["a"]))
    registry.register_provider("fake", lambda: native)
    try:
        completion = await service.complete(
            role="plan", org_id=org_id, messages=QUESTION, schema=Plan, settings=build_settings()
        )
    finally:
        registry.clear_provider_cache()

    assert list(native.last_call().request.messages) == QUESTION
    # Validated anyway: "supports JSON schema" means different things on
    # different APIs, and the type of ``parsed`` must not depend on which
    # provider answered.
    assert completion.parsed_as(Plan).steps == ["a"]


async def test_an_unparseable_reply_is_repaired_once_and_both_calls_are_metered(
    platform: URL, migrated_database: URL, fake_llm: FakeLLM
) -> None:
    """A repair is a second real call: two provider calls, two ledger rows, and
    the second flagged so "how often does the model ignore the schema" is a
    GROUP BY rather than a guess."""
    org_id = await _org(migrated_database)
    fake_llm.script("I think three steps", times=1).script_json(Plan(steps=["a", "b"]))

    completion = await service.complete(
        role="plan", org_id=org_id, messages=QUESTION, schema=Plan, settings=build_settings()
    )

    assert completion.parsed_as(Plan).steps == ["a", "b"]
    assert completion.repaired

    rows = await _ledger(platform, org_id)
    assert [row["repaired"] for row in rows] == [False, True]
    assert all(row["status"] == "ok" for row in rows)


async def test_the_repair_shows_the_model_its_own_reply_and_the_problem(
    platform: URL, migrated_database: URL, fake_llm: FakeLLM
) -> None:
    org_id = await _org(migrated_database)
    fake_llm.script("I think three steps", times=1).script_json(Plan(steps=["a"]))

    await service.complete(
        role="plan", org_id=org_id, messages=QUESTION, schema=Plan, settings=build_settings()
    )

    repair = fake_llm.calls[1].request.messages
    assert repair[-2] == Message(role="assistant", content="I think three steps")
    assert "no JSON object" in repair[-1].content
    # The original question survives the repair: a second answer to a different
    # question would be indistinguishable from a correct one.
    assert repair[0].content == "platform rules"


async def test_two_bad_replies_fail_with_both_complaints(
    platform: URL, migrated_database: URL, fake_llm: FakeLLM
) -> None:
    """The pair is the diagnosis: the same complaint twice means the schema is
    wrong for the task, two different ones mean the model is guessing."""
    org_id = await _org(migrated_database)
    fake_llm.script("no json at all", times=1).script('{"steps": "not a list"}')

    with pytest.raises(StructuredOutputError) as raised:
        await service.complete(
            role="plan", org_id=org_id, messages=QUESTION, schema=Plan, settings=build_settings()
        )

    message = str(raised.value)
    assert "two attempts" in message
    assert "no JSON object" in message
    assert "steps" in message
    assert len(await _ledger(platform, org_id)) == 2


async def test_the_repair_is_never_a_loop(
    platform: URL, migrated_database: URL, fake_llm: FakeLLM
) -> None:
    """Once, not until it works. A model that cannot follow a schema twice will
    not follow it on the fifth attempt, and each attempt is real money."""
    org_id = await _org(migrated_database)
    fake_llm.script("never json")

    with pytest.raises(StructuredOutputError):
        await service.complete(
            role="plan", org_id=org_id, messages=QUESTION, schema=Plan, settings=build_settings()
        )

    assert fake_llm.count() == 2


async def test_a_failure_during_the_repair_is_metered_too(
    platform: URL, migrated_database: URL, fake_llm: FakeLLM
) -> None:
    org_id = await _org(migrated_database)
    fake_llm.script("not json", times=1).script(raises=LLMError("429", retryable=True))

    # One attempt per pass, so this test stays about the repair pairing rather
    # than about how many times a 429 is retried (test_retry.py owns that).
    with pytest.raises(LLMError, match="429"):
        await service.complete(
            role="plan",
            org_id=org_id,
            messages=QUESTION,
            schema=Plan,
            retry=RetryPolicy(attempts=1),
            settings=build_settings(),
        )

    rows = await _ledger(platform, org_id)
    assert [(row["status"], row["repaired"]) for row in rows] == [("ok", False), ("error", True)]


async def test_parsed_as_refuses_the_wrong_schema(
    platform: URL, migrated_database: URL, fake_llm: FakeLLM
) -> None:
    """Callers ask for the schema they passed; the check lives in one place
    rather than as an unchecked cast at every call site."""

    class Other(BaseModel):
        verdict: str

    org_id = await _org(migrated_database)
    fake_llm.script_json(Plan(steps=["a"]))

    completion = await service.complete(
        role="plan", org_id=org_id, messages=QUESTION, schema=Plan, settings=build_settings()
    )

    with pytest.raises(LLMError, match="carries no Other"):
        completion.parsed_as(Other)


async def test_the_tokens_the_provider_reported_are_what_is_billed(
    platform: URL, migrated_database: URL, fake_llm: FakeLLM
) -> None:
    org_id = await _org(migrated_database)
    fake_llm.script("x", usage=Usage(input_tokens=900, output_tokens=100))

    await service.complete(
        role="plan",
        org_id=org_id,
        messages=QUESTION,
        settings=build_settings(llm_prices={"fake-strong": {"input": 10.0, "output": 30.0}}),
    )

    (row,) = await _ledger(platform, org_id)
    assert (row["input_tokens"], row["output_tokens"]) == (900, 100)
    assert row["tokens_estimated"] is False
    # 900 in at $10/M plus 100 out at $30/M.
    assert str(row["cost_usd"]) == "0.012000"
