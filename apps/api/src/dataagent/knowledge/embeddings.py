"""Turning text into vectors, and writing down what that cost (architecture 5.5).

The one place in this package that spends money, so it is the one place that has
to obey WP6.1's rule: **no path spends tokens without writing a `usage_ledger`
row.** Embedding a corpus costs more than the chat calls that later answer
questions about it, and a spend outside the ledger makes *"what did this
organization spend"* unanswerable from the one table built to answer it.

Four things are deliberate.

**Batched, and the batch is a ceiling rather than a target.** One request per
chunk is a round trip and a rate-limit slot each; one request for a whole book is
a request that times out and loses everything in it. `EMBEDDINGS_BATCH` bounds it
at both ends, and a failed batch loses one batch.

**The width is checked on the way in, not trusted.** A provider that returns
1024 floats for a `vector(1536)` column produces an insert error deep inside
ingest, pointing at a constraint rather than at the model id that caused it. So
the first response is measured against `EMBEDDINGS_DIMENSIONS` and a mismatch
raises here, naming both numbers and the model.

**Order is a contract.** The provider returns one vector per input and the caller
pairs them with chunks positionally, so a reordered response would attach every
vector to the wrong text — silently, and in a way no test of "did it embed"
would catch. The response is sorted by its own `index` field rather than assumed
to arrive in order.

**Metered under its own role and tier** (`embed`/`embed`, revision 0017), never
under `small`. D-018's ladder does not apply: there is one embedding model, and
filing its tokens beside intake calls would make any spend-by-tier query wrong.

This module deliberately does **not** go through `llm/service.py`. That front
door resolves a role to a tier to a chat model, repairs structured output and
runs the fallback chain — none of which an embedding call has or wants. What it
shares is the part that matters: the same key, the same settings, and the same
ledger.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

import httpx

from dataagent.config import Settings, get_settings
from dataagent.db.models import EMBEDDING_DIMENSIONS
from dataagent.llm import meter
from dataagent.llm.base import LLMError, Usage

__all__ = ["Embedder", "EmbeddingError", "OpenAIEmbedder", "embed_texts"]

#: What `response.json()` actually hands back. Named rather than spelled out at
#: each use, so the casts below read as "this is provider JSON, and nothing here
#: may assume its shape" rather than as type noise.
JsonObject = dict[str, Any]

BASE_URL = "https://api.openai.com/v1"
EMBEDDINGS = "/embeddings"

#: Long enough for a full batch of chunks, short enough that a hung provider
#: does not hold an ingest open indefinitely.
TIMEOUT_SECONDS = 60.0

HTTP_OK = 200
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR = 500

#: Truncated for B-030's reason: provider error text describes the *request*, but
#: that is an assumption about somebody else's API rather than a guarantee, and
#: this text reaches the ledger and the logs.
ERROR_TEXT_LIMIT = 300


class EmbeddingError(LLMError):
    """An embedding call failed, already sanitized.

    A subclass of `LLMError` so a caller that already handles provider failure
    handles this too, and so `retryable` means the same thing it means
    everywhere else.
    """


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    """What one provider call returned, and what it cost."""

    vectors: tuple[tuple[float, ...], ...]
    usage: Usage
    model: str


class Embedder(Protocol):
    """What ingest needs from a provider. Deliberately one method.

    A **structural** protocol rather than a base class, and narrow on purpose:
    the stub used in tests satisfies it by having the method, not by inheriting
    from it, so nothing in the test suite can accidentally acquire real
    behaviour from a shared parent. That is **B-040**'s lesson applied to the
    one path in `knowledge/` that spends money — a suite that can reach a real
    provider is a suite that can spend it.
    """

    async def embed(self, texts: Sequence[str]) -> EmbeddingBatch:  # pragma: no cover - protocol
        ...


class OpenAIEmbedder:
    """The OpenAI embeddings endpoint, and nothing more.

    Thin for the same reason `llm/openai.py` is thin: everything a second
    provider could disagree about lives outside it.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str = BASE_URL,
        client: httpx.AsyncClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        resolved = settings if settings is not None else get_settings()
        key = api_key if api_key is not None else _key_from(resolved)
        chosen = model if model is not None else resolved.embeddings_model
        if not chosen:
            # No default in code, for D-017's reason: a model id compiled into a
            # release is stale within months, and a stale one either 404s or
            # returns vectors of a width the column will not accept.
            raise EmbeddingError(
                "EMBEDDINGS_MODEL is not set, so nothing can be embedded. "
                "Set it to a model this key may call — verify with GET /v1/models."
            )
        self._model = chosen
        self._expected = resolved.embeddings_dimensions
        self._client = client if client is not None else httpx.AsyncClient(base_url=base_url)
        self._headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    @property
    def model(self) -> str:
        return self._model

    async def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        if not texts:
            return EmbeddingBatch(vectors=(), usage=Usage(), model=self._model)

        try:
            response = await self._client.post(
                EMBEDDINGS,
                headers=self._headers,
                json={"model": self._model, "input": list(texts)},
                timeout=TIMEOUT_SECONDS,
            )
        except httpx.TimeoutException:
            raise EmbeddingError(
                "the embedding provider did not respond in time", retryable=True
            ) from None
        except httpx.HTTPError:
            # Not chained and not detailed, for the reason `llm/openai.py` gives:
            # an httpx error's repr can carry the full URL.
            raise EmbeddingError("could not reach the embedding provider", retryable=True) from None

        if response.status_code != HTTP_OK:
            raise _failure(response)

        return self._batch(cast(JsonObject, response.json()))

    def _batch(self, body: JsonObject) -> EmbeddingBatch:
        raw: object = body.get("data")
        if not isinstance(raw, list):
            raise EmbeddingError("the embedding provider returned no data")

        # **Sorted by the provider's own index**, never assumed to be in order.
        # A reordered response would attach every vector to the wrong text,
        # silently, and no test of "did it embed" would catch it.
        rows: list[JsonObject] = [
            cast(JsonObject, row) for row in cast(list[object], raw) if isinstance(row, dict)
        ]
        rows.sort(key=lambda row: _as_int(row.get("index")))

        vectors: list[tuple[float, ...]] = []
        for row in rows:
            embedding: object = row.get("embedding")
            if not isinstance(embedding, list):
                raise EmbeddingError("the embedding provider returned a row with no vector")
            vector = tuple(_as_float(value) for value in cast(list[object], embedding))
            # Checked here, where the model id is in scope, rather than at the
            # insert — which would report a column constraint and say nothing
            # about why.
            if len(vector) != self._expected:
                raise EmbeddingError(
                    f"{self._model} returned {len(vector)} dimensions but this deployment "
                    f"stores {self._expected}. The column's width is fixed by a migration, "
                    "so a different model needs one — not only a configuration change."
                )
            vectors.append(vector)

        usage = body.get("usage")
        tokens = 0
        if isinstance(usage, dict):
            reported = cast(JsonObject, usage)
            tokens = _as_int(reported.get("prompt_tokens")) or _as_int(reported.get("total_tokens"))
        return EmbeddingBatch(
            vectors=tuple(vectors),
            # No output tokens: an embedding produces a vector, not text. Left at
            # zero rather than folded into the input count, so the ledger's two
            # columns keep meaning what they mean for every other row.
            usage=Usage(input_tokens=tokens, output_tokens=0, estimated=tokens == 0),
            model=self._model,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


async def embed_texts(
    *,
    org_id: uuid.UUID,
    texts: Sequence[str],
    embedder: Embedder,
    actor_user_id: uuid.UUID | None = None,
    settings: Settings | None = None,
) -> list[tuple[float, ...]]:
    """Embed everything, in batches, and meter every call including the failures.

    Returns one vector per input, in input order — the contract ingest pairs
    chunks against.

    **A failing batch is metered before it raises**, which is the same rule
    `llm/service.py` follows: a provider that died mid-generation has still
    consumed tokens, and a ledger that only records successes understates the
    bill exactly when someone is trying to find out why it is high.
    """
    resolved = settings if settings is not None else get_settings()
    size = max(1, resolved.embeddings_batch)
    model = getattr(embedder, "model", resolved.embeddings_model or "unknown")

    out: list[tuple[float, ...]] = []
    for start in range(0, len(texts), size):
        window = list(texts[start : start + size])
        began = time.perf_counter()
        try:
            batch = await embedder.embed(window)
        except LLMError as error:
            await meter.record(
                org_id=org_id,
                role="embed",
                tier="embed",
                provider=resolved.embeddings_provider,
                model=str(model),
                usage=Usage(),
                latency_ms=_elapsed(began),
                actor_user_id=actor_user_id,
                error=str(error)[:ERROR_TEXT_LIMIT],
                settings=resolved,
            )
            raise
        await meter.record(
            org_id=org_id,
            role="embed",
            tier="embed",
            provider=resolved.embeddings_provider,
            model=batch.model,
            usage=batch.usage,
            latency_ms=_elapsed(began),
            actor_user_id=actor_user_id,
            settings=resolved,
        )
        if len(batch.vectors) != len(window):
            raise EmbeddingError(
                f"asked for {len(window)} embeddings and got {len(batch.vectors)}; "
                "pairing them with the text would attach vectors to the wrong chunks"
            )
        out.extend(batch.vectors)
    return out


def _as_int(value: object) -> int:
    """A provider's number, or zero.

    Never raises: a usage block arriving in an unexpected shape should cost the
    ledger its precision, not the caller its embeddings. The row still gets
    written, with `estimated` set, which is the distinction WP6.1 built that flag
    for.
    """
    return int(value) if isinstance(value, int | float) else 0


def _as_float(value: object) -> float:
    """One component of a vector, and this one *does* raise.

    The opposite call from `_as_int`, deliberately: a malformed usage count is a
    cosmetic loss, while a malformed vector is a wrong answer to every future
    search that touches it. Better to lose the batch than to store a coordinate
    somebody invented.
    """
    if not isinstance(value, int | float):
        raise EmbeddingError("the embedding provider returned a non-numeric vector component")
    return float(value)


def _elapsed(began: float) -> int:
    return max(0, int((time.perf_counter() - began) * 1000))


def _key_from(settings: Settings) -> str:
    key = settings.openai_api_key
    if key is None:
        raise EmbeddingError("OPENAI_API_KEY is not set, so nothing can be embedded")
    return key.get_secret_value()


def _failure(response: httpx.Response) -> EmbeddingError:
    retryable = response.status_code == HTTP_TOO_MANY_REQUESTS or (
        response.status_code >= HTTP_SERVER_ERROR
    )
    return EmbeddingError(
        f"the embedding provider refused the request ({response.status_code}): {_detail(response)}",
        retryable=retryable,
    )


def _detail(response: httpx.Response) -> str:
    """The provider's own words about what it refused, truncated.

    Everything here is `object` until proven otherwise: this text reaches the
    ledger and the logs, so the one thing it must not do is raise on the way to
    describing a failure.
    """
    try:
        body = cast(object, response.json())
    except ValueError:
        return response.text[:ERROR_TEXT_LIMIT]
    if isinstance(body, dict):
        error: object = cast(JsonObject, body).get("error")
        if isinstance(error, dict):
            message: object = cast(JsonObject, error).get("message")
            if message is not None:
                return str(message)[:ERROR_TEXT_LIMIT]
    # Falls back to the raw body rather than to a re-serialised parse of it: the
    # text is what the provider actually sent, and re-encoding a half-understood
    # structure to describe a failure is a second thing that can go wrong while
    # the first is being reported.
    return response.text[:ERROR_TEXT_LIMIT]


#: A last guard against the two constants drifting. The column's width is set by
#: a migration and the setting is what the provider is checked against; if they
#: ever disagree, every insert fails at runtime with a constraint error. Asserted
#: at import so the process refuses to start instead.
def check_dimensions(settings: Settings | None = None) -> None:
    """Raise if configuration and the column disagree about the vector's width."""
    resolved = settings if settings is not None else get_settings()
    if resolved.embeddings_dimensions != EMBEDDING_DIMENSIONS:
        raise EmbeddingError(
            f"EMBEDDINGS_DIMENSIONS is {resolved.embeddings_dimensions} but "
            f"knowledge_chunks.embedding is vector({EMBEDDING_DIMENSIONS}). "
            "One of them is wrong, and every insert would fail."
        )
