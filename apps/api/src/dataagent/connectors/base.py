"""What every connector is, and what may reach one (architecture Part 5.1).

The agent thinks "I need schema information". Only this package knows what
``information_schema`` looks like on each engine, how a statement timeout is set,
or whether LIMIT is spelled TOP. Everything above the connectors sees one shape.

Two enforcement points live here, and the second is the important one.

**Capabilities.** ``Caps`` states per-engine truth the DAL and the profiler adapt
to, rather than each of them guessing from a dialect string.

**The query type gate.** ``execute`` accepts only a ``ValidatedQuery``, and a
``ValidatedQuery`` cannot be built without a ``PolicyGrant`` held by a module on
a short, named list. The DAL's validator joins that list in Phase 5; until then
the only holder is ``connectors.introspection``, whose SQL is fixed templates
with bound parameters and never touches user input.

Be precise about what that buys, because "impossible" is too strong a word for
Python:

* A call site that passes a bare string does not type-check. pyright runs in
  strict mode in CI, so this one is real and mechanical.
* A module that wants to forge a grant must name itself as one of the sanctioned
  validators — a lie that is one grep away in review, and asserted against in
  ``test_only_sanctioned_modules_can_validate_sql``.
* Nothing stops a determined author with commit access. The gate makes "run
  unvalidated SQL" a deliberate, visible act instead of an easy accident, which
  is the achievable half of the goal.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, final, runtime_checkable

__all__ = [
    "SANCTIONED_VALIDATORS",
    "Caps",
    "ColumnInfo",
    "Connector",
    "ConnectorError",
    "ExecLimits",
    "ForeignKey",
    "Health",
    "PolicyGrant",
    "ResultFrame",
    "TableRef",
    "TlsStatus",
    "ValidatedQuery",
]

#: The only modules that may declare SQL fit to run. Extended in Phase 5 by the
#: DAL's validator and by nothing else; a test asserts this set's contents so
#: that widening it is a reviewed change rather than an import.
SANCTIONED_VALIDATORS: frozenset[str] = frozenset(
    {
        # Fixed templates with bound parameters, no user input anywhere near them.
        "dataagent.connectors.introspection",
        # Phase 5. Named now so the shape of the list is visible from the start.
        "dataagent.dal.validator",
    }
)


class ConnectorError(Exception):
    """A failure talking to a customer database, already sanitized.

    Connectors raise only this. The driver's own exception is *not* chained:
    ``raise ... from error`` would keep the unsanitized text in ``__cause__``,
    where the next traceback to be printed would put a DSN in a log file.

    ``statement_fault`` says which of two unrelated things happened, because the
    caller has to treat them differently and could not tell them apart. *The
    database could not be reached* is unfixable by rewriting the SQL. *The
    database rejected this SQL* usually is — and the engine generally says how.
    Treating both as unfixable is what ended a live run after one query on
    ``function round(double precision, integer) does not exist``, whose own HINT
    read *"You might need to add explicit type casts"*.

    Each connector decides, because the evidence is dialect-specific: PostgreSQL
    has SQLSTATE, SQL Server has error numbers. **The default is False**, so a
    connector that has not been taught keeps today's behaviour rather than
    silently gaining a retry loop.
    """

    def __init__(self, message: str, *, statement_fault: bool = False) -> None:
        super().__init__(message)
        self.statement_fault = statement_fault


@final
class PolicyGrant:
    """The right to declare a piece of SQL validated.

    Constructing one requires naming yourself, and the name must be on
    ``SANCTIONED_VALIDATORS``. Pass ``__name__``; passing anything else is
    either a mistake or a lie, and both are visible.
    """

    __slots__ = ("holder",)

    def __init__(self, holder: str) -> None:
        if holder not in SANCTIONED_VALIDATORS:
            raise PermissionError(
                f"{holder!r} may not declare SQL validated. Only the SQL policy "
                "module may build a ValidatedQuery (architecture Part 5.1, 7.5)."
            )
        self.holder = holder


@final
class ValidatedQuery:
    """SQL that a sanctioned validator has approved, plus its bound parameters.

    Immutable, and deliberately reticent in ``repr``: parameters can carry values
    from a customer's database, and a dataclass repr in a traceback is a very
    ordinary way for those to reach a log.
    """

    __slots__ = ("_dialect", "_origin", "_parameters", "_sql")

    def __init__(
        self,
        grant: PolicyGrant,
        *,
        sql: str,
        dialect: str,
        parameters: Sequence[object] = (),
    ) -> None:
        # The annotation says PolicyGrant; this is the runtime half of the same
        # statement, for callers that are not type-checked at all.
        if not isinstance(grant, PolicyGrant):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError("A ValidatedQuery needs a PolicyGrant from the SQL policy module.")
        self._sql = sql
        self._dialect = dialect
        self._parameters = tuple(parameters)
        self._origin = grant.holder

    @property
    def sql(self) -> str:
        return self._sql

    @property
    def dialect(self) -> str:
        return self._dialect

    @property
    def parameters(self) -> tuple[object, ...]:
        return self._parameters

    @property
    def origin(self) -> str:
        """Which validator approved this. Recorded with executions from Phase 5."""
        return self._origin

    @property
    def sql_hash(self) -> str:
        return hashlib.sha256(self._sql.encode()).hexdigest()[:12]

    def __repr__(self) -> str:
        return (
            f"ValidatedQuery(dialect={self._dialect!r}, origin={self._origin!r}, "
            f"sql_hash={self.sql_hash!r})"
        )


@dataclass(frozen=True, slots=True)
class Caps:
    """Per-engine truth, stated rather than inferred (architecture Part 5.1)."""

    #: sqlglot's name for this dialect: postgres | tsql | mysql.
    dialect: str
    max_identifier_length: int
    identifier_quote: str
    #: information_schema | sys — where metadata comes from.
    catalog_access: str
    #: limit | top — consumed by the DAL's transpile step in Phase 5.
    limit_syntax: str
    #: How a per-statement deadline is imposed: a session GUC, or a driver option.
    statement_timeout_mechanism: str
    supports_tablesample: bool
    explain_format: str


@dataclass(frozen=True, slots=True)
class TlsStatus:
    """What was asked for, and what the *server* says actually happened.

    Both halves are here because they can disagree, and the disagreement is the
    interesting part: ``prefer`` against a database that serves no certificate is
    a plaintext connection that no error ever mentions (B-013).
    """

    #: The policy this connection was opened with.
    mode: str
    #: Not our opinion — read back from the engine's own view of this session.
    encrypted: bool
    #: One line, safe to show: protocol and cipher, and what was *not* checked.
    detail: str


@dataclass(frozen=True, slots=True)
class Health:
    """The answer to "can we use this data source, and is it safe to?"

    ``readonly_verified`` is the one that matters at registration: it is false
    unless this process has evidence that the credentials **cannot write**.
    Unknown is false. A failed check is false. It is never assumed.
    """

    reachable: bool
    readonly_verified: bool
    detail: str
    checked_at: datetime
    server_version: str | None = None
    #: Each check that ran, and what it found. Safe to show an admin: it names
    #: privileges and roles, never credentials.
    evidence: tuple[str, ...] = ()
    #: None when the check never got far enough to know. Deliberately separate
    #: from ``readonly_verified``: a plaintext connection to a genuinely
    #: read-only account is a real risk *and* a real verification, and collapsing
    #: the two would report one of them wrongly.
    tls: TlsStatus | None = None


@dataclass(frozen=True, slots=True)
class TableRef:
    schema: str
    name: str
    #: table | view
    kind: str
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    schema: str
    table: str
    name: str
    data_type: str
    nullable: bool
    ordinal: int
    is_primary_key: bool
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class ForeignKey:
    constraint_name: str
    from_schema: str
    from_table: str
    from_columns: tuple[str, ...]
    to_schema: str
    to_table: str
    to_columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExecLimits:
    """Every execution is bounded. There is no unbounded variant to reach for."""

    max_rows: int = 1000
    timeout_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class ResultFrame:
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    #: True when ``max_rows`` cut the result short — so a caller never mistakes a
    #: truncated answer for a complete one.
    truncated: bool
    duration_ms: int


@runtime_checkable
class Connector(Protocol):
    """One database, spoken to in one shape.

    The full protocol in architecture Part 5.1 also carries ``sample``,
    ``profile`` and ``explain``. Those arrive with the profiler in Phase 4 and
    the DAL in Phase 5 — each method lands in the phase that has a caller for
    it, rather than as an abstract method nobody implements.
    """

    def capabilities(self) -> Caps: ...

    async def aclose(self) -> None:
        """Release the connection. Every caller owes a connector this.

        Part of the protocol rather than of one implementation: a session left
        open is left open on somebody else's server, and the caller cannot know
        which connectors hold one.
        """
        ...

    async def test_connection(self) -> Health: ...

    async def list_schemas(self) -> list[str]: ...

    async def list_tables(self, schemas: Sequence[str]) -> list[TableRef]: ...

    async def list_columns(self, schemas: Sequence[str]) -> list[ColumnInfo]: ...

    async def list_foreign_keys(self, schemas: Sequence[str]) -> list[ForeignKey]: ...

    async def execute(self, query: ValidatedQuery, limits: ExecLimits) -> ResultFrame: ...
