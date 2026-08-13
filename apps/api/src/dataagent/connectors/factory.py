"""Which connector speaks to which engine.

One function, so "what happens when someone registers a MySQL database" has a
single answer that a reviewer can read. An engine with no connector is refused
here with a message naming the work package that brings it, rather than failing
somewhere deeper with an attribute error.
"""

from __future__ import annotations

from dataagent.config import Settings, get_settings
from dataagent.connectors.base import Caps, Connector, ConnectorError
from dataagent.connectors.postgres import POSTGRES_CAPS, PostgresConnector
from dataagent.connectors.sqlserver import SQLSERVER_CAPS, SqlServerConnector

__all__ = ["SUPPORTED_ENGINES", "caps_for", "connector_for", "require_supported"]

#: Engines this build can talk to.
SUPPORTED_ENGINES: frozenset[str] = frozenset({"pg", "mssql"})

#: And what to say about the ones it cannot, so the answer names a work package
#: rather than being a shrug.
_NOT_YET: dict[str, str] = {
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


#: What each engine is, without asking one. Kept beside ``connector_for`` so the
#: two answers to "what happens for engine X" cannot drift apart.
_CAPS: dict[str, Caps] = {"pg": POSTGRES_CAPS, "mssql": SQLSERVER_CAPS}


def caps_for(engine: str) -> Caps:
    """The engine's capabilities, with no connection and no credential.

    The DAL judges a query before anything is opened — whether LIMIT is spelled
    TOP does not depend on whether the customer's server is up — so this reads
    the connector module's constant rather than calling ``capabilities()`` on an
    instance that would first have to connect.
    """
    require_supported(engine)
    return _CAPS[engine]


def connector_for(
    *,
    engine: str,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
    tls_mode: str,
    settings: Settings | None = None,
) -> Connector:
    """Build the connector for one data source's stored settings.

    ``tls_mode`` comes from the data-source row, where the policy in
    ``connectors.tls`` put it. The CA bundle to check certificates against is
    deployment-wide rather than per source, so it is read here (B-013).
    """
    require_supported(engine)
    resolved = settings if settings is not None else get_settings()

    if engine == "mssql":
        # No CA file: Microsoft's driver trusts the system store and offers no
        # per-connection bundle, so TLS_CA_FILE is a Postgres-only override
        # today (B-015). Passing it would suggest otherwise.
        return SqlServerConnector(
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
            tls_mode=tls_mode,
        )

    return PostgresConnector(
        host=host,
        port=port,
        database=database,
        username=username,
        password=password,
        tls_mode=tls_mode,
        tls_ca_file=resolved.tls_ca_file,
    )
