"""Walking the provider chain (architecture Part 4.9, 8.5).

``registry.resolve`` returns an ordered chain — primary first, then whatever
configuration named after it. This module decides when to move down it.

The rule is one line, and the whole design of ``LLMError.retryable`` exists to
make it that short: **move on only when the failure was worth retrying.** A 429
or a 503 says this provider is busy or broken and another might not be. A 400
says the request is wrong, and it will be just as wrong at the next provider —
walking the chain there would turn one clear error into three, spend money on
two of them, and bury the cause.

Metering stays above this module. ``walk`` takes an ``attempt`` callback and
calls it once per try, so ``llm/service.py`` writes a ``usage_ledger`` row for
every attempt including the ones that failed. That is what makes a fallback
visible afterwards: an ``error`` row from one provider followed by an ``ok`` row
from another, rather than a single success that quietly hides which model
answered.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Sequence
from functools import partial

from dataagent.llm.base import Completion, LLMError
from dataagent.llm.registry import ModelChoice
from dataagent.llm.retry import RetryPolicy, with_retries

__all__ = ["walk"]


async def walk(
    chain: Sequence[ModelChoice],
    attempt: Callable[[ModelChoice], Awaitable[Completion]],
    *,
    policy: RetryPolicy | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    jitter: Callable[[], float] = random.random,
) -> Completion:
    """The first completion the chain can produce.

    Raises the last failure when the chain is exhausted, and raises immediately
    — without touching the rest of the chain — on anything not retryable.
    """
    if not chain:
        raise LLMError("no model chain to call: resolve returned nothing", retryable=False)

    last: LLMError | None = None
    for choice in chain:
        try:
            return await with_retries(
                partial(attempt, choice), policy=policy, sleep=sleep, jitter=jitter
            )
        except LLMError as error:
            if not error.retryable:
                raise
            last = error

    assert last is not None  # noqa: S101 — the loop ran at least once
    raise last
