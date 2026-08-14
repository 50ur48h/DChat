"""A model that answers from a script, and remembers everything it was asked.

This is the backbone of every agent test from Phase 7 and of the eval harness in
Phase 9, which makes it the most-used module in this package and the one whose
mistakes are hardest to see: a fake that is subtly non-deterministic turns a real
regression into "the suite is flaky", and a fake whose failures are unhelpful
turns a five-minute debug into an afternoon.

So two rules shape it.

**Determinism is structural, not a promise.** Nothing here reads a clock, a
random number or the environment. The same requests in the same order always
produce the same completions, and token counts come from the text rather than
from a provider's arithmetic. A script with no ``times`` limit answers the same
way however often it matches, so a retry cannot change an outcome.

**Recording is the important half of the API** — the scripting half only has to
be adequate. A test asserts on what the agent *asked*: that the SQL role saw the
table card, that the critic was given the draft, that nothing was sent twice,
that a repair carried the violation back. All of that reads ``calls``, so
``RecordedCall`` keeps the whole request rather than a summary of it.

**An unmatched request is a failure with an explanation.** The commonest way a
test goes wrong here is a prompt that changed shape, so no script matched. That
must not surface as a ``StopIteration`` or an empty string that fails three
assertions later: it raises, naming the role, the model, what the scripts were
looking for, and the tail of the prompt that arrived.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Self

from pydantic import BaseModel

from dataagent.llm.base import (
    ROLES,
    Completion,
    LLMError,
    LLMRequest,
    ProviderCaps,
    Role,
    Usage,
    estimate_tokens,
)

__all__ = ["FakeLLM", "NoScriptedResponseError", "RecordedCall", "Script"]

#: What a fake completion reports as its model when a test does not care.
STUB_MODEL = "fake-model"


class NoScriptedResponseError(LLMError):
    """Nothing in the script matched the request that arrived."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


@dataclass(frozen=True, slots=True)
class Script:
    """One scripted answer and the requests it applies to.

    A script with no ``role`` and no ``contains`` matches everything, which is
    the right default for a test that cares about one call. Both narrow it, and
    they narrow it by *and*: role first because it is what a reader of the test
    is thinking in, ``contains`` because prompt text is what distinguishes two
    calls of the same role.
    """

    #: The reply text. A callable receives the request, for the rare case where a
    #: fixed string cannot express the answer — it must stay a pure function of
    #: the request, or determinism goes with it.
    respond: str | Callable[[LLMRequest], str] = ""
    role: Role | None = None
    #: Matched against the whole prompt, case-sensitively. A substring rather than
    #: a regex: tests are read far more often than they are written, and a
    #: substring is the same thing to every reader.
    contains: str | None = None
    #: Raised instead of answering. For exercising retry, fallback and the
    #: refusal paths — a provider that only ever succeeds tests half the system.
    raises: LLMError | None = None
    #: How many times this script may match, or None for unlimited. Use it to
    #: script a sequence: first call fails to follow the schema, second obeys.
    times: int | None = None
    #: Overrides the token estimate, for tests about metering and cost.
    usage: Usage | None = None
    finish_reason: str = "stop"

    def matches(self, request: LLMRequest) -> bool:
        if self.role is not None and request.tags.role != self.role:
            return False
        return self.contains is None or self.contains in request.prompt_text


@dataclass(frozen=True, slots=True)
class RecordedCall:
    """One call, kept whole.

    The request rather than a summary: an assertion about a prompt is an
    assertion about the exact text a real provider would have received, and a
    summary is a second thing to keep in step with the first.
    """

    request: LLMRequest
    completion: Completion | None
    error: LLMError | None = None

    @property
    def role(self) -> Role:
        return self.request.tags.role

    @property
    def model(self) -> str:
        return self.request.model

    @property
    def prompt(self) -> str:
        return self.request.prompt_text

    @property
    def system_prompt(self) -> str:
        return self.request.text_of("system")

    @property
    def user_prompt(self) -> str:
        return self.request.text_of("user")


