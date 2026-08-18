"""The guard that stops a test spending real money (B-040).

A guard that has never fired is a guard nobody has tested, and this one protects
against a failure that already happened once: a WP7.2c test reached the agent
through HTTP, settings resolved through the developer's ``.env``, and OpenAI
answered for real. CI could not have caught it — CI has no key, so the suite went
green there while charging every developer who ran it locally.

These run the guard against a deliberately non-stub provider, which is the only
way to know it still bites.

**There are two doors and both are watched** (B-073). A chat model comes out of
``registry.get_provider``; an embedder comes out of
``knowledge.embeddings.get_embedder`` and bills the same account through a
different endpoint. The second half of this file is the second door, tested the
same way and for a sharper reason: one upload is one embedding call per chunk,
so an unguarded embedder spends faster than the completion that prompted B-040.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import SecretStr

from dataagent.knowledge import embeddings
from dataagent.llm import registry
from dataagent.llm.base import Completion, LLMRequest, ProviderCaps
from llm_fixture import build_settings


class _PretendLiveProvider:
    """Claims not to be a stub, and would be a real API in production.

    ``is_stub=False`` is the whole fixture: it is the one bit the registry's
    production check and this guard both read, and the FakeLLM sets it True.
    """

    def capabilities(self) -> ProviderCaps:
        return ProviderCaps(name="pretend-live", supports_response_schema=True, is_stub=False)

    async def complete(self, request: LLMRequest) -> Completion:  # pragma: no cover - never reached
        raise AssertionError("the guard should have stopped this before any call")

    async def aclose(self) -> None:  # pragma: no cover - never reached
        return None


@pytest.fixture
def pretend_live() -> object:
    registry.register_provider("pretend-live", _PretendLiveProvider)
    try:
        yield
    finally:
        registry.clear_provider_cache()


def test_asking_for_a_non_stub_provider_is_refused(
    pretend_live: object, expected_live_provider_refusal: None
) -> None:
    """The first mechanism: it raises, so nothing leaves the machine."""
    with pytest.raises(RuntimeError, match="live provider"):
        registry.get_provider("pretend-live")


def test_the_refusal_says_how_to_fix_it(
    pretend_live: object, expected_live_provider_refusal: None
) -> None:
    """A guard whose message does not say what to do next gets worked around."""
    with pytest.raises(RuntimeError) as raised:
        registry.get_provider("pretend-live")

    message = str(raised.value)
    assert "build_settings()" in message
    assert "live_provider" in message


def test_a_stub_provider_passes_straight_through(fake_llm: object) -> None:
    """The guard must be invisible to the suite it protects."""
    provider = registry.get_provider("fake")

    assert provider.capabilities().is_stub


@pytest.mark.live_provider
def test_a_marked_test_may_use_a_live_provider(pretend_live: object) -> None:
    """The opt-out exists so the guard is a control rather than an obstacle.

    Nothing in the suite carries this marker today — the live smokes are scripts,
    run by hand, on purpose. It is here so that if something ever needs to, the
    decision is visible in the test's own source rather than in a comment.
    """
    provider = registry.get_provider("pretend-live")

    assert provider.capabilities().is_stub is False


# ---------------------------------------------------------------------------
# The second door: embeddings (B-073)
# ---------------------------------------------------------------------------


def _configured(**overrides: Any) -> Any:
    """Settings a real embedder can be built from. No call is ever made."""
    return build_settings(
        openai_api_key=SecretStr("test-key"),
        embeddings_model="text-embedding-3-small",
        **overrides,
    )


@pytest.fixture(autouse=True)
def forget_built_embedders() -> Iterator[None]:
    """The factory caches, and a cached live embedder would outlive the test
    that built it — including into a test with no opt-out marker."""
    embeddings.reset_embedder()
    yield
    embeddings.reset_embedder()


def test_asking_for_a_live_embedder_is_refused(expected_live_provider_refusal: None) -> None:
    """No pretend object here, deliberately: this is the embedder a configured
    deployment actually builds, refused because it declares `is_stub` false."""
    with pytest.raises(RuntimeError, match="live embedder"):
        embeddings.get_embedder(_configured())


def test_the_embedder_refusal_says_how_to_fix_it(expected_live_provider_refusal: None) -> None:
    """Same advice as the provider door, in the same words. Two doors refusing
    in two dialects would be two things to learn."""
    with pytest.raises(RuntimeError) as raised:
        embeddings.get_embedder(_configured())

    message = str(raised.value)
    assert "build_settings()" in message
    assert "live_provider" in message


def test_no_embedder_configured_passes_straight_through() -> None:
    """The guard must be invisible to the suite it protects, and None is what
    every test that does not configure embeddings already gets."""
    assert embeddings.get_embedder(build_settings()) is None


@pytest.mark.live_provider
def test_a_marked_test_may_build_a_live_embedder() -> None:
    """The same opt-out, on the same terms."""
    embedder = embeddings.get_embedder(_configured())

    assert embedder is not None
    assert embedder.is_stub is False
