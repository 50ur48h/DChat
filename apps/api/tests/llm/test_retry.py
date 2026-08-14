"""Retrying, without a clock and without a random number.

``with_retries`` takes ``sleep`` and ``jitter`` as parameters precisely so this
file can exercise the real backoff arithmetic deterministically. A retry policy
tested against ``time.sleep`` is one that never runs in CI, which is how they
come to be wrong.
"""

from __future__ import annotations

import pytest

from dataagent.llm.base import LLMError
from dataagent.llm.retry import RetryPolicy, with_retries


class Recorder:
    """A sleep that records instead of waiting."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


async def test_a_call_that_succeeds_is_not_retried() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    sleep = Recorder()
    assert await with_retries(operation, sleep=sleep, jitter=lambda: 0.0) == "ok"
    assert calls == 1
    assert sleep.waits == []


async def test_a_retryable_failure_is_retried_up_to_the_policy() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise LLMError("429", retryable=True)
        return "ok"

    sleep = Recorder()
    result = await with_retries(
        operation, policy=RetryPolicy(attempts=3), sleep=sleep, jitter=lambda: 0.0
    )

    assert result == "ok"
    assert calls == 3
    assert len(sleep.waits) == 2


async def test_a_non_retryable_failure_is_raised_at_once() -> None:
    """A 400 is a request this code built wrongly. Three attempts is three times
    the latency and the same error."""
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise LLMError("bad request", retryable=False)

    sleep = Recorder()
    with pytest.raises(LLMError, match="bad request"):
        await with_retries(operation, sleep=sleep, jitter=lambda: 0.0)

    assert calls == 1
    assert sleep.waits == []


async def test_exhausting_the_attempts_raises_the_last_failure() -> None:
    """The last one, not the first: it is the error that actually ended the
    sequence, and it is what the fallback above sees."""
    attempt = 0

    async def operation() -> str:
        nonlocal attempt
        attempt += 1
        raise LLMError(f"failure {attempt}", retryable=True)

    with pytest.raises(LLMError, match="failure 3"):
        await with_retries(
            operation, policy=RetryPolicy(attempts=3), sleep=Recorder(), jitter=lambda: 0.0
        )


async def test_backoff_grows_and_is_capped() -> None:
    async def operation() -> str:
        raise LLMError("still busy", retryable=True)

    sleep = Recorder()
    with pytest.raises(LLMError):
        await with_retries(
            operation,
            policy=RetryPolicy(attempts=5, base_delay=1.0, max_delay=4.0),
            sleep=sleep,
            jitter=lambda: 0.5,
        )

    # jitter of 0.5 makes the factor exactly 1.0, so these are the raw delays:
    # 1, 2, 4, then capped at 4.
    assert sleep.waits == [1.0, 2.0, 4.0, 4.0]


def test_jitter_spreads_the_wait_around_the_exponential() -> None:
    """Full-width jitter, because the failure being guarded against is every
    caller in a fleet retrying in lockstep after one shared 429."""
    policy = RetryPolicy(base_delay=2.0)

    assert policy.delay_for(1, jitter=0.0) == 1.0
    assert policy.delay_for(1, jitter=0.5) == 2.0
    assert policy.delay_for(1, jitter=0.999) == pytest.approx(2.998)


def test_a_policy_that_never_calls_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        RetryPolicy(attempts=0)
