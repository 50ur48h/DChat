"""What every LLM provider is, and what a call to one looks like.

Architecture Part 4.9. One protocol, a model registry, per-role assignment —
deliberately the smallest thing that makes replacing a provider a configuration
change rather than a rewrite.

Three shapes carry the whole design:

* **``Role``** is what the *caller* is doing — planning, writing SQL, criticising.
  Callers name a role and never a model, because a call site that names a model
  is a cost decision hidden in application code.
* **``Tier``** is how much model that job is worth. Roles map to tiers, tiers map
  to models, and both maps are configuration (``llm/registry.py``). This
  indirection is the single biggest cost lever in the product (architecture 8.3):
  moving ``observe`` from strong to small changes what a run costs without
  touching a line of agent code.
* **``Completion``** is what came back, and it carries what a budget will need to
  spend — tokens, model, latency. The BudgetState that does the spending is
  Phase 8; this only makes sure the numbers exist when it arrives.

Two things this module deliberately does *not* do. It does not parse structured
output — that is ``llm/structured.py``, so every provider inherits the same
parse-then-repair behaviour instead of each implementing its own. And it does not
meter — that is ``llm/service.py``, the front door, for the same reason the DAL
records in ``dal.run``: a provider that meters itself is a provider that can
forget to.

The model is untrusted (architecture 7.4). Nothing here is a security boundary
and nothing here should ever become one: what a completion is *allowed to cause*
is decided by the tool registry and the DAL, not by how convincing its text is.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel

__all__ = [
    "DEFAULT_ROLE_TIERS",
    "ROLES",
    "TIERS",
    "CallLimits",
    "Completion",
    "LLMError",
    "LLMProvider",
    "LLMRequest",
    "Message",
    "ProviderCaps",
    "Role",
    "Tags",
    "Tier",
    "Usage",
    "estimate_tokens",
]

#: The roles from architecture 4.9's ``MODEL_ROLES``, which are the stages of the
#: research loop in 4.4. Plan §6 Phase 6 paraphrases them as
#: ``planner/sql_author/critic/composer/cheap``; the architecture is the binding
#: document and its names survive into Phase 8, where each is literally a state
#: of the loop (DECISIONS D-018).
Role = Literal["intake", "observe", "plan", "sql", "critic", "compose", "embed"]

#: How much model a job is worth. Not model names: a tier outlives the model that
#: currently fills it, and every provider spells its own catalogue differently.
Tier = Literal["small", "mid", "strong", "embed"]

ROLES: tuple[Role, ...] = ("intake", "observe", "plan", "sql", "critic", "compose", "embed")
TIERS: tuple[Tier, ...] = ("small", "mid", "strong", "embed")

#: Architecture 4.9's table, verbatim. Configuration overrides it per deployment
#: (``LLM_ROLE_MAP``) and eventually per organization; this is the default that
#: makes a run cost what 8.3 says it should.
DEFAULT_ROLE_TIERS: dict[Role, Tier] = {
    "intake": "small",
    "observe": "small",
    "plan": "strong",
    "sql": "strong",
    "critic": "small",
    "compose": "mid",
    # The one entry that is not on 4.9's ladder, and says so by mapping to a
    # tier of its own (WP10.1a, revision 0017). Embeddings have no small/mid/
    # strong choice to make — there is one model, named by `EMBEDDINGS_MODEL` —
    # so filing them under `small` would put their tokens in the same bucket as
    # intake calls and make any spend-by-tier query wrong. It is here rather
    # than exempted from the mapping because
    # `test_every_role_resolves_to_the_tier_the_architecture_assigns` is a real
    # invariant: a role with no tier is a call nobody can price.
    "embed": "embed",
}


class LLMError(Exception):
    """A failure talking to a model provider, already sanitized.

    Providers raise only this, and — as ``ConnectorError`` does for customer
    databases — they do not chain the underlying exception: ``raise ... from
    error`` keeps the original in ``__cause__``, where the next traceback printed
    would put an API key or a request body into a log file.

    ``retryable`` is the whole reason this is one class with a flag rather than a
    hierarchy: WP6.2's fallback chain asks exactly one question of a failure —
    *is it worth trying the next provider* — and a 429 or a 503 answers yes while
    "your request was malformed" answers no.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class Message:
    """One turn of the conversation sent to a model.

    ``role`` here is the *message* role the chat APIs use, which is a different
    thing from this module's ``Role`` and is why the two never appear in the same
    function signature without qualification.
    """

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class CallLimits:
    """What one call may cost, in the small.

    Named after ``connectors.ExecLimits`` and for the same reason: there is no
    unbounded variant to reach for. This is a *per-call* bound and not the run
    budget from architecture 4.4 — that one counts iterations, queries and tokens
    across a whole investigation, it lives in the controller, and it is Phase 8's
    to build. Conflating them early would put budget arithmetic inside the
    provider, which is precisely where 4.4 says it must never live.
    """

    max_output_tokens: int = 1024
    #: Zero by default. Every role in this product is doing analysis, not prose
    #: generation, and a reproducible answer is worth more than a varied one —
    #: it is also what makes an eval suite mean anything (Phase 9).
    temperature: float = 0.0
    timeout_seconds: float = 60.0


