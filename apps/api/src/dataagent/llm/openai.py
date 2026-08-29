"""The OpenAI provider (DECISIONS D-017).

Deliberately thin, as ``llm/base.py`` requires: send, receive, report usage,
sanitize failures, and set ``retryable`` correctly. Retries are ``llm/retry.py``,
fallback is ``llm/fallback.py``, parsing is ``llm/structured.py``, metering is
``llm/service.py``. Everything this module decides for itself is a place the
second provider could come to disagree with it, so it decides as little as
possible.

Three mappings are all that is provider-specific here:

* **System messages become ``instructions``.** The Responses API takes
  system-level guidance as a top-level field rather than as a turn. Our
  messages are joined in order, so the instruction layering of architecture 4.8
  survives the trip — L0 stays above L5.
* **A schema becomes ``text.format``**, natively constrained. ``strict`` is
  requested only when the schema can carry it (see ``strict_safe``); the
  result is validated by ``llm/structured.py`` either way, so a provider that
  honours the schema and one that merely tries converge on the same guarantee.
* **Status codes become ``retryable``.** 429 and 5xx yes, everything else no.

**One honest caveat, recorded rather than hidden.** A failure's message is
included in the ``LLMError`` and therefore reaches the ``usage_ledger`` row and
the logs. Provider error text describes the *request* — a bad parameter, a
rate limit, an unknown model — rather than quoting the prompt back, so this is
safe in every case observed. It is an assumption about someone else's API, not
a guarantee this code can make, and it is the one path by which prompt content
could theoretically reach a log (**B-030**). The text is truncated for that
reason, not for tidiness.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, cast

import httpx

from dataagent.config import Settings, get_settings
from dataagent.llm.base import (
    Completion,
    LLMError,
    LLMRequest,
    ProviderCaps,
    Usage,
)

__all__ = ["OpenAIProvider", "strict_safe"]

BASE_URL = "https://api.openai.com/v1"
RESPONSES = "/responses"

#: How much provider error text is kept. Short because of the caveat above.
ERROR_TEXT_LIMIT = 300

#: Spelled out rather than taken from ``httpx.codes``, whose members type as
#: tuples under strict checking and read no more clearly than the numbers.
HTTP_OK = 200
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR = 500


class OpenAIProvider:
    """One HTTP client for the OpenAI Responses API.

    The client is built once and reused: a new connection per call would be a
    TLS handshake per call, which is why ``registry`` caches provider instances.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = BASE_URL,
        client: httpx.AsyncClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        resolved = settings if settings is not None else get_settings()
        key = api_key if api_key is not None else _key_from(resolved)
        self._client = client if client is not None else httpx.AsyncClient(base_url=base_url)
        self._headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    def capabilities(self) -> ProviderCaps:
        return ProviderCaps(
            name="openai",
            # Native JSON-schema constrained decoding. `llm/structured.py`
            # validates the result regardless — "supports JSON schema" means
            # different things on different APIs, and the type of
            # `Completion.parsed` must not depend on which provider answered.
            supports_response_schema=True,
            # We fold system messages into `instructions`, so the caller does
            # not have to know this API keeps them out of the turn list.
            supports_system_message=True,
            max_output_tokens=128_000,
        )

    async def complete(self, request: LLMRequest) -> Completion:
        payload = _body(request)
        try:
            response = await self._client.post(
                RESPONSES,
                headers=self._headers,
                json=payload,
                timeout=request.limits.timeout_seconds,
            )
        except httpx.TimeoutException:
            # Worth retrying and worth falling back on: the request may well
            # have been fine and the far end merely slow.
            raise LLMError("the provider did not respond in time", retryable=True) from None
        except httpx.HTTPError:
            # Deliberately not chained and deliberately not detailed: an httpx
            # error's repr can carry the full URL, and the same reasoning that
            # keeps connector errors bare applies here.
            raise LLMError("could not reach the provider", retryable=True) from None

        if response.status_code != HTTP_OK:
            raise _failure(response)

        return _completion(response.json(), request)

    async def aclose(self) -> None:
        await self._client.aclose()


def _key_from(settings: Settings) -> str:
    if settings.openai_api_key is None:
        raise LLMError(
            "OPENAI_API_KEY is not set, so the openai provider cannot be used. "
            "Set it in .env, or remove 'openai' from LLM_PROVIDERS.",
            retryable=False,
        )
    return settings.openai_api_key.get_secret_value()


def _body(request: LLMRequest) -> dict[str, Any]:
    """Our request, as this API spells it."""
    instructions = request.text_of("system")
    body: dict[str, Any] = {
        "model": request.model,
        "input": [
            {"role": message.role, "content": message.content}
            for message in request.messages
            if message.role != "system"
        ],
        "max_output_tokens": request.limits.max_output_tokens,
    }
    if instructions:
        body["instructions"] = instructions
    if request.schema is not None:
        schema = request.schema.model_json_schema()
        body["text"] = {
            "format": {
                "type": "json_schema",
                "name": request.schema.__name__,
                "schema": schema,
                "strict": strict_safe(schema),
            }
        }
    return body


