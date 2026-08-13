"""The data access layer — the only path from the agent to customer data.

Architecture Part 7.1: everything the model influences arrives here as text and
leaves as a ``ValidatedQuery`` or a refusal. Nothing else in the application may
build one, and no connector will run anything else.

``validator`` decides; ``policy`` loads what it decides against; the executor
(WP5.2) is what finally runs it.
"""

from __future__ import annotations

from dataagent.dal.errors import PolicyViolation, ViolationCode
from dataagent.dal.policy import SourcePolicy, source_policy
from dataagent.dal.validator import ColumnRef, TableRef, Validated, validate

__all__ = [
    "ColumnRef",
    "PolicyViolation",
    "SourcePolicy",
    "TableRef",
    "Validated",
    "ViolationCode",
    "source_policy",
    "validate",
]
