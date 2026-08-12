"""The SQL Server connector against a real SQL Server.

Two questions, the same two the Postgres suite asks: does it describe the
database accurately, and can it be made to write? The answers arrive through a
different driver, a different catalog and a weaker session model, which is
exactly why they are asked again rather than assumed to carry over.

These tests need the compose container — `make up.mssql && make seed.mssql` —
and skip when it is not there, because a 1.5 GB image is not something to
require of every `pytest` run. CI's mssql job sets ``REQUIRE_MSSQL=1``, which
turns the skip into a failure: a silently skipped connector suite would leave
the engine untested exactly when someone changed it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest

from dataagent.config import Settings
from dataagent.connectors import introspection
from dataagent.connectors.base import ConnectorError, ExecLimits, PolicyGrant, ValidatedQuery
from dataagent.connectors.sqlserver import SqlServerConnector

# Bound to names rather than written beside `password=`: a quoted literal there
# is what secret scanners and ruff's S106 exist for, and neither should have to
# learn to ignore this file.
WRONG_LOGIN = "definitely-not-the-password"
SA_ACCOUNT = "sa"


@dataclass(frozen=True)
class PizzaServer:
    """Where the fixture is, and the two ways in."""

    host: str
    port: int
    database: str
    reader_username: str
    reader_password: str
    owner_username: str
    owner_password: str

    def reader(self, tls_mode: str = "prefer") -> SqlServerConnector:
        return SqlServerConnector(
            host=self.host,
            port=self.port,
            database=self.database,
            username=self.reader_username,
            password=self.reader_password,
            tls_mode=tls_mode,
        )

    def owner(self, tls_mode: str = "prefer") -> SqlServerConnector:
        return SqlServerConnector(
            host=self.host,
            port=self.port,
            database=self.database,
            username=self.owner_username,
            password=self.owner_password,
            tls_mode=tls_mode,
        )


def _setting(name: str, default: str) -> str:
    """Environment first, then the repository's .env, then a documented default.

    Through Settings' own env file so that `make up.mssql` on a developer
    machine is enough — the same reasoning as the Postgres fixtures, which would
    otherwise only ever run in CI.
    """
    from dataagent.config import _REPO_ENV_FILE  # pyright: ignore[reportPrivateUsage]

    value = os.environ.get(name)
    if value:
        return value
    if _REPO_ENV_FILE is not None and _REPO_ENV_FILE.is_file():
        for line in _REPO_ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                found = line.split("=", 1)[1].strip()
                if found:
                    return found
    return default


@pytest.fixture(scope="session")
def pizza_mssql() -> PizzaServer:
    """The seeded SQL Server, or a skip — unless CI demands otherwise."""
    server = PizzaServer(
        host=_setting("MSSQL_HOST", "localhost"),
        port=int(_setting("MSSQL_PORT", "1433")),
        database=_setting("MSSQL_DB", "pizza"),
        reader_username=_setting("MSSQL_PIZZA_READONLY_USER", "pizza_readonly"),
        reader_password=_setting("MSSQL_PIZZA_READONLY_PASSWORD", ""),
        owner_username=SA_ACCOUNT,
        owner_password=_setting("MSSQL_SA_PASSWORD", ""),
    )
    required = os.environ.get("REQUIRE_MSSQL") == "1"

    import asyncio
    import socket

    def reachable() -> bool:
        try:
            with socket.create_connection((server.host, server.port), timeout=2):
                return True
        except OSError:
            return False

    if not server.reader_password or not reachable():
        message = f"no seeded SQL Server at {server.host}:{server.port}"
        if required:
            pytest.fail(f"REQUIRE_MSSQL=1 but {message}")
        pytest.skip(f"{message} — run `make up.mssql && make seed.mssql`")

    async def seeded() -> bool:
        async with server.reader() as connector:
            tables = await connector.list_tables(["dbo"])
        return any(table.name == "orders" for table in tables)

    if not asyncio.run(seeded()):
        message = "the SQL Server container is running but not seeded"
        if required:
            pytest.fail(f"REQUIRE_MSSQL=1 but {message}")
        pytest.skip(f"{message} — run `make seed.mssql`")

    return server


def _forge(sql: str) -> ValidatedQuery:
    """Build a query the way only the SQL policy module may — see the note in
    the Postgres suite; the source scan would fail on this call inside ``src``."""
    return ValidatedQuery(
        PolicyGrant("dataagent.connectors.introspection"), sql=sql, dialect="tsql"
    )


# ---------------------------------------------------------------------------
# Describing the database
# ---------------------------------------------------------------------------


async def test_capabilities_state_this_engines_truth() -> None:
    caps = SqlServerConnector(
        host="h", port=1433, database="d", username="u", password=WRONG_LOGIN, tls_mode="require"
    ).capabilities()

    assert caps.dialect == "tsql"
    assert caps.limit_syntax == "top", "TOP, not LIMIT — Phase 5 transpiles from this"
    assert caps.max_identifier_length == 128
    assert caps.catalog_access == "sys"
    assert caps.statement_timeout_mechanism == "driver_query_timeout"


async def test_it_lists_the_schemas_a_login_may_see(pizza_mssql: PizzaServer) -> None:
    async with pizza_mssql.reader() as connector:
        schemas = await connector.list_schemas()

    assert "dbo" in schemas
    assert "analytics" in schemas
    assert "sys" not in schemas
    assert "db_datareader" not in schemas, "the engine's own role schemas are not data"


async def test_it_lists_tables_and_views_with_their_descriptions(
    pizza_mssql: PizzaServer,
) -> None:
    async with pizza_mssql.reader() as connector:
        tables = await connector.list_tables(["dbo"])

    by_name = {table.name: table for table in tables}
    assert set(by_name) == {"stores", "customers", "staff", "menu_items", "orders", "payments"}
    assert by_name["orders"].kind == "table"
    assert by_name["stores"].comment == "Physical restaurant locations."
    assert "order_items" not in by_name, "the fixture's missing join is deliberate"


async def test_schema_filtering_happens_even_though_the_query_cannot_do_it(
    pizza_mssql: PizzaServer,
) -> None:
    """The T-SQL templates take no parameters, so the filter is in Python. It
    still has to work, and a second schema is in the fixture to prove it."""
    async with pizza_mssql.reader() as connector:
        analytics = await connector.list_tables(["analytics"])
        both = await connector.list_tables(["dbo", "analytics"])

    assert [(table.schema, table.name, table.kind) for table in analytics] == [
        ("analytics", "busy_stores", "view")
    ]
    assert len(both) == 7


async def test_it_lists_columns_with_types_nullability_and_keys(
    pizza_mssql: PizzaServer,
) -> None:
    async with pizza_mssql.reader() as connector:
        columns = await connector.list_columns(["dbo"])

    orders = {column.name: column for column in columns if column.table == "orders"}
    assert [column.name for column in columns if column.table == "orders"] == [
        "id",
        "order_date",
        "store_id",
        "customer_id",
        "channel",
        "total_amount",
        "status",
    ], "columns must come back in ordinal order"
    assert orders["id"].is_primary_key is True
    assert orders["store_id"].is_primary_key is False
    assert orders["order_date"].nullable is False
    assert orders["order_date"].data_type == "date"
    # Assembled to read like a declaration, which is what the catalog will show.
    assert orders["total_amount"].data_type == "decimal(8, 2)"
    assert orders["channel"].data_type == "varchar(20)"
    assert orders["total_amount"].comment is not None

    customers = {column.name: column for column in columns if column.table == "customers"}
    assert customers["phone"].nullable is True
    # nvarchar stores its length in bytes; reporting 640 instead of 320 would be
    # a small lie the catalog would then repeat.
    assert customers["email"].data_type == "nvarchar(320)"


async def test_it_finds_the_foreign_key_graph(pizza_mssql: PizzaServer) -> None:
    """Phase 4's join graph is built from exactly this."""
    async with pizza_mssql.reader() as connector:
        keys = await connector.list_foreign_keys(["dbo"])

    edges = {(key.from_table, key.from_columns, key.to_table, key.to_columns) for key in keys}
    assert ("orders", ("store_id",), "stores", ("id",)) in edges
    assert ("orders", ("customer_id",), "customers", ("id",)) in edges
    assert ("payments", ("order_id",), "orders", ("id",)) in edges
    assert ("staff", ("store_id",), "stores", ("id",)) in edges
    assert not [key for key in keys if key.to_table == "menu_items"], (
        "nothing may link orders to menu items — Phase 8's honest refusal depends on it"
    )


