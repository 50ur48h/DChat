"""Which connector speaks to which engine.

One function, so "what happens when someone registers a MySQL database" has a
single answer that a reviewer can read. An engine with no connector is refused
here with a message naming the work package that brings it, rather than failing
somewhere deeper with an attribute error.
"""

from __future__ import annotations

from dataagent.connectors.base import Connector, ConnectorError
from dataagent.connectors.postgres import PostgresConnector

__all__ = ["SUPPORTED_ENGINES", "connector_for", "require_supported"]

#: Engines this build can talk to.
SUPPORTED_ENGINES: frozenset[str] = frozenset({"pg"})

#: And what to say about the ones it cannot, so the answer names a work package
#: rather than being a shrug.
_NOT_YET: dict[str, str] = {
    "mssql": "The SQL Server connector arrives in WP3.3.",
    "mysql": "MySQL is a V1.1 connector (architecture Part 5.1).",
}


def require_supported(engine: str) -> None:
    """Refuse an engine we cannot speak, before anything else is attempted.

    Checked first by callers, deliberately: reading a credential out of the
    secrets store or opening a socket to a customer's network to discover
    something this process already knew is work nobody asked for.
    """
    if engine in SUPPORTED_ENGINES:
        return

    pending = _NOT_YET.get(engine)
    if pending is not None:
        raise ConnectorError(f"No connector for engine {engine!r} yet. {pending}")
    raise ConnectorError(f"Unknown engine {engine!r}")


def connector_for(
    *,
    engine: str,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
) -> Connector:
    """Build the connector for one data source's stored settings."""
    require_supported(engine)
    return PostgresConnector(
        host=host,
        port=port,
        database=database,
        username=username,
        password=password,
    )
