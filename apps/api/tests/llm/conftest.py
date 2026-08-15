"""Fixtures for the LLM suite.

``fake_llm`` lives in the root conftest, because the agent suite needs the same
provider and two definitions of one fixture drift.

Every test here goes through the real registry rather than handing a provider
straight to the code under test. That is deliberate: the registry is where a
role becomes a model, and a suite that bypassed it would prove the front door
works while leaving the thing that decides *what a run costs* untested.
"""

from __future__ import annotations

import pytest

from dataagent.config import Settings
from llm_fixture import build_settings


@pytest.fixture
def llm_settings() -> Settings:
    return build_settings()
