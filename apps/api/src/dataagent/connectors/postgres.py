"""The PostgreSQL connector (architecture Part 5.1, plan WP3.2).

Four properties are structural here rather than remembered:

**Read-only sessions.** Every connection this class opens for reading sets
``default_transaction_read_only = on``, and every execution runs inside a
transaction that is *also* declared read-only. A write would have to get past
both, and then past the credentials themselves.

**Bounded work.** ``statement_timeout`` is set per call from ``ExecLimits`` and
rows come back through a server-side cursor that fetches ``max_rows + 1`` — one
extra, so "there was more" is a fact rather than a guess. There is no code path
that fetches an unbounded result.

**Sanitized failures.** Nothing leaves this module as a driver exception. Every
one becomes a ``ConnectorError`` carrying a scrubbed message, with the original
deliberately *not* chained: ``raise ... from error`` keeps the driver's text in
``__cause__``, and the next traceback printed anywhere would put a DSN in a log.

**Declared encryption.** ``tls_mode`` is a required constructor argument with no
default, decided by the policy in ``connectors.tls``, and a verification asks the
server — not the driver — whether the session ended up encrypted (B-013). The
answer is reported even when it is the one nobody wants to see.

The read-only *verification* deserves its own paragraph, because it is easy to
write a version that proves nothing. Asking a read-only session to write and
watching it fail tests our own session setting, not the customer's credentials.
So verification (a) reads the engine's privilege catalog — what may this *role*
do — and (b) attempts one `CREATE TABLE` on a **separate connection with normal
session settings**, inside a transaction that is always rolled back. Only both
together justify `readonly_verified`.

One note on typing. asyncpg ships partial annotations, so pyright in strict mode
reports most of its surface as unknown. Rather than relax the setting for the
whole package, its objects are held as ``Any`` at this one boundary and every
value that leaves this module is converted to a declared type on the way out.
The untyped area is therefore exactly this file, and it is visible.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self, cast

import asyncpg

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
from dataagent.connectors.tls import ssl_parameter, tls_detail

__all__ = ["POSTGRES_CAPS", "PostgresConnector"]

#: asyncpg's own objects, deliberately untyped here — see the module note.
type AsyncpgConnection = Any

#: Bound through an Any-typed name for the same reason, and this is the one
#: suppression in the file: asyncpg's own signature for connect() declares
#: `timeout: int`, which its documentation contradicts, and every keyword it
#: takes is otherwise reported as unknown.
_connect: Any = asyncpg.connect  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

#: Shows up in pg_stat_activity, so a DBA can see who is asking.
APPLICATION_NAME = "dataagent"

DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0

POSTGRES_CAPS = Caps(
    dialect="postgres",
    max_identifier_length=63,
    identifier_quote='"',
    catalog_access="pg_catalog",
    limit_syntax="limit",
    statement_timeout_mechanism="set_statement_timeout",
    supports_tablesample=True,
    explain_format="json",
)


class PostgresConnector:
    """One customer PostgreSQL database.

    Holds at most one open connection, lazily. Use it as an async context
    manager, or call ``aclose()``: a connector that outlives its close leaves a
    session open on somebody else's server.
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
        tls_ca_file: Path | None = None,
        connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ) -> None:
        self._host = host
        self._port = port
        self._database = database
        self._username = username
        self._password = password
        # Required, with no default: how much encryption a customer's credentials
        # travel under is not a decision this class gets to make quietly. The
        # policy that answers it is `connectors.tls.resolve_tls_mode` (B-013).
        self._tls_mode = tls_mode
        self._tls_ca_file = tls_ca_file
        self._connect_timeout = connect_timeout_seconds
        self._connection: AsyncpgConnection | None = None

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
                await connection.close()

    def capabilities(self) -> Caps:
        return POSTGRES_CAPS

    # -- connections -------------------------------------------------------

    def _known(self) -> tuple[str, ...]:
        """What must never appear in an error message leaving this class."""
        return (self._password, self._host, self._username, self._database)

    def _fail(self, error: Exception) -> ConnectorError:
        return ConnectorError(sanitize_exception(error, known=self._known()))

    async def _open(self, *, read_only: bool) -> AsyncpgConnection:
        settings = {"application_name": APPLICATION_NAME}
        if read_only:
            settings["default_transaction_read_only"] = "on"

        try:
            return await _connect(
                host=self._host,
                port=self._port,
                user=self._username,
                password=self._password,
                database=self._database,
                timeout=self._connect_timeout,
                server_settings=settings,
                # Whatever the policy decided for this data source. A mode the
                # server cannot satisfy fails the connection here, which is the
                # behaviour worth having: better a refusal an admin can read than
                # a silent fallback to plaintext.
                ssl=ssl_parameter(self._tls_mode, self._tls_ca_file),
            )
        except Exception as error:
            raise self._fail(error) from None

    async def _reader(self) -> AsyncpgConnection:
        if self._connection is None or self._connection.is_closed():
            self._connection = await self._open(read_only=True)
        return self._connection

    # -- the one execution path -------------------------------------------

    async def execute(self, query: ValidatedQuery, limits: ExecLimits) -> ResultFrame:
        """Run approved SQL. The only place this module sends a query.

        Column names come from the prepared statement rather than from the first
        row, so an empty result still describes its own shape.
        """
        connection = await self._reader()
        started = time.perf_counter()

        try:
            async with connection.transaction(readonly=True):
                # SET LOCAL: bounded by this transaction, so a connection handed
                # back for the next call carries no leftover deadline.
                await connection.execute(_statement_timeout(limits))
                statement = await connection.prepare(query.sql)
                columns = tuple(str(attribute.name) for attribute in statement.get_attributes())
                cursor = await statement.cursor(*query.parameters)
                records = await cursor.fetch(limits.max_rows + 1)
        except Exception as error:
            raise self._fail(error) from None

        duration_ms = int((time.perf_counter() - started) * 1000)
        truncated = len(records) > limits.max_rows
        rows = tuple(tuple(record.values()) for record in records[: limits.max_rows])
        return ResultFrame(columns=columns, rows=rows, truncated=truncated, duration_ms=duration_ms)

    # -- introspection -----------------------------------------------------

    async def list_schemas(self) -> list[str]:
        frame = await self.execute(introspection.schemas(), _METADATA_LIMITS)
        return [str(row[0]) for row in frame.rows]

    async def list_tables(self, schemas: Sequence[str]) -> list[TableRef]:
        frame = await self.execute(introspection.tables(schemas), _METADATA_LIMITS)
        return [
            TableRef(
                schema=str(row[0]),
                name=str(row[1]),
                kind=str(row[2]),
                comment=_optional(row[3]),
            )
            for row in frame.rows
        ]

    async def list_columns(self, schemas: Sequence[str]) -> list[ColumnInfo]:
        frame = await self.execute(introspection.columns(schemas), _METADATA_LIMITS)
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
        ]

    async def list_foreign_keys(self, schemas: Sequence[str]) -> list[ForeignKey]:
        frame = await self.execute(introspection.foreign_keys(schemas), _METADATA_LIMITS)
        return [
            ForeignKey(
                constraint_name=str(row[0]),
                from_schema=str(row[1]),
                from_table=str(row[2]),
                from_columns=_names(row[3]),
                to_schema=str(row[4]),
                to_table=str(row[5]),
                to_columns=_names(row[6]),
            )
            for row in frame.rows
        ]

    # -- verification ------------------------------------------------------

    async def test_connection(self) -> Health:
        """Connect, then prove the credentials cannot write.

        Never raises: an unusable data source is an answer this endpoint exists
        to give. ``readonly_verified`` is false unless there is evidence for it —
        an error, a timeout or an unexpected shape all leave it false.
        """
        checked_at = datetime.now(UTC)

        try:
            schemas = await self.list_schemas()
        except ConnectorError as error:
            return Health(
                reachable=False,
                readonly_verified=False,
                detail=str(error),
                checked_at=checked_at,
            )

        # Asked before the privilege questions, because it describes the channel
        # every later answer travelled over.
        tls = await self._tls_status()
        tls_note = () if tls is None else (f"TLS: {tls.detail}",)

        try:
            frame = await self.execute(introspection.readonly_evidence(schemas), ExecLimits(1))
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
        evidence = (*tls_note, *writable, probe_note, *self._role_note(facts))

        verified = not writable and probe_refused
        # The role's *name* is not repeated back. It is half a credential, and
        # architecture Part 7.3 allows a response to carry only its last four
        # characters — which the data-source row already stores.
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
        """Whether this session is encrypted, according to the server (B-013).

        The driver knows what it *asked* for; only the engine knows what it got,
        and with ``prefer`` those differ silently whenever the server serves no
        certificate. An engine that will not answer leaves this unknown — which
        is reported as unknown, never as encrypted.
        """
        try:
            frame = await self.execute(introspection.tls_status(), ExecLimits(1))
            facts = dict(zip(frame.columns, frame.rows[0], strict=True))
        except (ConnectorError, IndexError, ValueError):
            return None

        encrypted = facts.get("encrypted") is True
        return TlsStatus(
            mode=self._tls_mode,
            encrypted=encrypted,
            detail=tls_detail(
                mode=self._tls_mode,
                encrypted=encrypted,
                version=_optional(facts.get("tls_version")),
                cipher=_optional(facts.get("cipher")),
            ),
        )

    def _role_note(self, facts: dict[str, object]) -> tuple[str, ...]:
        """Say *that* the server disagrees about who connected, never who.

        A connection pooler in front of the database can hand the session to a
        different role than the one configured, which quietly makes every
        privilege fact above true of somebody else. Worth surfacing; not worth
        printing two usernames to do it.
        """
        reported = facts.get("role_name")
        if isinstance(reported, str) and reported != self._username:
            return ("the server reports a different role than the one configured",)
        return ()

    async def _write_probe(self) -> tuple[bool, str]:
        """Attempt one write, on a connection that is *not* read-only.

        This is the check that would be worthless done any other way: a session
        with ``default_transaction_read_only`` would refuse the statement no
        matter how privileged the credentials are, and we would learn nothing
        except that we can configure our own driver.

        The transaction is rolled back unconditionally, so even the outcome we
        do not want leaves nothing behind.
        """
        try:
            connection = await self._open(read_only=False)
        except ConnectorError as error:
            return False, f"the write probe could not run ({error})"

        try:
            transaction = connection.transaction()
            await transaction.start()
            try:
                await connection.execute(introspection.write_probe_sql())
            except asyncpg.InsufficientPrivilegeError:
                return True, "CREATE TABLE was refused: permission denied"
            except asyncpg.PostgresError as error:
                # Any other refusal — no schema it may create in, for instance —
                # is still a refusal. The SQLSTATE goes in the evidence so an
                # admin can see *how* it was refused.
                return True, f"CREATE TABLE was refused (SQLSTATE {_sqlstate(error)})"
            else:
                return False, (
                    "CREATE TABLE succeeded and was rolled back — these credentials can write"
                )
            finally:
                with suppress(Exception):
                    await transaction.rollback()
        except Exception as error:
            return False, f"the write probe could not run ({self._fail(error)})"
        finally:
            with suppress(Exception):
                await connection.close()


