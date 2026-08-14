"""Fixtures for the LLM suite.

Every test here goes through the real registry rather than handing a provider
straight to the code under test. That is deliberate: the registry is where a
role becomes a model, and a suite that bypassed it would prove the front door
works while leaving the thing that decides *what a run costs* untested.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from llm_fixture import build_settings

from dataagent.config import Settings
from dataagent.llm import registry
from dataagent.llm.fake import FakeLLM


@pytest.fixture
def llm_settings() -> Settings:
    return build_settings()


@pytest.fixture
def fake_llm() -> Iterator[FakeLLM]:
    """A FakeLLM registered as the provider named ``fake``, and unregistered after.

    The teardown matters more than it looks: a provider left in the registry
    would answer the next test's calls, which is exactly the cross-test coupling
    that makes a deterministic harness stop being deterministic.
    """
    fake = FakeLLM()
    registry.register_provider("fake", lambda: fake)
    try:
        yield fake
    finally:
        registry.clear_provider_cache()