@dataclass(eq=False)
class FakeLLM:
    """A provider whose answers are written in advance.

    Satisfies ``LLMProvider`` in full, so anything that takes a provider takes
    this one — including the metered front door, which is how tests get real
    ``usage_ledger`` rows without a network.
    """

    scripts: list[Script] = field(default_factory=list[Script])
    #: Answered when no script matches. Left None on purpose: a fake that
    #: cheerfully answers anything hides the prompt change that broke the test.
    default: str | None = None
    model_name: str = STUB_MODEL

    _calls: list[RecordedCall] = field(default_factory=list[RecordedCall], init=False, repr=False)
    _used: dict[int, int] = field(default_factory=dict[int, int], init=False, repr=False)

    # -- scripting ---------------------------------------------------------

    def script(
        self,
        respond: str | Callable[[LLMRequest], str] = "",
        *,
        role: Role | None = None,
        contains: str | None = None,
        raises: LLMError | None = None,
        times: int | None = None,
        usage: Usage | None = None,
        finish_reason: str = "stop",
    ) -> Self:
        """Append one script and return self, so calls chain in a fixture."""
        self.scripts.append(
            Script(
                respond=respond,
                role=role,
                contains=contains,
                raises=raises,
                times=times,
                usage=usage,
                finish_reason=finish_reason,
            )
        )
        return self

    def script_json(
        self,
        payload: BaseModel | Mapping[str, object],
        *,
        role: Role | None = None,
        contains: str | None = None,
        times: int | None = None,
    ) -> Self:
        """Script a structured reply from an object rather than from a string.

        Keeps the schema honest in the test itself: passing the pydantic model
        the code expects means a test cannot script a shape the code could never
        accept, which is the most convincing way for a fake to lie.
        """
        text = (
            payload.model_dump_json()
            if isinstance(payload, BaseModel)
            else json.dumps(dict(payload), sort_keys=True)
        )
        return self.script(text, role=role, contains=contains, times=times)

    @classmethod
    def from_mapping(cls, entries: Sequence[Mapping[str, object]]) -> FakeLLM:
        """Build from plain data — a fixture file in whatever format the caller
        parsed. Deliberately format-agnostic: the eval harness of Phase 9 will
        keep its scripts in YAML beside its cases, and this package should not
        acquire a parser to accommodate that.
        """
        fake = cls()
        for entry in entries:
            respond = entry.get("respond", "")
            role = entry.get("role")
            contains = entry.get("contains")
            times = entry.get("times")
            if role is not None and role not in ROLES:
                raise ValueError(f"{role!r} is not an LLM role. Roles are {list(ROLES)}.")
            fake.script(
                respond if isinstance(respond, str) else json.dumps(respond, sort_keys=True),
                role=role,
                contains=contains if isinstance(contains, str) else None,
                times=times if isinstance(times, int) else None,
            )
        return fake

    def reset(self) -> None:
        """Forget every call and every use count. Scripts stay."""
        self._calls.clear()
        self._used.clear()

    # -- the provider protocol --------------------------------------------

    def capabilities(self) -> ProviderCaps:
        return ProviderCaps(
            name="fake",
            # False on purpose: the fake exercises the *harder* path, where the
            # schema is rendered into the prompt and the reply has to be parsed
            # and possibly repaired. A fake that claimed native structured output
            # would leave the repair path — the one that actually goes wrong —
            # untested in every suite that uses it.
            supports_response_schema=False,
            is_stub=True,
        )

    async def complete(self, request: LLMRequest) -> Completion:
        script = self._match(request)
        if script is None:
            error = NoScriptedResponseError(self._unmatched_message(request))
            self._calls.append(RecordedCall(request=request, completion=None, error=error))
            raise error

        if script.raises is not None:
            self._calls.append(RecordedCall(request=request, completion=None, error=script.raises))
            raise script.raises

        text = script.respond(request) if callable(script.respond) else script.respond
        completion = Completion(
            text=text,
            model=request.model or self.model_name,
            provider="fake",
            usage=script.usage
            or Usage(
                input_tokens=estimate_tokens(request.prompt_text),
                output_tokens=estimate_tokens(text),
                estimated=True,
            ),
            # Fixed, not measured: a duration that varies is a duration a test
            # cannot assert on, and this one never means anything anyway.
            latency_ms=0,
            finish_reason=script.finish_reason,
        )
        self._calls.append(RecordedCall(request=request, completion=completion))
        return completion

    async def aclose(self) -> None:
        """Nothing to close. Defined because the protocol says every caller owes
        a provider this, and a fake that does not accept it would let a caller
        forget."""

    # -- recording ---------------------------------------------------------

    @property
    def calls(self) -> tuple[RecordedCall, ...]:
        """Every call, in order — including the ones that raised."""
        return tuple(self._calls)

    def calls_for(self, role: Role) -> tuple[RecordedCall, ...]:
        return tuple(call for call in self._calls if call.role == role)

    def last_call(self, role: Role | None = None) -> RecordedCall:
        candidates = self._calls if role is None else list(self.calls_for(role))
        if not candidates:
            raise AssertionError(
                f"no call was made{'' if role is None else f' for role {role!r}'}. "
                f"Roles called: {sorted({call.role for call in self._calls}) or 'none'}"
            )
        return candidates[-1]

    def count(self, role: Role | None = None) -> int:
        return len(self._calls if role is None else self.calls_for(role))

    def prompts(self, role: Role | None = None) -> tuple[str, ...]:
        source = self._calls if role is None else list(self.calls_for(role))
        return tuple(call.prompt for call in source)

    # -- internals ---------------------------------------------------------

    def _match(self, request: LLMRequest) -> Script | None:
        for index, script in enumerate(self.scripts):
            if not script.matches(request):
                continue
            if script.times is not None and self._used.get(index, 0) >= script.times:
                continue
            self._used[index] = self._used.get(index, 0) + 1
            return script
        if self.default is not None:
            return Script(respond=self.default)
        return None

    def _unmatched_message(self, request: LLMRequest) -> str:
        """The message a developer reads when a prompt changed shape.

        It names what was asked and what was on offer, because "no scripted
        response" on its own sends the reader to the wrong file.
        """
        offers = (
            ", ".join(
                f"[role={script.role or 'any'} contains={script.contains!r}]"
                for script in self.scripts
            )
            or "none"
        )
        tail = request.prompt_text[-300:]
        return (
            f"FakeLLM has no scripted response for role={request.tags.role!r} "
            f"model={request.model!r}. Scripts tried: {offers}. "
            f"End of the prompt that arrived:\n…{tail}"
        )