# ---------------------------------------------------------------------------
# Bounded execution
# ---------------------------------------------------------------------------


async def test_a_result_is_cut_to_the_limit_and_says_so(pizza_mssql: PizzaServer) -> None:
    async with pizza_mssql.reader() as connector:
        frame = await connector.execute(
            _forge("SELECT id FROM dbo.stores ORDER BY id"), ExecLimits(2)
        )

    assert frame.rows == ((1,), (2,))
    assert frame.truncated is True


async def test_a_result_within_the_limit_is_not_marked_truncated(
    pizza_mssql: PizzaServer,
) -> None:
    async with pizza_mssql.reader() as connector:
        frame = await connector.execute(
            _forge("SELECT id FROM dbo.stores ORDER BY id"), ExecLimits(50)
        )

    assert len(frame.rows) == 5
    assert frame.truncated is False


async def test_an_empty_result_still_describes_its_shape(pizza_mssql: PizzaServer) -> None:
    async with pizza_mssql.reader() as connector:
        frame = await connector.execute(
            _forge("SELECT id, name FROM dbo.stores WHERE 1 = 0"), ExecLimits(5)
        )

    assert frame.columns == ("id", "name")
    assert frame.rows == ()


async def test_a_slow_query_is_stopped_by_the_drivers_timeout(pizza_mssql: PizzaServer) -> None:
    """ODBC counts whole seconds, so this asks for 0.1 and gets 1 — which is the
    point: rounding up beats `int(0.1)`, which would mean no timeout at all."""
    async with pizza_mssql.reader() as connector:
        with pytest.raises(ConnectorError):
            await connector.execute(
                _forge("WAITFOR DELAY '00:00:10'"), ExecLimits(max_rows=1, timeout_seconds=0.1)
            )


