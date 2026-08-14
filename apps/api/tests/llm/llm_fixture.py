"""What the LLM suite is configured with.

A module rather than fixtures, so a test that needs to name a model id or build
its own settings can import the same values the fixtures use — the shape
``tests/dal/catalog_fixture.py`` established.
"""

from __future__ import annotations

from dataagent.config import Settings

#: One provider, three tiers, obviously fake model ids. Named after their tier so
#: that a test asserting on a model id is really asserting on the tier that was
#: chosen for the role.
FAKE_MODELS = {"fake": {"small": "fake-small", "mid": "fake-mid", "strong": "fake-strong"}}


def build_settings(**overrides: object) -> Settings:
    """Settings with one configured provider and no prices.

    No prices on purpose: the default state of this system is that a model is
    unpriced, so the tests that care about cost add them explicitly and the rest
    prove that an unpriced call records a null rather than a zero.

    **Hermetic in two ways, and both are required.** ``_env_file=None`` switches
    off the repository's ``.env`` for these settings, and every LLM field is then
    passed explicitly. The second alone is not enough: pydantic-settings
    *deep-merges* dict-typed fields across sources, so an explicit
    ``llm_role_map={}`` is merged with whatever ``.env`` holds rather than
    replacing it, and ``llm_models`` would quietly gain every real provider the
    developer had configured. A machine with working local configuration would
    otherwise assert different things from CI — which is precisely what happened
    the first time this file was written without it.
    """
    values: dict[str, object] = {
        "env": "ci",
        "build_env": "dev",
        "llm_providers": ("fake",),
        "llm_models": FAKE_MODELS,
        "llm_role_map": {},
        "llm_prices": {},
        "llm_run_cost_limit_usd": None,
        "llm_refuse_unpriced_when_capped": True,
        "openai_api_key": None,
        "anthropic_api_key": None,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # pyright: ignore[reportArgumentType, reportCallIssue]
