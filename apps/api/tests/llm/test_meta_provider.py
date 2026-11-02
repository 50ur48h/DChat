"""The Meta provider, and proof that configuring it is what reaches it.

The unit half is short because the protocol is ``test_openai_provider.py``'s:
Meta's Model API speaks the same Responses dialect, and those tests already pin
the request we build and every awkward reply. What is Meta's own is the endpoint,
the key and the name, so that is what these assert — and above all that one
vendor's key is never sent to the other.

The front-door half is the one that earns its keep. A provider that is correct
when handed straight to a test can still be unreachable from configuration —
registered under a name nothing resolves, or answering under a name the ledger
then misfiles. So it starts where a deployment does, at ``LLM_PROVIDERS=meta``,
and ends where the product looks, at a ``usage_ledger`` row. Only the network is
replaced: the provider builds its own client from Meta's real base URL, because
that default is part of what is under test.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, SecretStr, ValidationError
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dataagent.config import Settings
from dataagent.db import engine as engine_module
from dataagent.llm import meta as meta_module
from dataagent.llm import registry, service
from dataagent.llm.base import LLMError, LLMRequest, Message, Tags
from dataagent.llm.meta import BASE_URL, MetaProvider
from dataagent.tenancy import session as session_module
from llm_fixture import build_settings

QUESTION = [Message(role="system", content="platform rules"), Message(role="user", content="ask")]

WONDER = "muse-wonder-1.0"

#: The shape of a Meta reply, from dev.meta.ai/docs/protocols/responses: the
#: same `output` items and `usage` counts as OpenAI's, cached share included.
META_REPLY: dict[str, Any] = {
    "model": WONDER,
    "status": "completed",
    "output": [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": '{"steps": ["count the orders"]}'}],
        }
    ],
    "usage": {
        "input_tokens": 1000,
        "output_tokens": 500,
        "input_tokens_details": {"cached_tokens": 600},
    },
}


class Plan(BaseModel):
    steps: list[str]


def meta_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "llm_providers": ("meta",),
        "llm_models": {"meta": {"small": WONDER, "mid": WONDER, "strong": WONDER}},
        "llm_prices": {WONDER: {"input": 4.00, "output": 20.00}},
        "meta_api_key": SecretStr("meta-test-key"),
        "meta_reasoning_effort": "minimal",
    }
    values.update(overrides)
    return build_settings(**values)


# ---------------------------------------------------------------------------
# The provider
# ---------------------------------------------------------------------------


def test_no_key_fails_with_the_fix_in_the_message() -> None:
    with pytest.raises(LLMError) as raised:
        MetaProvider(settings=build_settings())

    message = str(raised.value)
    assert "META_API_KEY" in message
    assert "LLM_PROVIDERS" in message
    assert not raised.value.retryable


def test_an_openai_key_is_never_borrowed_for_meta() -> None:
    """Two vendors' keys in one .env is the ordinary case once both exist. A
    provider that fell back to the other's variable would send a spending
    credential to a third party, so the absence of its own key is a refusal."""
    with pytest.raises(LLMError, match="META_API_KEY"):
        MetaProvider(settings=build_settings(openai_api_key=SecretStr("sk-openai-test")))


def test_the_provider_names_itself_meta_and_keeps_native_schemas() -> None:
    caps = MetaProvider(settings=meta_settings()).capabilities()

    assert caps.name == "meta"
    assert caps.supports_response_schema
    assert not caps.is_stub


def test_the_key_is_not_in_the_repr() -> None:
    provider = MetaProvider(settings=meta_settings(meta_api_key=SecretStr("meta-secret-value")))

    assert "meta-secret-value" not in repr(provider)


def test_the_endpoint_is_meta_s_documented_one() -> None:
    assert BASE_URL == "https://api.meta.ai/v1"


async def test_every_call_carries_the_configured_reasoning_effort() -> None:
    """Measured on a real key before this existed: with no effort sent, Muse
    Wonder spent 798 of an 800-token cap reasoning and returned no text at all.
    The cap counts reasoning, so the effort is what leaves room for an answer."""
    seen: dict[str, Any] = {}

    def answer(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=META_REPLY)

    provider = MetaProvider(
        settings=meta_settings(meta_reasoning_effort="low"),
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(answer), base_url="https://api.meta.test/v1"
        ),
    )
    await provider.complete(
        LLMRequest(model=WONDER, messages=QUESTION, tags=Tags(org_id=uuid.uuid4(), role="critic"))
    )

    assert seen["reasoning"] == {"effort": "low"}


def test_the_default_effort_is_the_one_that_was_measured_to_fit() -> None:
    assert Settings.model_fields["meta_reasoning_effort"].default == "minimal"


def test_an_effort_meta_refuses_is_refused_at_configuration() -> None:
    """``none`` is a value Meta's documentation lists and Muse Wonder answers
    with a 400. Refused at boot, by name, rather than on the first question."""
    with pytest.raises(ValidationError, match="meta_reasoning_effort"):
        build_settings(meta_reasoning_effort="none")


# ---------------------------------------------------------------------------
# From configuration to the ledger
# ---------------------------------------------------------------------------


@pytest.fixture
def meta_wire(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Every request a client built during the test sends, answered locally."""
    seen: list[httpx.Request] = []
    real_client = httpx.AsyncClient

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=META_REPLY)

    def local_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return real_client(*args, transport=httpx.MockTransport(answer), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", local_client)
    return seen


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
                text("INSERT INTO organizations (id, name) VALUES (:id, 'Muse')"), {"id": org_id}
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


@pytest.mark.live_provider
async def test_llm_providers_meta_reaches_meta_and_the_ledger_names_it(
    platform: URL,
    migrated_database: URL,
    meta_wire: list[httpx.Request],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``LLM_PROVIDERS=meta`` → registry → Meta's endpoint → a ledger row.

    Marked ``live_provider`` because the front door obtains a non-stub provider,
    which the session guard otherwise refuses (B-040). No money is at risk: every
    client built during the test answers from ``meta_wire`` and nothing leaves
    the process.

    Both keys are configured on purpose, so the header assertion also proves
    which one travels.
    """
    org_id = await _org(migrated_database)
    settings = meta_settings(openai_api_key=SecretStr("sk-openai-test"))
    # The registry's factory builds the provider from process settings, exactly
    # as a deployment does — which on a developer's machine means their .env and
    # their real key. Pointed at this test's settings, so what is asserted below
    # is the fixture's key and the result is the same on CI as locally.
    monkeypatch.setattr(meta_module, "get_settings", lambda: settings)

    registry.clear_provider_cache()
    try:
        completion = await service.complete(
            role="plan", org_id=org_id, messages=QUESTION, schema=Plan, settings=settings
        )
    finally:
        await registry.aclose_providers()
        registry.clear_provider_cache()

    assert len(meta_wire) == 1
    sent = meta_wire[0]
    assert str(sent.url) == "https://api.meta.ai/v1/responses"
    assert sent.headers["authorization"] == "Bearer meta-test-key"
    assert "sk-openai-test" not in str(sent.headers)
    body = json.loads(sent.content)
    assert body["model"] == WONDER
    assert body["instructions"].startswith("platform rules")
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["reasoning"] == {"effort": "minimal"}

    assert completion.provider == "meta"
    assert completion.parsed == Plan(steps=["count the orders"])

    rows = await _ledger(platform, org_id)
    assert len(rows) == 1
    row = rows[0]
    assert (row["provider"], row["model"], row["status"]) == ("meta", WONDER, "ok")
    assert (row["input_tokens"], row["output_tokens"], row["cached_input_tokens"]) == (
        1000,
        500,
        600,
    )
    # 1000 x $4 + 500 x $20 per million. The cached share is recorded and not yet
    # discounted, as D-067 decided for every provider.
    assert row["cost_usd"] == Decimal("0.014")
