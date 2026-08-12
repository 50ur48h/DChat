"""The SQL Server connector (architecture Part 5.1, plan WP3.3).

The same protocol as the Postgres connector, and the same three promises —
bounded work, sanitized failures, declared encryption. One promise it cannot
make the same way, and the difference is written down here rather than glossed:

**There is no read-only session.** Postgres has ``default_transaction_read_only``
and refuses a write at the session level before privileges are even consulted.
SQL Server has no equivalent: ``ApplicationIntent=ReadOnly`` is about availability
group routing, and ODBC's ``SQL_ATTR_ACCESS_MODE`` is advisory — Microsoft's
driver accepts it and enforces nothing. So this connector's guard is different in
kind: it never commits. Connections are opened with ``autocommit`` off and every
execution ends in a rollback, in a ``finally``, whether it succeeded or not.
A write that somehow reached this class would be undone rather than refused,
which is weaker than a refusal and is why ``readonly_verified`` matters more here
than it does on Postgres.

**Synchronous driver, async protocol.** pyodbc blocks, so every call into it goes
through ``asyncio.to_thread``. A pyodbc connection may be used from more than one
thread only if the uses do not overlap, and the thread-pool gives no guarantee
about which thread runs what — so an ``asyncio.Lock`` serialises everything that
touches the handle. It is not there for correctness of *our* logic; it is there
because the driver requires it.

**Whole-second timeouts.** ``Caps.statement_timeout_mechanism`` says
``driver_query_timeout`` for this engine, and that is a real limitation: ODBC's
query timeout is an integer number of seconds, so a sub-second ``ExecLimits``
rounds up to one second rather than silently becoming "no timeout at all", which
is what ``int(0.1)`` would have meant.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self, cast

import pyodbc

from dataagent.connectors import introspection
from dataagent.connectors.base import (
    Caps,
    ColumnInfo,
    ConnectorError,
    ExecLimits,
    ForeignKey,
    Health,
    ResultFrame,
    TableRef,
    TlsStatus,
    ValidatedQuery,
)
from dataagent.connectors.sanitizer import sanitize_exception
from dataagent.connectors.tls import odbc_parameters, tls_detail

__all__ = ["SQLSERVER_CAPS", "SqlServerConnector"]

#: pyodbc's objects carry partial annotations, so they are held as ``Any`` at
#: this one boundary and everything leaving the module is a declared type —
#: the same arrangement the Postgres connector documents for asyncpg.
type OdbcConnection = Any

_connect: Any = pyodbc.connect

#: Installed by ``apps/api/docker/install-odbc.sh``. Named in full because ODBC
#: driver names are registry keys, not version ranges: there is no "latest".
ODBC_DRIVER = "ODBC Driver 18 for SQL Server"

#: Shows up in sys.dm_exec_sessions, so a DBA can see who is asking.
APPLICATION_NAME = "dataagent"

DEFAULT_CONNECT_TIMEOUT_SECONDS = 10

SQLSERVER_CAPS = Caps(
    dialect="tsql",
    max_identifier_length=128,
    # T-SQL's own spelling is [brackets]; double quotes are equally valid while
    # QUOTED_IDENTIFIER is ON, which it is for every ODBC connection, and one
    # character is what this field can hold.
    identifier_quote='"',
    catalog_access="sys",
    limit_syntax="top",
    statement_timeout_mechanism="driver_query_timeout",
    supports_tablesample=True,
    explain_format="showplan_xml",
)

#: SQLSTATE class 42 is "syntax error or access rule violation" — which is what a
#: refused CREATE TABLE is. Distinguishing it from, say, 08S01 (communication
#: link failure) is what keeps a network problem from being read as proof that
#: the credentials cannot write.
_ACCESS_RULE_VIOLATION = "42"


class SqlServerConnector:
    """One customer SQL Server database.

    Holds at most one open connection, lazily. Use it as an async context
    manager, or call ``aclose()``.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
        tls_mode: str,
        connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ) -> None:
        self._host = host
        self._port = port
        self._database = database
        self._username = username
        self._password = password
        self._tls_mode = tls_mode
        self._connect_timeout = connect_timeout_seconds
        self._connection: OdbcConnection | None = None
        self._lock = asyncio.Lock()

    # -- lifecycle ---------------------------------------------------------

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            with suppress(Exception):
                await asyncio.to_thread(connection.close)

    def capabilities(self) -> Caps:
        return SQLSERVER_CAPS

    # -- connections -------------------------------------------------------

    def _known(self) -> tuple[str, ...]:
        """What must never appear in an error message leaving this class.

        Login failures on this engine quote the account back at you — the driver
        text is literally ``Login failed for user 'sa'`` — so the username being
        on this list is not a precaution, it is the common case.
        """
        return (self._password, self._host, self._username, self._database)

    def _fail(self, error: Exception) -> ConnectorError:
        return ConnectorError(sanitize_exception(error, known=self._known()))

    async def _open(self, *, read_only: bool) -> OdbcConnection:
        try:
            return await asyncio.to_thread(self._connect_blocking, read_only=read_only)
        except Exception as error:
            raise self._fail(error) from None

    def _connect_blocking(self, *, read_only: bool) -> OdbcConnection:
        # Keyword form rather than a hand-built connection string: pyodbc brace-
        # quotes values that contain ODBC's separators, so a password with a
        # semicolon in it connects instead of silently truncating the string.
        return _connect(
            driver=f"{{{ODBC_DRIVER}}}",
            server=f"{self._host},{self._port}",
            database=self._database,
            uid=self._username,
            pwd=self._password,
            app=APPLICATION_NAME,
            **odbc_parameters(self._tls_mode),
            # pyodbc's own keywords, not connection-string attributes.
            timeout=self._connect_timeout,
            autocommit=False,
            # Advisory on this driver, as the module docstring says. Set anyway:
            # it costs nothing, it is the standard's way to say what we intend,
            # and the probe connection deliberately does not set it.
            readonly=read_only,
        )

    async def _reader(self) -> OdbcConnection:
        if self._connection is None:
            self._connection = await self._open(read_only=True)
        return self._connection

    # -- the one execution path -------------------------------------------

    async def execute(self, query: ValidatedQuery, limits: ExecLimits) -> ResultFrame:
        """Run approved SQL. The only place this module sends a query.

        Ends in a rollback whatever happens. With no read-only session to lean
        on, "nothing this connector does is ever committed" is the guard, and a
        guard in a ``finally`` is the only kind worth having.
        """
        connection = await self._reader()
        started = time.perf_counter()

        async with self._lock:
            try:
                columns, records = await asyncio.to_thread(
                    self._execute_blocking, connection, query, limits
                )
            except Exception as error:
                raise self._fail(error) from None

        duration_ms = int((time.perf_counter() - started) * 1000)
        truncated = len(records) > limits.max_rows
        rows = tuple(tuple(record) for record in records[: limits.max_rows])
        return ResultFrame(columns=columns, rows=rows, truncated=truncated, duration_ms=duration_ms)

    def _execute_blocking(
        self, connection: OdbcConnection, query: ValidatedQuery, limits: ExecLimits
    ) -> tuple[tuple[str, ...], list[tuple[object, ...]]]:
        connection.timeout = _query_timeout(limits)
        cursor = connection.cursor()
        try:
            cursor.execute(query.sql, *query.parameters)
            description = cast("Sequence[Sequence[object]] | None", cursor.description)
            if description is None:
                # A statement with no result set. The DAL will only ever send
                # SELECT and EXPLAIN, so this is not a path anything takes on
                # purpose — but fetching from it raises "previous SQL was not a
                # query", and an empty answer is the truthful one.
                return (), []
            columns = tuple(str(column[0]) for column in description)
            # One more than asked for, so "there was more" is a fact rather than
            # a guess — the same trick as the server-side cursor on Postgres.
            fetched = cast("list[Sequence[object]]", cursor.fetchmany(limits.max_rows + 1))
            return columns, [tuple(row) for row in fetched]
        finally:
            with suppress(Exception):
                cursor.close()
            with suppress(Exception):
                connection.rollback()

    # -- introspection -----------------------------------------------------

    async def list_schemas(self) -> list[str]:
        frame = await self.execute(introspection.tsql_schemas(), _METADATA_LIMITS)
        return [str(row[0]) for row in frame.rows]

    async def list_tables(self, schemas: Sequence[str]) -> list[TableRef]:
        frame = await self.execute(introspection.tsql_tables(), _METADATA_LIMITS)
        wanted = set(schemas)
        return [
            TableRef(
                schema=str(row[0]),
                name=str(row[1]),
                kind=str(row[2]),
                comment=_optional(row[3]),
            )
            for row in frame.rows
            if str(row[0]) in wanted
        ]

    async def list_columns(self, schemas: Sequence[str]) -> list[ColumnInfo]:
        frame = await self.execute(introspection.tsql_columns(), _METADATA_LIMITS)
        wanted = set(schemas)
        return [
            ColumnInfo(
                schema=str(row[0]),
                table=str(row[1]),
                name=str(row[2]),
                data_type=str(row[3]),
                nullable=bool(row[4]),
                ordinal=int(cast("int", row[5])),
                is_primary_key=bool(row[6]),
                comment=_optional(row[7]),
            )
            for row in frame.rows
            if str(row[0]) in wanted
        ]

    async def list_foreign_keys(self, schemas: Sequence[str]) -> list[ForeignKey]:
        """One key per constraint, with its columns in key order.

        The query returns a row per column because T-SQL has no array_agg worth
        depending on across versions, so the grouping happens here — relying on
        the ORDER BY in the template, which is part of the template for exactly
        this reason.
        """
        frame = await self.execute(introspection.tsql_foreign_keys(), _METADATA_LIMITS)
        wanted = set(schemas)

        keys: dict[tuple[str, str, str], ForeignKey] = {}
        for row in frame.rows:
            constraint, from_schema, from_table = str(row[0]), str(row[1]), str(row[2])
            if from_schema not in wanted:
                continue
            identity = (from_schema, from_table, constraint)
            existing = keys.get(identity)
            if existing is None:
                keys[identity] = ForeignKey(
                    constraint_name=constraint,
                    from_schema=from_schema,
                    from_table=from_table,
                    from_columns=(str(row[3]),),
                    to_schema=str(row[4]),
                    to_table=str(row[5]),
                    to_columns=(str(row[6]),),
                )
            else:
                keys[identity] = ForeignKey(
                    constraint_name=existing.constraint_name,
                    from_schema=existing.from_schema,
                    from_table=existing.from_table,
                    from_columns=(*existing.from_columns, str(row[3])),
                    to_schema=existing.to_schema,
                    to_table=existing.to_table,
                    to_columns=(*existing.to_columns, str(row[6])),
                )
        return list(keys.values())

    # -- verification ------------------------------------------------------

    async def test_connection(self) -> Health:
        """Connect, then prove the credentials cannot write.

        Never raises, and ``readonly_verified`` is false unless there is evidence
        for it — the same contract as the Postgres connector, reached through a
        different set of catalog questions.
        """
        checked_at = datetime.now(UTC)

        try:
            await self.list_schemas()
        except ConnectorError as error:
            return Health(
                reachable=False,
                readonly_verified=False,
                detail=str(error),
                checked_at=checked_at,
            )

        tls = await self._tls_status()
        tls_note = () if tls is None else (f"TLS: {tls.detail}",)

        try:
            frame = await self.execute(introspection.tsql_readonly_evidence(), ExecLimits(1))
            facts = dict(zip(frame.columns, frame.rows[0], strict=True))
        except (ConnectorError, IndexError, ValueError) as error:
            return Health(
                reachable=True,
                readonly_verified=False,
                detail=(
                    "Connected, but could not establish whether these credentials "
                    f"are read-only: {error}"
                ),
                checked_at=checked_at,
                evidence=tls_note,
                tls=tls,
            )

        writable = _write_privileges(facts)
        probe_refused, probe_note = await self._write_probe()
        evidence = (*tls_note, *writable, probe_note)

        verified = not writable and probe_refused
        detail = (
            "Connected. These credentials cannot write."
            if verified
            else "Connected, but these credentials are not read-only: "
            + "; ".join(writable or (probe_note,))
        )

        return Health(
            reachable=True,
            readonly_verified=verified,
            detail=detail,
            checked_at=checked_at,
            server_version=_version(facts),
            evidence=evidence,
            tls=tls,
        )

    async def _tls_status(self) -> TlsStatus | None:
        """Whether this session is encrypted — from the server if it will say.

        ``sys.dm_exec_connections`` needs VIEW SERVER STATE, which a genuinely
        read-only login will not have, so the common case for a correctly
        registered data source is that the server refuses to answer. That is not
        a reason to report nothing: every mode except ``disable`` asks the driver
        for encryption and the driver fails the connection rather than falling
        back, so a connection that exists is an encrypted one. The wording says
        which of the two answered.
        """
        try:
            frame = await self.execute(introspection.tsql_tls_status(), ExecLimits(1))
            option = str(frame.rows[0][0]).upper()
        except (ConnectorError, IndexError):
            return self._tls_from_driver()

        encrypted = option == "TRUE"
        return TlsStatus(
            mode=self._tls_mode,
            encrypted=encrypted,
            detail=tls_detail(mode=self._tls_mode, encrypted=encrypted),
        )

    def _tls_from_driver(self) -> TlsStatus:
        encrypted = self._tls_mode != "disable"
        return TlsStatus(
            mode=self._tls_mode,
            encrypted=encrypted,
            detail=(
                f"{tls_detail(mode=self._tls_mode, encrypted=encrypted)} "
                "(according to the driver: this login may not read the server's "
                "own view of the connection)"
            ),
        )

    async def _write_probe(self) -> tuple[bool, str]:
        """Attempt one write on a connection that is not declared read-only.

        Same experiment as on Postgres and the same rollback, but it carries more
        weight here: with no read-only session to sit behind, this and the
        privilege catalog are the whole of the evidence.
        """
        try:
            connection = await self._open(read_only=False)
        except ConnectorError as error:
            return False, f"the write probe could not run ({error})"

        try:
            return await asyncio.to_thread(self._probe_blocking, connection)
        except Exception as error:
            return False, f"the write probe could not run ({self._fail(error)})"
        finally:
            with suppress(Exception):
                await asyncio.to_thread(connection.close)

    def _probe_blocking(self, connection: OdbcConnection) -> tuple[bool, str]:
        cursor = connection.cursor()
        try:
            cursor.execute(introspection.tsql_write_probe_sql())
        except pyodbc.Error as error:
            state = _sqlstate(error)
            if state.startswith(_ACCESS_RULE_VIOLATION):
                return True, f"CREATE TABLE was refused (SQLSTATE {state})"
            # A link failure is not a refusal, and reading it as one would let a
            # flaky network hand out a green tick.
            return False, f"the write probe could not run (SQLSTATE {state})"
        else:
            return False, (
                "CREATE TABLE succeeded and was rolled back — these credentials can write"
            )
        finally:
            with suppress(Exception):
                cursor.close()
            with suppress(Exception):
                connection.rollback()


