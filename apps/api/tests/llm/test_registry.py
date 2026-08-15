"""Which model serves which role (architecture Part 4.9, 8.3).

The registry is where a run's cost is decided, so the tests are about the two
things that decide it — the role→tier map and the tier→model map — and about
what happens when configuration cannot answer.
"""

from __future__ import annotations

import pytest

from dataagent.config import Settings
from dataagent.db import models
from dataagent.llm import registry
from dataagent.llm.base import DEFAULT_ROLE_TIERS, ROLES, TIERS, ProviderCaps
from dataagent.llm.fake import FakeLLM
from dataagent.llm.registry import ProviderNotConfiguredError
from llm_fixture import FAKE_MODELS


def test_every_role_resolves_to_the_tier_the_architecture_assigns(llm_settings: Settings) -> None:
    """Architecture 4.9's table is the default, unedited.

    Asserted role by role rather than as one dict comparison so a failure names
    the role that moved — which is the thing a reviewer needs to see, because
    moving a role between tiers is a cost decision.
    """
    for role in ROLES:
        assert registry.tier_for(role, llm_settings) == DEFAULT_ROLE_TIERS[role]


def test_the_role_map_moves_one_role_without_touching_the_others(llm_settings: Settings) -> None:
    """The cost lever of 8.3: one environment variable, one role."""
    settings = llm_settings.model_copy(update={"llm_role_map": {"compose": "small"}})

    assert registry.tier_for("compose", settings) == "small"
    assert registry.tier_for("plan", settings) == "strong"


def test_a_role_mapped_to_something_that_is_not_a_tier_is_refused(llm_settings: Settings) -> None:
    settings = llm_settings.model_copy(update={"llm_role_map": {"plan": "enormous"}})

    with pytest.raises(ProviderNotConfiguredError, match="not a tier"):
        registry.tier_for("plan", settings)


def test_resolution_names_the_model_for_the_role_s_tier(llm_settings: Settings) -> None:
    assert registry.resolve("plan", llm_settings)[0].model == "fake-strong"
    assert registry.resolve("intake", llm_settings)[0].model == "fake-small"
    assert registry.resolve("compose", llm_settings)[0].model == "fake-mid"


def test_the_chain_follows_the_configured_provider_order(llm_settings: Settings) -> None:
    """``resolve`` returns the fallback chain of 4.9, primary first.

    WP6.2 walks the tail. Resolving it now means a second provider that is
    misconfigured is found while someone is looking, not during the outage that
    first needs it.
    """
    settings = llm_settings.model_copy(
        update={
            "llm_providers": ("fake", "other"),
            "llm_models": {**FAKE_MODELS, "other": {"small": "o-s", "mid": "o-m", "strong": "o-b"}},
        }
    )

    chain = registry.resolve("sql", settings)

    assert [choice.provider for choice in chain] == ["fake", "other"]
    assert [choice.model for choice in chain] == ["fake-strong", "o-b"]


def test_a_provider_with_no_model_for_the_tier_fails_by_naming_both(
    llm_settings: Settings,
) -> None:
    """No built-in model table, so this is the everyday misconfiguration.

    The message has to name the provider *and* the tier, because "LLM_MODELS is
    wrong" sends the reader to a JSON blob with no idea what is missing.
    """
    settings = llm_settings.model_copy(update={"llm_models": {"fake": {"small": "fake-small"}}})

    with pytest.raises(ProviderNotConfiguredError) as raised:
        registry.resolve("plan", settings)

    assert "strong" in str(raised.value)
    assert "fake" in str(raised.value)


def test_no_configured_provider_is_an_error_rather_than_a_default(llm_settings: Settings) -> None:
    settings = llm_settings.model_copy(update={"llm_providers": ()})

    with pytest.raises(ProviderNotConfiguredError, match="LLM_PROVIDERS"):
        registry.resolve("plan", settings)


def test_an_unregistered_provider_says_what_is_registered(llm_settings: Settings) -> None:
    registry.clear_provider_cache()

    with pytest.raises(ProviderNotConfiguredError) as raised:
        registry.get_provider("mistral", llm_settings)

    message = str(raised.value)
    assert "mistral" in message
    assert "LLM_PROVIDERS" in message
    # And it names what *is* available, so the fix is visible from the error:
    # `openai` ships with this build, `anthropic` does not yet (B-029).
    assert "openai" in message


def test_a_provider_is_built_once_and_reused(llm_settings: Settings, fake_llm: FakeLLM) -> None:
    """A provider holds a connection pool; building one per call would be a TLS
    handshake per LLM call."""
    first = registry.get_provider("fake", llm_settings)
    second = registry.get_provider("fake", llm_settings)

    assert first is second is fake_llm


def test_registering_again_replaces_the_cached_instance(llm_settings: Settings) -> None:
    original, replacement = FakeLLM(), FakeLLM()
    registry.register_provider("fake", lambda: original)
    assert registry.get_provider("fake", llm_settings) is original

    registry.register_provider("fake", lambda: replacement)
    try:
        assert registry.get_provider("fake", llm_settings) is replacement
    finally:
        registry.clear_provider_cache()


def test_a_stub_provider_is_refused_in_production(
    llm_settings: Settings, fake_llm: FakeLLM
) -> None:
    """A fake reaching production would not fail — it would fabricate.

    That is the worst failure this product has: confident, evidenced-looking
    answers with no model and no data behind them. Refused for a production
    build and for a production environment, the same pair the auth and secrets
    assertions use.
    """
    assert fake_llm.capabilities().is_stub

    for update in ({"build_env": "prod"}, {"env": "prod"}):
        registry.clear_provider_cache()
        registry.register_provider("fake", lambda: fake_llm)
        with pytest.raises(ProviderNotConfiguredError, match="fabricate"):
            registry.get_provider("fake", llm_settings.model_copy(update=update))


@pytest.mark.live_provider
def test_a_real_provider_is_not_caught_by_the_stub_guard(llm_settings: Settings) -> None:
    """The guard must key off the declaration, not off being a test double.

    Marked ``live_provider`` because it deliberately obtains a **non-stub**
    provider, which the session guard otherwise refuses (B-040). No money is at
    risk — the provider is a local subclass with no network behind it — but the
    marker is the honest way to say "this test wants the thing the guard stops",
    and it keeps that decision in the test's own source.
    """

    class NotAStub(FakeLLM):
        def capabilities(self) -> ProviderCaps:
            return ProviderCaps(name="real", supports_response_schema=True, is_stub=False)

    registry.register_provider("fake", NotAStub)
    try:
        assert registry.get_provider("fake", llm_settings.model_copy(update={"env": "prod"}))
    finally:
        registry.clear_provider_cache()


def test_the_ledger_and_the_llm_package_agree_about_roles_and_tiers() -> None:
    """``usage_ledger`` stores a role and a tier under a CHECK constraint, and
    the database schema deliberately does not import the agent package.

    Two copies of one list is a drift waiting to happen — a role added in
    ``llm/base.py`` and not in the schema would be rejected by the database at
    the first call, in production, at whatever hour that is. This is the cheap
    version of that discovery.
    """
    assert models.LLM_ROLES == ROLES
    assert models.LLM_TIERS == TIERS
