"""The embedding client, against a stub transport (WP10.1a).

No network and no key: every test here drives `OpenAIEmbedder` through an
`httpx.MockTransport`, which is what lets them assert on the **request** as well
as the response. B-040's rule applies with full force — a suite that can reach a
real provider is a suite that can spend real money, and embedding is the one path
in `knowledge/` that costs anything.

The test worth reading first is
``test_a_reordered_response_is_paired_by_index_not_by_arrival``. Every other
failure here is loud; that one is silent. If the provider returns vectors out of
order and the code trusts arrival order, every chunk gets somebody else's vector,
retrieval quietly returns nonsense, and nothing about the ingest looks wrong.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from dataagent.knowledge.embeddings import (
    EmbeddingError,
    OpenAIEmbedder,
)
from llm_fixture import build_settings

DIMENSIONS = 1536


def _vector(seed: float, size: int = DIMENSIONS) -> list[float]:
    return [seed] * size


def _settings(**overrides: Any):
    base = build_settings()
    return base.model_copy(
        update={
            "openai_api_key": base.openai_api_key,
            "embeddings_model": "text-embedding-3-small",
            "embeddings_dimensions": DIMENSIONS,
            "embeddings_provider": "openai",
            **overrides,
        }
    )


def _embedder(handler: Any, **overrides: Any) -> OpenAIEmbedder:
    transport = httpx.MockTransport(handler)
    return OpenAIEmbedder(
        api_key="test-key",
        client=httpx.AsyncClient(transport=transport, base_url="https://stub"),
        settings=_settings(**overrides),
    )


def _ok(vectors: list[list[float]], *, indices: list[int] | None = None, tokens: int = 7):
    order = indices if indices is not None else list(range(len(vectors)))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": vector}
                    for index, vector in zip(order, vectors, strict=True)
                ],
                "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
            },
        )

    return handler


async def test_one_vector_comes_back_per_text() -> None:
    embedder = _embedder(_ok([_vector(0.1), _vector(0.2)]))

    batch = await embedder.embed(["first", "second"])

    assert len(batch.vectors) == 2
    assert batch.vectors[0][0] == pytest.approx(0.1)
    assert batch.vectors[1][0] == pytest.approx(0.2)


async def test_a_reordered_response_is_paired_by_index_not_by_arrival() -> None:
    """**The silent failure this module is most exposed to.**

    The provider is free to return its rows in any order and says which is which
    in `index`. Trusting arrival order attaches every vector to the wrong text,
    retrieval returns nonsense, and nothing about the ingest looks wrong.
    """
    # Arrives second-then-first, and says so.
    embedder = _embedder(_ok([_vector(0.2), _vector(0.1)], indices=[1, 0]))

    batch = await embedder.embed(["first", "second"])

    assert batch.vectors[0][0] == pytest.approx(0.1), "the vector for 'first'"
    assert batch.vectors[1][0] == pytest.approx(0.2), "the vector for 'second'"


async def test_the_request_names_the_configured_model_and_sends_the_texts() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": _vector(0.5)}], "usage": {"prompt_tokens": 3}},
        )

    await _embedder(handler).embed(["net revenue"])

    assert seen["model"] == "text-embedding-3-small"
    assert seen["input"] == ["net revenue"]


async def test_a_wrong_width_is_refused_where_the_model_id_is_still_in_scope() -> None:
    """Rather than at the insert, which would report a column constraint and say
    nothing about which model caused it or what the deployment expects."""
    embedder = _embedder(_ok([_vector(0.1, size=1024)]))

    with pytest.raises(EmbeddingError) as caught:
        await embedder.embed(["anything"])

    message = str(caught.value)
    assert "1024" in message and "1536" in message
    assert "text-embedding-3-small" in message
    assert "migration" in message, "it has to say a config edit alone will not fix this"


async def test_a_non_numeric_component_loses_the_batch_rather_than_being_coerced() -> None:
    """The opposite call from a malformed usage count: that is a cosmetic loss,
    while an invented coordinate is a wrong answer to every future search."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": [{"index": 0, "embedding": ["not-a-number"] * DIMENSIONS}]}
        )

    with pytest.raises(EmbeddingError):
        await _embedder(handler).embed(["anything"])


async def test_a_rate_limit_is_retryable_and_a_bad_request_is_not() -> None:
    """The same distinction `llm/fallback.py` walks the chain on: a 429 may well
    succeed next time, a 400 is our bug and would be our bug at the next
    provider too."""

    def refuse(status: int):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={"error": {"message": "no"}})

        return handler

    with pytest.raises(EmbeddingError) as rate_limited:
        await _embedder(refuse(429)).embed(["x"])
    with pytest.raises(EmbeddingError) as bad_request:
        await _embedder(refuse(400)).embed(["x"])

    assert rate_limited.value.retryable is True
    assert bad_request.value.retryable is False


async def test_the_provider_message_reaches_the_error_and_is_truncated() -> None:
    """It goes to the ledger and the logs, so it is bounded (B-030)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "x" * 5000}})

    with pytest.raises(EmbeddingError) as caught:
        await _embedder(handler).embed(["x"])

    assert len(str(caught.value)) < 1000


async def test_an_empty_input_costs_no_request_at_all() -> None:
    """Ingest of a document that chunked to nothing must not bill for it."""
    called = False

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        nonlocal called
        called = True
        return httpx.Response(200, json={"data": []})

    batch = await _embedder(handler).embed([])

    assert batch.vectors == ()
    assert called is False


async def test_a_missing_model_id_refuses_before_any_call() -> None:
    """No default in code (D-017): a stale model id either 404s or returns
    vectors of a width the column will not accept."""
    with pytest.raises(EmbeddingError) as caught:
        OpenAIEmbedder(api_key="k", settings=_settings(embeddings_model=None))

    assert "EMBEDDINGS_MODEL" in str(caught.value)


async def test_a_missing_key_refuses_before_any_call() -> None:
    with pytest.raises(EmbeddingError) as caught:
        OpenAIEmbedder(settings=_settings(openai_api_key=None))

    assert "OPENAI_API_KEY" in str(caught.value)


async def test_usage_is_reported_and_marked_estimated_when_absent() -> None:
    """`estimated` is what keeps a ledger from silently mixing measured and
    guessed numbers, which is what every cost total rests on (WP6.1)."""

    def without_usage(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": _vector(0.1)}]})

    measured = await _embedder(_ok([_vector(0.1)], tokens=42)).embed(["x"])
    guessed = await _embedder(without_usage).embed(["x"])

    assert measured.usage.input_tokens == 42
    assert measured.usage.estimated is False
    assert guessed.usage.estimated is True
    # An embedding produces a vector, not text: folding anything into the output
    # column would make it mean something different here than everywhere else.
    assert measured.usage.output_tokens == 0


def test_the_configured_width_and_the_column_are_checked_against_each_other() -> None:
    """They are set in two places — a migration and a setting — and if they ever
    disagree every insert fails at runtime with a constraint error. This says so
    at startup instead, naming both numbers."""
    from dataagent.knowledge.embeddings import check_dimensions

    check_dimensions(_settings())

    with pytest.raises(EmbeddingError) as caught:
        check_dimensions(_settings(embeddings_dimensions=1024))

    assert "1024" in str(caught.value) and "1536" in str(caught.value)
