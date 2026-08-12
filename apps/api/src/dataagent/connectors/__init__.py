"""Talking to customer databases (architecture Part 5.1).

* ``base`` — the protocol every connector implements, the capability descriptor
  the DAL and profiler adapt to, and ``ValidatedQuery``: the type ``execute``
  accepts and nothing else can be.
* ``introspection`` — the fixed SQL that metadata comes from, for both engines,
  and until Phase 5 the only thing allowed to declare SQL validated.
* ``postgres`` — the PostgreSQL implementation: read-only sessions, bounded
  execution, and read-only verification that asks the engine rather than itself.
* ``sqlserver`` — the same protocol over pyodbc, where there is no read-only
  session to lean on and nothing is ever committed instead.
* ``factory`` — which connector speaks to which engine, and what to say about
  the engines this build cannot speak yet (MySQL: V1.1).
* ``tls`` — how much encryption a connection must have, and which addresses are
  allowed to do without it (B-013).
* ``sanitizer`` / ``probe`` — the error scrubber every failure passes through,
  and a transport-level reachability check that needs no credentials.
"""

from __future__ import annotations

from dataagent.connectors.sanitizer import sanitize, sanitize_exception

__all__ = ["sanitize", "sanitize_exception"]
