"""Which model does which job, and who supplies it (architecture Part 4.9, 8.3).

Two maps, both configuration, resolved in this order:

    role  ──LLM_ROLE_MAP──▶  tier  ──LLM_MODELS──▶  model, per provider

Nothing else in the application names a model. That is the point: architecture
8.3 calls model tiering the biggest cost lever in the product, and a lever is
only useful if it is in one place. Moving ``observe`` from ``strong`` to
``small`` is an environment variable; finding every call site that hard-coded a
model name is a refactor.

**Why models are configuration and not defaults in code.** Provider catalogues
change every few months, and a stale default is the worst kind: it either 404s
in production or silently bills for the wrong tier. So there is no built-in
model table — ``LLM_MODELS`` must name a model for every tier of every
configured provider, and resolution fails by naming the variable and the missing
tier rather than guessing.

The chain is ordered. ``resolve`` returns one choice per configured provider, in
configured order, which is the static per-role fallback chain of 4.9. WP6.2's
``llm/fallback.py`` walks it; until then the front door uses the first entry and
the rest are inert — but they are resolved and validated from day one, so a
misconfigured second provider is a startup-shaped problem rather than something
discovered during the first outage.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass

from dataagent.config import Settings, get_settings
from dataagent.llm.base import DEFAULT_ROLE_TIERS, TIERS, LLMError, LLMProvider, Role, Tier

__all__ = [
    "ModelChoice",
    "ProviderNotConfiguredError",
    "aclose_providers",
    "clear_provider_cache",
    "get_provider",
    "register_provider",
    "resolve",
    "tier_for",
]


class ProviderNotConfiguredError(LLMError):
    """Configuration cannot answer "which model, from whom".

    An ``LLMError`` because from a caller's point of view the call cannot be
    made, and never retryable: a second attempt reads the same configuration.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """One resolved answer to "what should serve this role"."""

    role: Role
    tier: Tier
    provider: str
    model: str


#: Providers this build ships. WP6.2 fills it with ``openai`` and ``anthropic``;
#: it is a table of *factories* rather than instances so that importing the
#: registry does not open an HTTP client, and so that a provider whose key is
#: absent fails when something tries to use it rather than at import time.
_BUILTIN_FACTORIES: dict[str, Callable[[], LLMProvider]] = {}

#: Registered at runtime — by tests, and by anything that wants to substitute a
#: provider without editing this module. Checked before the built-ins, so an
#: override wins.
_OVERRIDES: dict[str, Callable[[], LLMProvider]] = {}

#: One instance per provider name. A provider holds an HTTP connection pool, so
#: building one per call would be a new TLS handshake per LLM call.
_INSTANCES: dict[str, LLMProvider] = {}


def register_provider(name: str, factory: Callable[[], LLMProvider]) -> None:
    """Make ``name`` resolvable, replacing any earlier registration.

    Registering invalidates the cached instance, so a test that swaps a provider
    mid-run gets the new one rather than the one built before the swap.
    """
    _OVERRIDES[name] = factory
    _INSTANCES.pop(name, None)


def clear_provider_cache() -> None:
    """Drop cached instances and every runtime registration.

    Built-in providers survive: they are code, not registration state. Tests call
    this in teardown so a fake registered by one test cannot answer another's
    calls — which would be the exact kind of cross-test coupling that makes a
    deterministic harness stop being deterministic.
    """
    _OVERRIDES.clear()
    _INSTANCES.clear()


async def aclose_providers() -> None:
    """Close every built provider and forget it.

    For application shutdown: a provider holds an HTTP connection pool, and the
    protocol says every caller owes it an ``aclose``. Failures are ignored on
    purpose — a process on its way out has nothing useful to do with them.
    """
    for provider in list(_INSTANCES.values()):
        with suppress(Exception):
            await provider.aclose()
    _INSTANCES.clear()


def get_provider(name: str, settings: Settings | None = None) -> LLMProvider:
    """The provider registered under ``name``, built once and kept."""
    cached = _INSTANCES.get(name)
    if cached is not None:
        return cached

    factory = _OVERRIDES.get(name) or _BUILTIN_FACTORIES.get(name)
    if factory is None:
        known = sorted(set(_OVERRIDES) | set(_BUILTIN_FACTORIES))
        raise ProviderNotConfiguredError(
            f"no LLM provider named {name!r} is registered. "
            f"Registered: {known or 'none'}. Check LLM_PROVIDERS."
        )

    provider = factory()
    resolved = settings if settings is not None else get_settings()
    if provider.capabilities().is_stub and resolved.is_production:
        raise ProviderNotConfiguredError(
            f"{name!r} answers without a model behind it and is not permitted in a "
            "production build or environment: it would fabricate answers in a "
            "product whose whole claim is that answers are evidenced."
        )

    _INSTANCES[name] = provider
    return provider


def tier_for(role: Role, settings: Settings | None = None) -> Tier:
    """How much model this role is worth, after configuration has its say."""
    resolved = settings if settings is not None else get_settings()
    override = resolved.llm_role_map.get(role)
    if override is None:
        return DEFAULT_ROLE_TIERS[role]
    if override not in TIERS:
        raise ProviderNotConfiguredError(
            f"LLM_ROLE_MAP maps role {role!r} to {override!r}, which is not a tier. "
            f"Tiers are {list(TIERS)}."
        )
    return override


def resolve(role: Role, settings: Settings | None = None) -> tuple[ModelChoice, ...]:
    """The ordered chain of models that may serve ``role``.

    First entry is the primary. Later entries are the fallbacks WP6.2 walks on a
    retryable failure; they are resolved here so that a chain which cannot be
    satisfied is an error now rather than during the incident that needs it.
    """
    resolved = settings if settings is not None else get_settings()
    tier = tier_for(role, resolved)

    if not resolved.llm_providers:
        raise ProviderNotConfiguredError(
            "LLM_PROVIDERS is empty, so no model can serve any role. Set it to an "
            "ordered, comma-separated list — the first is primary, the rest are "
            "fallbacks."
        )

    chain: list[ModelChoice] = []
    for provider in resolved.llm_providers:
        models = resolved.llm_models.get(provider)
        if not models:
            raise ProviderNotConfiguredError(
                f"LLM_MODELS names no models for provider {provider!r}. It must map "
                f"each configured provider to a model per tier: {list(TIERS)}."
            )
        model = models.get(tier)
        if not model:
            raise ProviderNotConfiguredError(
                f"LLM_MODELS has no {tier!r} model for provider {provider!r}, which "
                f"role {role!r} needs. Model ids are deployment configuration; this "
                "build ships none, deliberately, so that a stale default cannot "
                "bill for the wrong tier."
            )
        chain.append(ModelChoice(role=role, tier=tier, provider=provider, model=model))
    return tuple(chain)
