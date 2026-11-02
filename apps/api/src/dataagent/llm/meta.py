"""The Meta Model API provider (DECISIONS D-069).

Meta serves its Muse models — ``muse-wonder-1.0``, ``muse-spark-1.3`` — behind
the Responses protocol OpenAI defined: the same request body, the same
``text.format`` JSON schema with the same strict subset, the same ``output_text``
items and the same ``usage`` counts, including the cached share D-067 records.
So this is not a second adapter. It is ``OpenAIProvider`` pointed at Meta's
endpoint with Meta's key, under its own name — because the ledger records who
answered, and a Meta call recorded as ``openai`` would put every cost and every
failure on the wrong bill.

Two things differ from the OpenAI account, and both are deliberate. **No
embeddings**: the Model API has no embedding model, so ``EMBEDDINGS_PROVIDER`` is
untouched by choosing ``meta`` for chat. **A reasoning effort on every call**
(``META_REASONING_EFFORT``, default ``minimal``). Reasoning tokens count against
``max_output_tokens``, and the caps this product sets per call were sized for
OpenAI's models. Measured on the owner's key: with no effort sent, Muse Wonder
spent 798 of an 800-token cap and 1,022 of 1,024 reasoning and returned no text
at all; at ``minimal`` the same two calls completed in 249 and 167 tokens.
"""

from __future__ import annotations

import httpx

from dataagent.config import Settings, get_settings
from dataagent.llm.base import LLMError
from dataagent.llm.openai import OpenAIProvider

__all__ = ["BASE_URL", "MetaProvider"]

#: Meta's documented endpoint for the Responses, Chat Completions and Messages
#: protocols alike (dev.meta.ai/docs/protocols/responses).
BASE_URL = "https://api.meta.ai/v1"


class MetaProvider(OpenAIProvider):
    """The Responses client, aimed at the Meta Model API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = BASE_URL,
        client: httpx.AsyncClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        resolved = settings if settings is not None else get_settings()
        super().__init__(
            api_key=api_key if api_key is not None else _key_from(resolved),
            base_url=base_url,
            client=client,
            settings=resolved,
            name="meta",
            reasoning_effort=resolved.meta_reasoning_effort,
        )


def _key_from(settings: Settings) -> str:
    if settings.meta_api_key is None:
        raise LLMError(
            "META_API_KEY is not set, so the meta provider cannot be used. "
            "Set it in .env, or remove 'meta' from LLM_PROVIDERS.",
            retryable=False,
        )
    return settings.meta_api_key.get_secret_value()
