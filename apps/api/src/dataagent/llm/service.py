"""The LLM package's front door.

``complete`` is the whole of it. Everything above this module — the planner, the
critic, the composer, the eval harness — asks for a *role* and a *shape*, and
what happens in between is decided here:

* the registry resolves the ordered chain of models that may serve the role;
* the run's cost ceiling is checked **before** each call, not after;
* ``fallback.walk`` tries each provider, retrying what is worth retrying;
* the meter writes a ``usage_ledger`` row **before** the result is returned or
  the failure re-raised, so there is no path that spends tokens without leaving
  a row;
* structured output is parsed and, if it will not parse, repaired exactly once —
  and the repair is a second metered pass over the chain, because it is a second
  real cost.

This mirrors ``dal.run`` deliberately. The DAL's rule is that nothing reads
customer data except through its front door; this package's rule is that nothing
spends a customer's tokens except through this one. Both are enforced the same
way — by there being no other function that does the interesting part.

**The prompt is built per provider, inside the attempt.** Providers disagree
about whether they can constrain decoding to a schema, so the messages that go
to the second provider in a chain are not necessarily the ones that went to the
first. Building the request once, above the chain, would send one of them the
wrong prompt — a bug that only appears during an outage, which is the worst time
to find it.

What is deliberately *not* here: the run budget of architecture 4.4, which
counts iterations and queries across a whole investigation and belongs to the
Phase 8 controller. ``CallLimits`` bounds one call; ``llm/budget.py`` bounds one
run's spend; neither is that.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace

from pydantic import BaseModel

from dataagent.config import Settings, get_settings
from dataagent.llm import budget, fallback, meter, registry
from dataagent.llm.base import (
    CallLimits,
    Completion,
    LLMError,
    LLMProvider,
    LLMRequest,
    Message,
    Role,
    Tags,
    Usage,
)
from dataagent.llm.registry import ModelChoice
from dataagent.llm.retry import RetryPolicy
from dataagent.llm.structured import (
    StructuredOutputError,
    instruction_for,
    parse,
    repair_messages,
)

__all__ = ["complete"]

#: Builds the messages for one provider. A function rather than a list because
#: what to send depends on what the provider can do — see the module docstring.
PromptFor = Callable[[LLMProvider], list[Message]]


async def complete(
    *,
    role: Role,
    org_id: uuid.UUID,
    messages: Sequence[Message],
    schema: type[BaseModel] | None = None,
    run_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    limits: CallLimits | None = None,
    retry: RetryPolicy | None = None,
    settings: Settings | None = None,
) -> Completion:
    """Call the model configured for ``role``, meter it, and return what it said.

    Raises ``LLMError`` — ``StructuredOutputError`` when a schema was asked for
    and two attempts did not produce it, ``RunCostExceededError`` when the run
    has spent its ceiling — and has written a ledger row for every provider call
    made before it does.
    """
    resolved = settings if settings is not None else get_settings()
    chain = registry.resolve(role, resolved)
    tags = Tags(org_id=org_id, role=role, run_id=run_id, actor_user_id=actor_user_id)
    call_limits = limits if limits is not None else CallLimits()
    base = list(messages)

    def first_prompt(provider: LLMProvider) -> list[Message]:
        return _with_schema_instruction(base, schema, provider)

    def attempt_for(
        prompt_for: PromptFor, *, repaired: bool
    ) -> Callable[[ModelChoice], Awaitable[Completion]]:
        async def attempt(choice: ModelChoice) -> Completion:
            provider = registry.get_provider(choice.provider, resolved)
            # Before the call: a ceiling enforced afterwards is a report.
            await budget.assert_within_limit(
                org_id=org_id, run_id=run_id, model=choice.model, settings=resolved
            )
            request = LLMRequest(
                model=choice.model,
                messages=prompt_for(provider),
                tags=tags,
                schema=schema,
                limits=call_limits,
            )
            return await _call(provider, request, choice, resolved, repaired=repaired)

        return attempt

    completion = await fallback.walk(
        chain,
        attempt_for(first_prompt, repaired=False),
        policy=retry,
    )
    if schema is None:
        return completion

    try:
        return replace(completion, parsed=parse(schema, completion.text))
    except StructuredOutputError as first:
        # Bound to a local before the closure: Python deletes the `except ... as`
        # name at the end of the block, and this closure outlives the statement
        # that made it.
        problem = str(first)

        def repair_prompt(provider: LLMProvider) -> list[Message]:
            return repair_messages(
                first_prompt(provider),
                reply=completion.text,
                problem=problem,
                schema=schema,
            )

        second = await fallback.walk(
            chain,
            attempt_for(repair_prompt, repaired=True),
            policy=retry,
        )
        try:
            parsed = parse(schema, second.text)
        except StructuredOutputError as final:
            # Both attempts named, because the pair is the diagnosis: the same
            # complaint twice means the schema is wrong for the task, two
            # different ones mean the model is guessing.
            raise StructuredOutputError(
                f"{second.model} did not produce valid {schema.__name__} in two "
                f"attempts. First: {problem}. After repair: {final}."
            ) from None
        return replace(second, parsed=parsed, repaired=True)


async def _call(
    provider: LLMProvider,
    request: LLMRequest,
    choice: ModelChoice,
    settings: Settings,
    *,
    repaired: bool,
) -> Completion:
    """One provider call, timed and metered whichever way it ends."""
    started = time.monotonic()
    try:
        completion = await provider.complete(request)
    except LLMError as error:
        # Tokens are unknown on a failure — the provider did not report any — so
        # the row records zero of them and says what went wrong. It exists so
        # that "the model was called and it failed" is visible; a quota counts
        # what was actually spent, which here is nothing we can prove. With a
        # chain, this row beside a later `ok` row from another provider is what
        # makes a fallback visible instead of silently survived.
        await meter.record(
            org_id=request.tags.org_id,
            role=choice.role,
            tier=choice.tier,
            provider=choice.provider,
            model=choice.model,
            usage=Usage(),
            latency_ms=_elapsed_ms(started),
            run_id=request.tags.run_id,
            actor_user_id=request.tags.actor_user_id,
            repaired=repaired,
            error=str(error),
            settings=settings,
        )
        raise

    latency_ms = _elapsed_ms(started)
    await meter.record(
        org_id=request.tags.org_id,
        role=choice.role,
        tier=choice.tier,
        provider=choice.provider,
        model=completion.model,
        usage=completion.usage,
        latency_ms=latency_ms,
        run_id=request.tags.run_id,
        actor_user_id=request.tags.actor_user_id,
        repaired=repaired,
        settings=settings,
    )
    return replace(completion, latency_ms=latency_ms)


def _with_schema_instruction(
    messages: list[Message], schema: type[BaseModel] | None, provider: LLMProvider
) -> list[Message]:
    """Add the "reply with JSON like this" instruction, when it is needed.

    Placed after the leading system messages rather than at the front: the
    instruction layers of architecture 4.8 are ordered, L0 platform safety wins
    every conflict, and a formatting instruction must not displace it.
    """
    if schema is None or provider.capabilities().supports_response_schema:
        return messages
    boundary = next(
        (index for index, message in enumerate(messages) if message.role != "system"),
        len(messages),
    )
    return [*messages[:boundary], instruction_for(schema), *messages[boundary:]]


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))