#: Metadata results are bounded by the size of the customer's schema, not by
#: their data. Generous, and still a limit.
_METADATA_LIMITS = ExecLimits(max_rows=50_000, timeout_seconds=30.0)


def _statement_timeout(limits: ExecLimits) -> str:
    """``SET LOCAL`` cannot take a bind parameter; this takes no input either."""
    return f"SET LOCAL statement_timeout = {max(1, int(limits.timeout_seconds * 1000))}"


def _optional(value: object) -> str | None:
    return None if value is None else str(value)


def _names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item) for item in cast("Sequence[object]", value))


def _write_privileges(facts: dict[str, object]) -> tuple[str, ...]:
    """Everything the catalog says this role may do that a reader may not."""
    findings: list[str] = []

    if _flag(facts, "is_superuser"):
        findings.append("the role is a superuser")
    if _flag(facts, "can_bypass_rls"):
        findings.append("the role can bypass row-level security")
    if _flag(facts, "writes_everything"):
        findings.append("the role is a member of pg_write_all_data")
    if _flag(facts, "can_create_in_database"):
        findings.append("the role may create objects in this database")

    schemas = _count(facts, "writable_schemas")
    if schemas:
        findings.append(f"{schemas} schema(s) allow this role to create objects")

    tables = _count(facts, "writable_tables")
    if tables:
        findings.append(
            f"{tables} table(s) accept INSERT, UPDATE, DELETE or TRUNCATE from this role"
        )

    return tuple(findings)


def _flag(facts: dict[str, object], name: str) -> bool:
    return facts.get(name) is True


def _count(facts: dict[str, object], name: str) -> int:
    value = facts.get(name)
    return value if isinstance(value, int) else 0


def _version(facts: dict[str, object]) -> str | None:
    raw = facts.get("server_version")
    if not isinstance(raw, str):
        return None
    # "PostgreSQL 16.4 (Debian …) on x86_64…" — the first two words are the part
    # anyone reads, and the rest names the host's architecture.
    return " ".join(raw.split()[:2])


def _sqlstate(error: asyncpg.PostgresError) -> str:
    return str(getattr(error, "sqlstate", "unknown"))
