"""Talking to customer databases (architecture Part 5.1).

WP3.1 ships the two pieces that must exist *before* any driver does: the error
sanitizer every connector failure passes through, and a transport-level
reachability probe that needs no credentials. The connector protocol itself, the
Postgres implementation and the read-only verification arrive in WP3.2, and SQL
Server in WP3.3.
"""

from __future__ import annotations

from dataagent.connectors.sanitizer import sanitize, sanitize_exception

__all__ = ["sanitize", "sanitize_exception"]