@dataclass(frozen=True, slots=True)
class Tags:
    """Who this call belongs to (architecture 4.9's ``tags{org,run,role}``).

    Carried on the request so the meter and the provider see the same attribution
    rather than two call sites agreeing by habit. Providers may pass a
    non-identifying form of it upstream for abuse tracking; none of it is a
    secret, and none of it is a customer's data.
    """

    org_id: uuid.UUID
    role: Role
    run_id: uuid.UUID | None = None
    actor_user_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class Usage:
    """Tokens in and out, as the provider reported them.

    Providers that do not report usage estimate it (``estimate_tokens``) and say
    so with ``estimated=True``. A ledger row that silently mixes measured and
    guessed numbers would make every cost total quietly untrustworthy, and cost
    totals are the thing quotas are enforced from.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    #: How many of ``input_tokens`` came from the provider's prompt cache, or
    #: None where the provider does not say. **A subset of the input, not an
    #: addition to it** — providers report it that way and bill it at a discount
    #: we do not currently model (revision 0034). Recorded before it is priced,
    #: deliberately: the size of the gap should be an observation.
    cached_input_tokens: int | None = None
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class ProviderCaps:
    """Per-provider truth, stated rather than inferred.

    The same idea as ``connectors.Caps``: the layer above adapts to what a
    provider can actually do instead of guessing from its name.
    """

    name: str
    #: Whether the API can be *made* to return JSON matching a schema. When false,
    #: ``llm/structured.py`` renders the schema into the prompt and parses the
    #: reply — which is the path that needs the repair attempt.
    supports_response_schema: bool
    #: Anthropic takes the system prompt as a separate top-level parameter rather
    #: than as a message; providers that do fold it in for us set this true.
    supports_system_message: bool = True
    max_output_tokens: int = 4096
    #: True for anything that answers without a model behind it. A stub reaching
    #: production would not fail — it would fabricate, confidently, in a product
    #: whose entire claim is that its answers are evidenced. ``registry`` refuses
    #: to hand one out in a production build.
    is_stub: bool = False


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """One call, fully specified.

    ``model`` is on the request rather than on the provider because a provider is
    a client for an API, not for one model: the registry picks the model, and the
    same instance serves ``small`` and ``strong`` calls in the same run.
    """

    model: str
    messages: Sequence[Message]
    tags: Tags
    #: A pydantic model the reply must satisfy, or None for free text. Providers
    #: may use it natively; whether they do or not, ``llm/structured.py``
    #: validates the result, so a native structured call and a prompted one
    #: converge on the same guarantee.
    schema: type[BaseModel] | None = None
    limits: CallLimits = field(default_factory=CallLimits)

    def text_of(self, role: Literal["system", "user", "assistant"]) -> str:
        """Every message of one role, joined. Used for matching and assertions."""
        return "\n".join(message.content for message in self.messages if message.role == role)

    @property
    def prompt_text(self) -> str:
        """The whole conversation as one string. Never sent anywhere — this is
        for scripting a fake and for assertions about what a prompt contained."""
        return "\n".join(f"{message.role}: {message.content}" for message in self.messages)


@dataclass(frozen=True, slots=True)
class Completion:
    """What came back, plus what it cost.

    ``parsed`` is filled in by the front door, not by the provider, so that
    "structured output" means the same thing regardless of which API produced it.
    ``repaired`` records that it took two calls to get here — the honest signal
    for how often a model fails to follow a schema, which Phase 9 will want and
    which is invisible if the repair is silent.
    """

    text: str
    model: str
    provider: str
    usage: Usage
    #: Wall time for this one call. Providers may leave it at zero: the front
    #: door stamps its own measurement, because it is the only layer that sees
    #: the whole call — connection, transport and generation — and one number
    #: measured in one place beats two that disagree.
    latency_ms: int
    #: The provider's own word for why generation stopped: stop | length |
    #: content_filter | tool_use. Passed through rather than normalised, because
    #: a normalisation nobody consumes yet is a guess that gets copied.
    finish_reason: str = "stop"
    parsed: BaseModel | None = None
    repaired: bool = False

    def parsed_as[ModelT: BaseModel](self, schema: type[ModelT]) -> ModelT:
        """The parsed object, typed — or a failure that says what arrived instead.

        Callers ask for the schema they passed. Returning ``BaseModel`` and
        letting each call site cast would put an unchecked assumption at every
        one of them; this puts the check in one place.
        """
        if not isinstance(self.parsed, schema):
            raise LLMError(
                f"this completion carries no {schema.__name__}: "
                f"parsed is {type(self.parsed).__name__}. Pass schema= to the call "
                "that produced it."
            )
        return self.parsed


@runtime_checkable
class LLMProvider(Protocol):
    """One model API, spoken to in one shape.

    Implementations are thin on purpose: send, receive, report usage, sanitize
    failures. Retries and fallback are WP6.2's ``llm/fallback.py``, parsing is
    ``llm/structured.py``, metering is ``llm/service.py``. A provider that starts
    doing any of those has become a place where behaviour can differ between
    providers, which is the one thing this abstraction exists to prevent.
    """

    def capabilities(self) -> ProviderCaps: ...

    async def complete(self, request: LLMRequest) -> Completion:
        """Raises ``LLMError`` and nothing else."""
        ...

    async def aclose(self) -> None:
        """Release the HTTP client. Every caller owes a provider this."""
        ...


def estimate_tokens(text: str) -> int:
    """A rough token count for providers that do not report one.

    Four characters per token is the usual English approximation and is wrong for
    code, for JSON and for every non-Latin script — which is why anything counted
    this way is flagged ``estimated`` all the way to the ledger rather than being
    quietly totalled with measured figures.
    """
    return max(1, len(text) // 4) if text else 0