def strict_safe(schema: dict[str, Any]) -> bool:
    """Whether this schema can be sent as ``strict`` without changing meaning.

    Strict mode requires every object to set ``additionalProperties: false`` and
    to list *every* property as required. A pydantic model with an optional
    field does neither, and rewriting it to comply would silently make that
    field mandatory — a change to the caller's contract, decided here, invisible
    at the call site. So the schema is sent as it is, and strictness is claimed
    only when it is already true. The non-strict path is not unguarded: the
    reply is validated, and repaired once, by ``llm/structured.py``.
    """
    definitions: list[dict[str, Any]] = [schema]
    defs: object = schema.get("$defs")
    if isinstance(defs, dict):
        for value in cast(dict[str, object], defs).values():
            if isinstance(value, dict):
                definitions.append(cast(dict[str, Any], value))

    for definition in definitions:
        if definition.get("type") != "object":
            continue
        if definition.get("additionalProperties") is not False:
            return False
        properties: object = definition.get("properties")
        required: object = definition.get("required")
        names: set[str] = (
            {str(key) for key in cast(dict[str, object], properties)}
            if isinstance(properties, dict)
            else set()
        )
        listed: set[str] = (
            {str(item) for item in cast(list[object], required)}
            if isinstance(required, list)
            else set()
        )
        if names != listed:
            return False
    return True


def _failure(response: httpx.Response) -> LLMError:
    """A non-200 as an ``LLMError`` with ``retryable`` set from the status."""
    status = response.status_code
    retryable = status == HTTP_TOO_MANY_REQUESTS or status >= HTTP_SERVER_ERROR
    detail = _error_detail(response)
    return LLMError(f"the provider returned {response.status_code}: {detail}", retryable=retryable)


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = cast(dict[str, Any], response.json())
    except (json.JSONDecodeError, ValueError):
        return response.text[:ERROR_TEXT_LIMIT] or "no detail"
    error = payload.get("error")
    if not isinstance(error, dict):
        return "no detail"
    parts = [
        str(cast(dict[str, Any], error).get(field))
        for field in ("type", "code")
        if cast(dict[str, Any], error).get(field)
    ]
    message = str(cast(dict[str, Any], error).get("message", ""))[:ERROR_TEXT_LIMIT]
    parts.append(message or "no detail")
    return " ".join(parts)


def _completion(payload: dict[str, Any], request: LLMRequest) -> Completion:
    """The response object, as our ``Completion``."""
    text, refusal = _output(payload)
    if refusal is not None:
        # A refusal is an answer, not a transport failure — the model declined.
        # Not retryable: the same request would be declined again, and the
        # fallback chain exists for outages, not for disagreements.
        raise LLMError(
            f"the model declined to answer: {refusal[:ERROR_TEXT_LIMIT]}", retryable=False
        )

    usage = cast(dict[str, Any], payload.get("usage", {}))
    return Completion(
        text=text,
        # What actually served the request, which can differ from what was
        # asked for — the ledger should record the former.
        model=str(payload.get("model") or request.model),
        provider="openai",
        usage=Usage(
            # `input_tokens` is inclusive of the cached ones; the details object
            # says how many of them there were. Read separately rather than
            # subtracted: the ledger stores what was sent and what was cached,
            # and nothing here decides what that is worth.
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            cached_input_tokens=_cached_input(usage),
            estimated=False,
        ),
        # Stamped by the front door, which is the only layer that sees the
        # whole call.
        latency_ms=0,
        finish_reason=_finish_reason(payload),
    )


def _cached_input(usage: dict[str, Any]) -> int | None:
    """How many input tokens the provider served from its prompt cache.

    None rather than 0 when the details object is absent or malformed, because
    the two are different claims: *the provider did not tell us* and *the
    provider told us none were cached*. Only the second is a measurement, and
    the ledger's column is read as a measurement.
    """
    details = usage.get("input_tokens_details")
    if not isinstance(details, dict):
        return None
    cached: object = cast(dict[str, Any], details).get("cached_tokens")
    if not isinstance(cached, int | float) or isinstance(cached, bool):
        return None
    return max(0, int(cached))


def _output(payload: dict[str, Any]) -> tuple[str, str | None]:
    """The assistant's text, and its refusal if it gave one.

    Output items other than ``message`` — reasoning summaries, tool calls — are
    skipped rather than concatenated: they are not the answer, and a caller that
    treated them as one would show internal text to a user.
    """
    chunks: list[str] = []
    for item in cast(Sequence[Any], payload.get("output", [])):
        if not isinstance(item, dict) or cast(dict[str, Any], item).get("type") != "message":
            continue
        for block in cast(Sequence[Any], cast(dict[str, Any], item).get("content", [])):
            if not isinstance(block, dict):
                continue
            typed = cast(dict[str, Any], block)
            if typed.get("type") == "refusal":
                return "", str(typed.get("refusal", "no reason given"))
            if typed.get("type") == "output_text":
                chunks.append(str(typed.get("text", "")))
    return "".join(chunks), None


def _finish_reason(payload: dict[str, Any]) -> str:
    """The provider's own word for why it stopped, normalised only where this
    API splits one concept across two fields."""
    if payload.get("status") == "incomplete":
        details = payload.get("incomplete_details")
        if isinstance(details, dict):
            return str(cast(dict[str, Any], details).get("reason", "incomplete"))
        return "incomplete"
    return str(payload.get("status", "completed"))