async def test_nothing_this_connector_does_is_committed(pizza_mssql: PizzaServer) -> None:
    """The guard that stands in for a read-only session.

    Run as the owner, who is allowed to write, through the ordinary execute
    path: the row is inserted and then rolled back, because every execution ends
    in a rollback whether it succeeded or not.
    """
    async with pizza_mssql.owner() as connector:
        await connector.execute(
            _forge(
                "INSERT INTO dbo.stores (id, name, city, country, opened_on) "
                "VALUES (999, N'Forged', N'Nowhere', N'Nowhere', '2020-01-01')"
            ),
            ExecLimits(1),
        )
        frame = await connector.execute(
            _forge("SELECT COUNT(*) FROM dbo.stores WHERE id = 999"), ExecLimits(1)
        )

    assert frame.rows == ((0,),), "an execution was committed"


# ---------------------------------------------------------------------------
# Verification — the point of the work package
# ---------------------------------------------------------------------------


async def test_a_read_only_login_is_verified(pizza_mssql: PizzaServer) -> None:
    async with pizza_mssql.reader() as connector:
        health = await connector.test_connection()

    assert health.reachable is True
    assert health.readonly_verified is True
    assert "cannot write" in health.detail
    assert health.server_version is not None
    assert health.server_version.startswith("Microsoft SQL Server")
    assert any("refused" in note for note in health.evidence)


async def test_the_sa_login_is_not_verified_and_the_reason_is_named(
    pizza_mssql: PizzaServer,
) -> None:
    """The failure mode this exists to catch: registering with the wrong account."""
    async with pizza_mssql.owner() as connector:
        health = await connector.test_connection()

    assert health.reachable is True
    assert health.readonly_verified is False
    assert "not read-only" in health.detail
    assert any("sysadmin" in note for note in health.evidence)


async def test_verification_never_names_the_login_it_connected_as(
    pizza_mssql: PizzaServer,
) -> None:
    """Architecture 7.3: a response may carry the last four characters, no more.

    This engine makes it easy to get wrong — its login failures quote the
    account back at you verbatim.
    """
    async with pizza_mssql.reader() as connector:
        health = await connector.test_connection()

    rendered = health.detail + " ".join(health.evidence)
    assert pizza_mssql.reader_username not in rendered
    assert pizza_mssql.reader_password not in rendered


