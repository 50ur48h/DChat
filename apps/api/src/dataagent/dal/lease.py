"""One connection to the customer database, held for the length of a run.

**Measured, not guessed** (**B-176**). On the run that ran out of budget, the
seven queries spent **39.2 seconds** between `tool_called` and `query_executed`
while the database itself executed for **15.7**. The missing 23.5 seconds was
not the data and not the loop: `executor.execute` builds a connector when none
is supplied and closes it in a `finally`, so every query in a run paid for

* a platform-database read to fetch the data source,
* a **Key Vault** round trip for the credential — `KeyVaultSecretsProvider`
  caches nothing, so this is a network call each time, and
* a fresh TLS handshake to a database in another region.

Seven times, at about 3.4 seconds each. A lease pays it once.

**Why a mutable object rather than a field on `ToolContext`.** The context is
frozen and slotted on purpose — a tool that could reach the organization by
another route could reach the wrong one — so the connection cannot be cached
*on* it. It is carried *by* it instead, and the frozen guarantee is untouched:
what the context holds is still fixed for the life of the run.

**This does not widen what a tool may do.** The lease hands its connector to
`dal.run`, which validates exactly as before; nothing here executes anything.
The only thing that changes is how many times the connection is built.

**A broken connection is dropped rather than reused.** A query can fail for
reasons the connection survives — a policy refusal, a syntax error — and for
reasons it does not. `forget()` is what the caller uses when the connector
itself is suspect, so a run does not spend its remaining steps replaying a dead
socket.
"""

from __future__ import annotations

import uuid
from contextlib import suppress

from dataagent.connectors.base import Connector


class ConnectionLease:
    """A connector built at most once per run, and closed with it."""

    __slots__ = ("_connector", "_data_source_id", "_org_id")

    def __init__(self, org_id: uuid.UUID, data_source_id: uuid.UUID) -> None:
        self._org_id = org_id
        self._data_source_id = data_source_id
        self._connector: Connector | None = None

    async def connector(self) -> Connector:
        """The run's connection, opening it on first use."""
        if self._connector is None:
            # Imported here rather than at module scope: `datasources.service`
            # imports from `dal` for its own reasons, and a top-level import
            # would close the cycle.
            from dataagent.datasources import service as datasources

            view = await datasources.get_data_source(self._org_id, self._data_source_id)
            self._connector = await datasources.connector_for_view(view)
        return self._connector

    def matches(self, org_id: uuid.UUID, data_source_id: uuid.UUID) -> bool:
        """Whether this lease is for the source being asked about.

        A conversation names one database (**D-022**), so this is true on every
        real path. It is checked anyway, because handing a connection for one
        organization's database to a query about another's is the single worst
        thing this product could do, and "it cannot happen" is not a check.
        """
        return self._org_id == org_id and self._data_source_id == data_source_id

    async def forget(self) -> None:
        """Drop the connection, so the next query builds a fresh one."""
        connector, self._connector = self._connector, None
        if connector is not None:
            # Closing an already-broken socket can itself raise, and there is
            # nothing to do about it and nothing worth failing a run over: the
            # point of forgetting is that this connection is already suspect.
            with suppress(Exception):
                await connector.aclose()

    async def aclose(self) -> None:
        """Close the run's connection. Safe to call when none was ever opened."""
        await self.forget()