#: Metadata results are bounded by the size of the customer's schema, not by
#: their data. Generous, and still a limit.
_METADATA_LIMITS = ExecLimits(max_rows=50_000, timeout_seconds=30.0)


def _query_timeout(limits: ExecLimits) -> int:
    """ODBC counts whole seconds, and zero means "no limit" — never that."""
    return max(1, math.ceil(limits.timeout_seconds))


def _optional(value: object) -> str | None:
    return None if value is None else str(value)


def _write_privileges(facts: dict[str, object]) -> tuple[str, ...]:
    """Everything the catalog says this login may do that a reader may not."""
    findings: list[str] = []

    if _flag(facts, "is_sysadmin"):
        findings.append("the login is a member of the sysadmin server role")
    if _flag(facts, "is_db_owner"):
        findings.append("the login is a member of db_owner")
    if _flag(facts, "is_db_datawriter"):
        findings.append("the login is a member of db_datawriter")
    if _flag(facts, "is_ddladmin"):
        findings.append("the login is a member of db_ddladmin")
    if _flag(facts, "can_create_in_database"):
        findings.append("the login may create tables in this database")

    tables = _count(facts, "writable_tables")
    if tables:
        findings.append(f"{tables} table(s) accept INSERT, UPDATE, DELETE or ALTER from this login")

    return tuple(findings)


def _flag(facts: dict[str, object], name: str) -> bool:
    """T-SQL has no boolean type: these arrive as 1 or 0."""
    value = facts.get(name)
    if isinstance(value, bool):
        return value
    return isinstance(value, int) and value == 1


def _count(facts: dict[str, object], name: str) -> int:
    value = facts.get(name)
    return value if isinstance(value, int) else 0


def _version(facts: dict[str, object]) -> str | None:
    """Assembled, not taken from @@VERSION, which also names the host OS."""
    raw = facts.get("product_version")
    return f"Microsoft SQL Server {raw}" if isinstance(raw, str) else None


def _sqlstate(error: pyodbc.Error) -> str:
    args = cast("tuple[object, ...]", error.args)
    return str(args[0]) if args else "unknown"
