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
    """
    values: dict[str, object] = {
        "env": "ci",
        "build_env": "dev",
        "llm_providers": ("fake",),
        "llm_models": FAKE_MODELS,
    }
    values.update(overrides)
    return Settings(**values)  # pyright: ignore[reportArgumentType]
