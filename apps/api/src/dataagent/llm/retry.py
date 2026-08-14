"""Trying again, the same way for every provider (architecture Part 8.5).

8.5 gives LLM provider errors one line: *retry three times on 429/5xx, then static
fallback model; run continues*. This module is the first half, and it lives
outside the providers so the second provider cannot quietly disagree with the
first about what "retry" means — the same reason ``structured`` and ``meter``
are not per-provider either.

Two things it is careful about.

**Only retryable failures are retried.** ``LLMError.retryable`` is set by the
provider from the status code, and that flag is the whole decision. A 400 is a
request this code built wrongly; trying it again three times is three times the
latency and the same error.

**Jitter is injected, not sampled.** ``sleep`` and ``jitter`` are parameters so
a test can run the real backoff logic with neither a clock nor a random number
in it. A retry policy tested with ``time.sleep`` is a retry policy nobody runs
in CI, which is how they come to be wrong.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

from dataagent.llm.base import LLMError

__all__ = ["RetryPolicy", "with_retries"]


class RetryPolicy:
    """How many times, and how long to wait (architecture 8.5's three attempts).

    ``attempts`` counts the *total* calls, so 3 means one try and two retries.
    Delay is exponential from ``base_delay``, capped at ``max_delay``, and each
    wait is multiplied by a jitter factor in [0.5, 1.5] — full-width jitter,
    because the failure this guards against is every caller in a fleet retrying
    in lockstep after a shared 429.
    """

    __slots__ = ("attempts", "base_delay", "max_delay")

    def __init__(self, attempts: int = 3, base_delay: float = 0.5, max_delay: float = 8.0) -> None:
        if attempts < 1:
            raise ValueError("attempts must be at least 1: a policy that never calls is not one")
        self.attempts = attempts
        self.base_delay = base_delay
        self.max_delay = max_delay

    def delay_for(self, attempt: int, jitter: float) -> float:
        """Seconds to wait before attempt ``attempt`` (1-based, so never called
        with 1). ``jitter`` is a draw in [0, 1) scaled to [0.5, 1.5)."""
        exponential = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        return exponential * (0.5 + jitter)


async def with_retries[T](
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    jitter: Callable[[], float] = random.random,
) -> T:
    """Run ``operation``, retrying only what the provider marked retryable.

    Re-raises the *last* failure when the attempts run out, so the caller — and
    ``llm/fallback.py`` above it — sees the error that actually ended the
    sequence rather than the first one.
    """
    resolved = policy if policy is not None else RetryPolicy()
    last: LLMError | None = None

    for attempt in range(1, resolved.attempts + 1):
        try:
            return await operation()
        except LLMError as error:
            if not error.retryable:
                raise
            last = error
            if attempt < resolved.attempts:
                await sleep(resolved.delay_for(attempt, jitter()))

    # Unreachable unless attempts < 1, which the constructor refuses.
    assert last is not None  # noqa: S101 — narrowing for the type checker
    raise last
