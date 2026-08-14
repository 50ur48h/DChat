"""Provider-agnostic, metered model calls (architecture Part 4.9, 8.3).

``complete`` is the front door and the only thing above this package needs to
import. It takes a *role* — what the caller is doing — and never a model name:
``registry`` maps role → tier → model from configuration, which is where
architecture 8.3's biggest cost lever lives.

``base`` states what a provider is; ``structured`` turns text into the pydantic
object a caller asked for, repairing once; ``meter`` writes the ``usage_ledger``
row that every call leaves behind; ``fake`` is the deterministic provider that
every agent test and every Phase 9 eval runs against. Real providers and the
fallback chain are WP6.2.

Nothing in here calls the DAL, and nothing in ``dal/`` calls this. Phase 7 joins
them in the agent runner; keeping them ignorant of each other is what lets the
FakeLLM stand in without a database and the DAL be tested without a model.
"""

from __future__ import annotations

from dataagent.llm.base import (
    DEFAULT_ROLE_TIERS,
    ROLES,
    TIERS,
    CallLimits,
    Completion,
    LLMError,
    LLMProvider,
    LLMRequest,
    Message,
    ProviderCaps,
    Role,
    Tags,
    Tier,
    Usage,
)
from dataagent.llm.fake import FakeLLM, NoScriptedResponseError
from dataagent.llm.registry import ModelChoice, ProviderNotConfiguredError, resolve
from dataagent.llm.service import complete
from dataagent.llm.structured import StructuredOutputError

__all__ = [
    "DEFAULT_ROLE_TIERS",
    "ROLES",
    "TIERS",
    "CallLimits",
    "Completion",
    "FakeLLM",
    "LLMError",
    "LLMProvider",
    "LLMRequest",
    "Message",
    "ModelChoice",
    "NoScriptedResponseError",
    "ProviderCaps",
    "ProviderNotConfiguredError",
    "Role",
    "StructuredOutputError",
    "Tags",
    "Tier",
    "Usage",
    "complete",
    "resolve",
]