async def test_the_write_probe_leaves_nothing_behind(pizza_mssql: PizzaServer) -> None:
    """It runs as sa too, where it succeeds — and is still rolled back."""
    async with pizza_mssql.owner() as connector:
        await connector.test_connection()

        frame = await connector.execute(
            _forge(
                "SELECT COUNT(*) FROM sys.objects WHERE name = "
                f"'{introspection.READONLY_PROBE_TABLE}'"
            ),
            ExecLimits(1),
        )

    assert frame.rows == ((0,),), "the write probe committed something"


# ---------------------------------------------------------------------------
# Encryption (B-013)
# ---------------------------------------------------------------------------


async def test_the_connection_is_encrypted_and_says_it_was_not_verified(
    pizza_mssql: PizzaServer,
) -> None:
    """Unlike the Postgres container, SQL Server generates a certificate for
    itself, so a local connection here is genuinely encrypted — and genuinely
    unverified, because that certificate is signed by nobody."""
    async with pizza_mssql.reader() as connector:
        health = await connector.test_connection()

    assert health.tls is not None
    assert health.tls.encrypted is True
    assert "not verified" in health.tls.detail
    assert any(note.startswith("TLS: ") for note in health.evidence)


async def test_a_read_only_login_is_told_it_may_not_read_the_servers_own_view(
    pizza_mssql: PizzaServer,
) -> None:
    """sys.dm_exec_connections needs VIEW SERVER STATE, which a read-only login
    does not have. The answer then comes from the driver, and says so rather
    than passing itself off as the server's word."""
    async with pizza_mssql.reader() as reader, pizza_mssql.owner() as owner:
        from_reader = await reader.test_connection()
        from_owner = await owner.test_connection()

    assert from_reader.tls is not None and from_owner.tls is not None
    assert "according to the driver" in from_reader.tls.detail
    assert "according to the driver" not in from_owner.tls.detail, (
        "sa may read the DMV, so the server itself answered"
    )


async def test_verifying_the_certificate_refuses_a_self_signed_one(
    pizza_mssql: PizzaServer,
) -> None:
    """The mode ladder is real on this engine too, not decoration."""
    async with pizza_mssql.reader(tls_mode="verify-full") as connector:
        health = await connector.test_connection()

    assert health.reachable is False
    assert health.readonly_verified is False


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


async def test_a_wrong_password_fails_without_quoting_the_credential(
    pizza_mssql: PizzaServer,
) -> None:
    connector = SqlServerConnector(
        host=pizza_mssql.host,
        port=pizza_mssql.port,
        database=pizza_mssql.database,
        username=pizza_mssql.reader_username,
        password=WRONG_LOGIN,
        tls_mode="prefer",
    )

    async with connector:
        health = await connector.test_connection()

    assert health.reachable is False
    assert health.readonly_verified is False
    assert WRONG_LOGIN not in health.detail
    # This driver's message is literally "Login failed for user '<name>'".
    assert pizza_mssql.reader_username not in health.detail


async def test_a_database_that_does_not_exist_is_reported_not_guessed(
    pizza_mssql: PizzaServer,
) -> None:
    connector = SqlServerConnector(
        host=pizza_mssql.host,
        port=pizza_mssql.port,
        database="no_such_database_here",
        username=pizza_mssql.reader_username,
        password=pizza_mssql.reader_password,
        tls_mode="prefer",
    )

    async with connector:
        health = await connector.test_connection()

    assert health.reachable is False
    assert "no_such_database_here" not in health.detail


async def test_closing_twice_is_harmless(pizza_mssql: PizzaServer) -> None:
    connector = pizza_mssql.reader()
    await connector.list_schemas()

    await connector.aclose()
    await connector.aclose()


def test_the_settings_agree_with_the_engine_the_factory_builds() -> None:
    """A cheap guard on the wiring, and one that needs no server."""
    from dataagent.connectors.factory import SUPPORTED_ENGINES, connector_for

    connector = connector_for(
        engine="mssql",
        host="db.example.com",
        port=1433,
        database="pizza",
        username="reader",
        password=WRONG_LOGIN,
        tls_mode="require",
        settings=Settings(env="ci", build_env="dev"),
    )

    assert "mssql" in SUPPORTED_ENGINES
    assert isinstance(connector, SqlServerConnector)
    assert connector.capabilities().dialect == "tsql"
