"""dataagent — the API service, agent runtime, DAL, connectors and catalog.

Import direction is one-way and enforced by review (arch Part 0.2.9):
``routes → services → {agent | catalog | knowledge | semantic | dal} → connectors``.
Nothing below a layer may import from above it.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
