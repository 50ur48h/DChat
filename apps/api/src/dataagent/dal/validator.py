"""The SQL policy engine (architecture Part 7.1 diagram 6, 7.5).

This module decides whether a piece of SQL may touch a customer's database. It
is the security boundary named in Part 7: the agent has no other path to data,
and nothing below here re-checks what is decided above it.

**The order of the checks is part of the design.** Cheap and absolute first,
so an attack is refused by the rule it actually broke rather than by whichever
later rule happened to trip:

1. one statement — the smuggling shape, refused before anything is inspected;
2. the statement is a SELECT, or an EXPLAIN of one;
3. no write, schema change or transaction control **anywhere** in the tree,
   including inside a CTE, which is where a top-level-only check fails;
4. no system schema, and no reach into another database on the same server;
5. no function that cannot be vouched for;
6. every table resolves against this organization's catalog;
7. every column resolves too, with ``SELECT *`` expanded against the catalog
   **before** the column rules are applied;
8. a denied column is refused wherever it appears — projection, predicate, join
   condition, ORDER BY, subquery, CTE body. A ``WHERE`` leaks values as surely
   as a projection does.

**Two properties of sqlglot carry more weight than they look.** Syntax it does
not understand becomes an ``exp.Command`` rather than an error, and a function it
does not know becomes an ``exp.Anonymous`` rather than a typed node. Both are
refused. That inverts the usual arrangement: the escape hatches worth naming
(``pg_read_file``, ``xp_cmdshell``, ``OPENROWSET``) are all anonymous, so the
deny list is there to give a clearer refusal, not to be the barrier. The barrier
is that a function this validator cannot name is a function it will not run.
That is stricter than architecture 7.5's deny list, in the direction 7.5 asks
for — see DECISIONS D-015.

**What this module does not do.** It does not connect, execute, inject a LIMIT
or mask a value; that is WP5.2's executor. It ends at a ``ValidatedQuery`` — the
only object a connector will run — plus the tables and columns the statement
touches, which the executor needs for masking and the audit row.
"""

#
# sqlglot 30 turned `expressions` into a package that re-exports its node
# classes without an `__all__`, so pyright reports every `exp.Something` as a
# private import. That is the library's packaging, not this module's typing, and
# the suppression is kept to the one file that imports sqlglot at all.
# pyright: reportPrivateImportUsage=false

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeGuard, cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError, SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope
from sqlglot.schema import MappingSchema
from sqlglot.tokens import TokenType

from dataagent.catalog.browse import Catalog, CatalogTableView
from dataagent.connectors.base import PolicyGrant, ValidatedQuery
from dataagent.dal.errors import PolicyViolation, ViolationCode
from dataagent.dal.policy import SourcePolicy

__all__ = ["ColumnRef", "TableRef", "Validated", "validate"]

#: This module is on ``SANCTIONED_VALIDATORS`` (connectors/base.py). Holding the
#: grant is what lets it declare SQL fit to run, and it is the only module in the
#: application that may.
_GRANT = PolicyGrant(__name__)

POLICY_DENY = "deny"
POLICY_MASK = "mask"

#: Ceilings on the *statement*, not on SQL. They exist because parsing,
#: qualifying and generating are all recursive over the tree: a query that is
#: deep rather than long can exhaust the interpreter before a single rule about
#: its meaning is reached, which would be a way past every one of them. Both are
#: far above any question a person would ask.
MAX_SQL_LENGTH = 20_000
MAX_NESTING_DEPTH = 50

#: Schemas the engine keeps for itself. The agent reads structure from the
#: catalog service, so it never has a reason to be in here, and an attempt is a
#: probe rather than a mistake. ``pg_`` as a prefix covers ``pg_toast`` and the
#: numbered ``pg_temp_N`` schemas without listing them.
_SYSTEM_SCHEMAS = frozenset({"information_schema", "sys", "sysibm", "mysql", "performance_schema"})
_SYSTEM_SCHEMA_PREFIXES = ("pg_",)

#: Named only to refuse them better. Every one of these is already refused for
#: being a function sqlglot cannot type; naming them turns "unknown function"
#: into "this function is not permitted", which tells the agent to stop trying.
_DENIED_FUNCTIONS = frozenset(
    {
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "pg_sleep",
        "pg_terminate_backend",
        "pg_reload_conf",
        "lo_import",
        "lo_export",
        "dblink",
        "dblink_exec",
        "query_to_xml",
        "openrowset",
        "opendatasource",
        "openquery",
        "openxml",
        "xp_cmdshell",
        "xp_dirtree",
        "xp_fileexist",
        "sp_executesql",
        "sp_oacreate",
        "sp_oamethod",
        "fn_get_audit_file",
        "waitfor",
    }
)
_DENIED_FUNCTION_PREFIXES = ("xp_", "sp_", "pg_", "lo_")

#: Functions sqlglot cannot type that are nonetheless allowed. Empty, and meant
#: to stay nearly so: an entry here is a standing decision that one specific
#: engine function is safe, and it belongs in a PR that says why.
_ALLOWED_UNTYPED_FUNCTIONS: frozenset[str] = frozenset()

#: Node classes that must not appear anywhere in the tree. ``DML`` and ``DDL``
#: are sqlglot's own base classes, so a statement type added by a future release
#: is covered the day it exists rather than the day someone remembers to list it.
#: The rest have no shared base and are named individually.
#: Typed as bare classes rather than as ``type[exp.Expression]``: sqlglot's DML
#: and DDL are mixins that do not inherit from Expression, which is exactly why
#: they are usable as categories here.
_FORBIDDEN_NODES: tuple[type, ...] = (
    exp.DML,
    exp.DDL,
    exp.Alter,
    exp.Analyze,
    exp.Command,
    exp.Commit,
    exp.Copy,
    exp.Drop,
    exp.Execute,
    exp.Grant,
    exp.Into,
    exp.Lock,
    exp.ReadCSV,
    exp.Refresh,
    exp.Rollback,
    exp.Set,
    exp.Transaction,
    exp.TruncateTable,
    exp.Use,
)

#: What a forbidden node is called when refusing it. sqlglot's class names are
#: close enough to SQL keywords to show an agent, but not all of them.
_OPERATION_NAMES: dict[type, str] = {
    exp.Command: "this statement",
    exp.Into: "SELECT INTO",
    exp.Lock: "a locking clause",
    exp.ReadCSV: "reading a file",
    exp.Set: "SET",
    exp.Transaction: "transaction control",
    exp.TruncateTable: "TRUNCATE",
}


@dataclass(frozen=True, slots=True)
class TableRef:
    """A table, as the catalog spells it."""

    schema: str
    table: str

    def __str__(self) -> str:
        return f"{self.schema}.{self.table}"


@dataclass(frozen=True, slots=True)
class ColumnRef:
    schema: str
    table: str
    column: str

    def __str__(self) -> str:
        return f"{self.schema}.{self.table}.{self.column}"


@dataclass(frozen=True, slots=True)
class Validated:
    """SQL that may run, and what it will touch.

    ``query`` is the only part a connector accepts. The rest is for the executor
    and the audit row: which tables were read, which columns, and which of those
    carry a ``mask`` policy that must be applied to the values coming back —
    masking catalog samples at write time (D-013) said nothing about results.
    """

    query: ValidatedQuery
    tables: tuple[TableRef, ...]
    columns: tuple[ColumnRef, ...]
    masked: tuple[ColumnRef, ...]

    @property
    def sql(self) -> str:
        return self.query.sql

    @property
    def touches_sensitive(self) -> bool:
        return bool(self.masked)


def validate(sql: str, *, source: SourcePolicy) -> Validated:
    """Judge one statement against one data source. Raises ``PolicyViolation``.

    The only entry point, and the only exception it raises is a violation — a
    caller that has to catch anything else is a caller that will eventually not.

    That is what the ``RecursionError`` guard is for. Parsing, qualifying and
    generating SQL are all recursive over the tree, so a statement that is deep
    rather than long — a thousand nested parentheses, a chain of ANDs — can
    exhaust the interpreter's stack instead of breaking a rule. ``_too_complex``
    refuses the ones that can be seen coming; this catches the rest, and turns a
    process-level failure into an ordinary refusal the agent can repair.
    """
    try:
        return _validate(sql, source=source)
    except RecursionError:
        failure = PolicyViolation(
            ViolationCode.TOO_COMPLEX,
            "This query nests too deeply to be checked. Simplify it, or split it into steps.",
        )
    raise failure


def _validate(sql: str, *, source: SourcePolicy) -> Validated:
    dialect = source.dialect
    statement, is_explain = _parse_one_read_only(sql, dialect)

    _reject_forbidden_nodes(statement)
    _reject_unvouched_functions(statement)

    tables = _bind_tables(statement, source.catalog)
    qualified = _resolve_identifiers(statement, source, tables)
    columns, masked = _apply_column_policy(qualified, source.catalog)

    canonical = qualified.sql(dialect=dialect, comments=False)
    if is_explain:
        canonical = f"EXPLAIN {canonical}"

    return Validated(
        query=ValidatedQuery(_GRANT, sql=canonical, dialect=dialect),
        tables=tuple(sorted(set(tables.values()), key=str)),
        columns=tuple(sorted(columns, key=str)),
        masked=tuple(sorted(masked, key=str)),
    )


# ---------------------------------------------------------------------------
# 1-2. One statement, and it reads
# ---------------------------------------------------------------------------


def _parse_one_read_only(sql: str, dialect: str) -> tuple[exp.Expression, bool]:
    """Parse in the source's own dialect and return the single read statement.

    The boolean says whether it arrived wrapped in EXPLAIN. sqlglot has no node
    for EXPLAIN — it falls back to a ``Command`` holding the rest as text — so
    the inner statement is parsed on its own and put back together at the end.
    That is also what makes ``EXPLAIN (ANALYZE) …`` fail: its options are not a
    statement, and ANALYZE would execute the query rather than plan it.
    """
    # The refusal is built inside the handler and raised outside it. `from None`
    # would only stop a traceback from *printing* the parser's error; the object
    # would still hang off __context__, and sqlglot's message quotes the SQL it
    # choked on — literal values included. Raising after the block leaves no
    # chain at all, which is what test_a_violation_never_chains_the_parser_error
    # asserts.
    _refuse_if_too_complex(sql, dialect)

    failure: PolicyViolation | None = None
    parsed: list[exp.Expression] = []
    try:
        parsed = [
            statement
            for statement in sqlglot.parse(sql, dialect=dialect)
            if isinstance(statement, exp.Expression)
        ]
    except ParseError as error:
        failure = PolicyViolation(
            ViolationCode.PARSE_ERROR,
            f"This is not valid {dialect} SQL{_where(error)}. Rewrite the query.",
        )
    except SqlglotError:
        # Depth limits and tokenizer failures. Same answer, and for the same
        # reason: nothing that cannot be parsed can be reasoned about.
        failure = PolicyViolation(
            ViolationCode.PARSE_ERROR, f"This could not be read as {dialect} SQL."
        )
    if failure is not None:
        raise failure

    if not parsed:
        raise PolicyViolation(ViolationCode.EMPTY_STATEMENT, "There is no statement here to run.")
    if len(parsed) > 1:
        raise PolicyViolation(
            ViolationCode.MULTIPLE_STATEMENTS,
            f"Send one statement. This is {len(parsed)}; only the first would be considered.",
        )

    statement = parsed[0]
    if _is_explain(statement):
        inner, nested = _parse_one_read_only(_explain_body(statement), dialect)
        if nested:
            raise PolicyViolation(
                ViolationCode.STATEMENT_NOT_READ_ONLY, "EXPLAIN cannot explain an EXPLAIN."
            )
        return inner, True

    if not isinstance(statement, exp.Select | exp.SetOperation):
        raise PolicyViolation(
            ViolationCode.STATEMENT_NOT_READ_ONLY,
            "Only SELECT is permitted here (optionally under EXPLAIN). "
            f"This is {_operation_name(statement)}.",
        )
    return statement, False


def _refuse_if_too_complex(sql: str, dialect: str) -> None:
    """Refuse what is too big or too deep to inspect, before inspecting it.

    Both ceilings are about the *checker*, not about SQL: a statement that makes
    the validator run out of stack has found a way past every rule below by
    never reaching them. Depth is counted from the tokenizer's parentheses
    rather than from the characters, so a literal like ``'((((((('`` is a string
    and not a nesting level.
    """
    if len(sql) > MAX_SQL_LENGTH:
        raise PolicyViolation(
            ViolationCode.TOO_COMPLEX,
            f"This statement is longer than the {MAX_SQL_LENGTH:,}-character limit. "
            "Ask for less in one query.",
        )

    depth = deepest = 0
    try:
        for token in sqlglot.tokenize(sql, read=dialect):
            if token.token_type is TokenType.L_PAREN:
                depth += 1
                deepest = max(deepest, depth)
            elif token.token_type is TokenType.R_PAREN:
                depth -= 1
    except SqlglotError:
        # Not tokenizable at all. The parser is about to say so more precisely.
        return

    if deepest > MAX_NESTING_DEPTH:
        raise PolicyViolation(
            ViolationCode.TOO_COMPLEX,
            f"This query nests {deepest} levels deep, past the limit of "
            f"{MAX_NESTING_DEPTH}. Simplify it, or split it into steps.",
        )


def _is_explain(statement: exp.Expression) -> TypeGuard[exp.Command]:
    return (
        isinstance(statement, exp.Command)
        and isinstance(statement.this, str)
        and statement.this.strip().upper() == "EXPLAIN"
    )


def _explain_body(statement: exp.Command) -> str:
    body = statement.args.get("expression")
    if isinstance(body, exp.Literal) and isinstance(body.this, str):
        return body.this
    raise PolicyViolation(
        ViolationCode.STATEMENT_NOT_READ_ONLY, "EXPLAIN needs a SELECT statement after it."
    )


def _where(error: ParseError) -> str:
    """The position of a parse failure, and nothing else from it.

    sqlglot's message quotes the SQL around the error, which can include a
    literal. The agent already has its own query; it needs the coordinates.
    """
    for item in error.errors:
        line, column = item.get("line"), item.get("col")
        if isinstance(line, int) and isinstance(column, int):
            return f" (line {line}, column {column})"
    return ""


def _operation_name(node: exp.Expr) -> str:
    for kind, name in _OPERATION_NAMES.items():
        if isinstance(node, kind):
            return name
    return type(node).__name__.upper()


# ---------------------------------------------------------------------------
# 3. Nothing that writes, anywhere
# ---------------------------------------------------------------------------


def _reject_forbidden_nodes(statement: exp.Expression) -> None:
    """Walk the whole tree, not just the top of it.

    ``WITH x AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM x`` parses
    as a Select. A validator that checks only the statement type approves it.
    """
    for node in statement.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise PolicyViolation(
                ViolationCode.WRITE_OPERATION,
                f"{_operation_name(node)} is not permitted: this connection reads and "
                "nothing else. Rewrite the query as a SELECT.",
                subject=_operation_name(node),
            )


# ---------------------------------------------------------------------------
# 4-5. Functions it cannot vouch for
# ---------------------------------------------------------------------------


def _reject_unvouched_functions(statement: exp.Expression) -> None:
    for node in statement.find_all(exp.Anonymous):
        name = node.name.lower()
        if name in _ALLOWED_UNTYPED_FUNCTIONS:
            continue
        if name in _DENIED_FUNCTIONS or name.startswith(_DENIED_FUNCTION_PREFIXES):
            raise PolicyViolation(
                ViolationCode.DENIED_FUNCTION,
                f"The function {node.name} is not permitted.",
                subject=node.name,
            )
        raise PolicyViolation(
            ViolationCode.UNKNOWN_FUNCTION,
            f"The function {node.name} is not one this service can vouch for. "
            "Use standard SQL functions, or aggregate in the query and shape the "
            "result afterwards.",
            subject=node.name,
        )


# ---------------------------------------------------------------------------
# 6. Every table is one the catalog knows
# ---------------------------------------------------------------------------


def _bind_tables(statement: exp.Expression, catalog: Catalog) -> dict[str, TableRef]:
    """Resolve every table reference, and rewrite it to the catalog's spelling.

    Returns the map the column pass needs: canonical ``schema.table`` string to
    ``TableRef``. Rewriting is not cosmetic — after it, the statement names
    exactly what the catalog described, so the optimizer's schema lookup and the
    column policy lookup cannot disagree about which table a column came from.
    """
    by_qualified, by_name = _catalog_index(catalog)
    cte_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
    bound: dict[str, TableRef] = {}

    for node in statement.find_all(exp.Table):
        if not isinstance(node.this, exp.Identifier):
            # FROM generate_series(...) and friends: a function standing where a
            # table belongs. There is no catalog row to check it against.
            raise PolicyViolation(
                ViolationCode.TABLE_FUNCTION,
                "Only catalogued tables and views may be read, not table functions.",
            )
        if node.catalog:
            raise PolicyViolation(
                ViolationCode.CROSS_DATABASE,
                f"{node.catalog}.{node.db}.{node.name} names another database. "
                "This data source reads one database only.",
                subject=node.catalog,
            )

        name, schema = node.name, node.db
        # A bare name may be a CTE defined in this same statement, which is not a
        # catalog object and was validated where it was written. A *qualified*
        # name never is, so the shortcut cannot be used to smuggle one in.
        if not schema and name.lower() in cte_names:
            continue

        if schema and _is_system_schema(schema):
            raise PolicyViolation(
                ViolationCode.SYSTEM_SCHEMA,
                f"{schema} is the database's own dictionary and is not readable here. "
                "Structure comes from the catalog, which you already have.",
                subject=schema,
            )

        ref = _lookup_table(schema, name, by_qualified, by_name)
        node.set("this", exp.to_identifier(ref.table, quoted=True))
        node.set("db", exp.to_identifier(ref.schema, quoted=True))
        bound[str(ref)] = ref

    if not bound:
        raise PolicyViolation(
            ViolationCode.UNKNOWN_TABLE,
            "This query reads no catalogued table. Name a table from the catalog.",
        )
    return bound


def _catalog_index(
    catalog: Catalog,
) -> tuple[dict[tuple[str, str], CatalogTableView], dict[str, list[CatalogTableView]]]:
    """Two lookups over the active snapshot, both case-folded.

    Case folding is deliberate on both engines: PostgreSQL folds unquoted names
    to lower case and SQL Server compares them case-insensitively by default, so
    matching exactly would refuse ``FROM Orders`` against a table the user can
    read by that name in any client. Two catalogued tables that differ only by
    case therefore become ambiguous rather than a coin toss.
    """
    by_qualified: dict[tuple[str, str], CatalogTableView] = {}
    by_name: dict[str, list[CatalogTableView]] = {}
    for table in catalog.tables:
        by_qualified[(table.schema_name.lower(), table.table_name.lower())] = table
        by_name.setdefault(table.table_name.lower(), []).append(table)
    return by_qualified, by_name


def _lookup_table(
    schema: str,
    name: str,
    by_qualified: dict[tuple[str, str], CatalogTableView],
    by_name: dict[str, list[CatalogTableView]],
) -> TableRef:
    if schema:
        found = by_qualified.get((schema.lower(), name.lower()))
        if found is None:
            raise PolicyViolation(
                ViolationCode.UNKNOWN_TABLE,
                f"There is no table {schema}.{name} in this data source's catalog.",
                subject=f"{schema}.{name}",
            )
        return TableRef(found.schema_name, found.table_name)

    candidates = by_name.get(name.lower(), [])
    if not candidates:
        raise PolicyViolation(
            ViolationCode.UNKNOWN_TABLE,
            f"There is no table named {name} in this data source's catalog.",
            subject=name,
        )
    if len(candidates) > 1:
        schemas = ", ".join(sorted(table.schema_name for table in candidates))
        raise PolicyViolation(
            ViolationCode.AMBIGUOUS_TABLE,
            f"{name} exists in more than one schema ({schemas}). Qualify it.",
            subject=name,
        )
    return TableRef(candidates[0].schema_name, candidates[0].table_name)


def _is_system_schema(schema: str) -> bool:
    folded = schema.lower()
    return folded in _SYSTEM_SCHEMAS or folded.startswith(_SYSTEM_SCHEMA_PREFIXES)


# ---------------------------------------------------------------------------
# 7. Stars expanded, columns qualified — against the catalog, before any rule
#    about columns is applied
# ---------------------------------------------------------------------------


def _resolve_identifiers(
    statement: exp.Expression, source: SourcePolicy, tables: dict[str, TableRef]
) -> exp.Expression:
    """Expand ``SELECT *`` and attach every column to the table it came from.

    Done by sqlglot's qualifier against a schema built from the catalog — which
    means a star becomes exactly the columns the catalog knows, and a column
    that resolves to nothing is refused before any policy question is asked.

    The qualifier is used for *resolution*, never as the guard: it happily
    passes a query over a table it has never heard of, which is why tables were
    bound first. Its failures are re-diagnosed here, because "could not be
    resolved" is the same sentence for a column that does not exist and one that
    exists on two of the query's tables, and an agent repairs those differently.
    """
    schema = _sqlglot_schema(source.catalog, tables, source.dialect)
    qualified: exp.Expression | None = None
    try:
        qualified = qualify(
            statement,
            dialect=source.dialect,
            schema=schema,
            infer_schema=False,
            validate_qualify_columns=True,
        )
    except OptimizeError:
        # Diagnosed and raised *outside* the handler, so the optimizer's own
        # message — which quotes the query — is not left behind on __context__.
        qualified = None
    if qualified is None:
        raise _diagnose(statement, source.catalog, tables)
    return qualified


def _sqlglot_schema(catalog: Catalog, tables: dict[str, TableRef], dialect: str) -> MappingSchema:
    """The catalog, in the shape the qualifier reads, holding only what is used.

    Only the tables this statement names: a schema carrying every table in the
    database would let the qualifier resolve a column against one the query
    never mentioned, and quietly widen what a star expands to.
    """
    wanted = {(ref.schema, ref.table) for ref in tables.values()}
    schema: dict[str, dict[str, dict[str, str]]] = {}
    for table in catalog.tables:
        if (table.schema_name, table.table_name) not in wanted:
            continue
        schema.setdefault(table.schema_name, {})[table.table_name] = {
            column.name: column.data_type for column in table.columns
        }
    # cast: MappingSchema declares `dict[str, object]`, and a dict is invariant
    # in its value type, so the nested shape it actually wants does not fit the
    # annotation. The value is the shape sqlglot documents.
    return MappingSchema(cast("dict[str, object]", schema), dialect=dialect)


def _diagnose(
    statement: exp.Expression, catalog: Catalog, tables: dict[str, TableRef]
) -> PolicyViolation:
    """Say which column failed, and why, in the agent's terms.

    The qualifier says "could not be resolved" for a column that does not exist
    and for one that exists on two of the query's tables. Those are repaired
    differently — add the column, or qualify the name — so they are told apart
    here rather than passed along as one sentence.
    """
    used = set(tables.values())
    available: dict[str, set[str]] = {}
    columns_of: dict[TableRef, set[str]] = {}
    for table in catalog.tables:
        ref = TableRef(table.schema_name, table.table_name)
        if ref not in used:
            continue
        columns_of[ref] = {column.name.lower() for column in table.columns}
        for column in table.columns:
            available.setdefault(column.name.lower(), set()).add(table.table_name)

    aliases: dict[str, TableRef] = {}
    for node in statement.find_all(exp.Table):
        if isinstance(node.this, exp.Identifier):
            aliases[node.alias_or_name.lower()] = TableRef(node.db, node.name)

    for column in statement.find_all(exp.Column):
        if column.table:
            owner = aliases.get(column.table.lower())
            if owner is None or owner not in columns_of:
                # An alias belonging to a CTE or a derived table: what it holds
                # is decided by its own body, not by the catalog.
                continue
            if column.name.lower() not in columns_of[owner]:
                return PolicyViolation(
                    ViolationCode.UNKNOWN_COLUMN,
                    f"There is no column {column.name} on {owner}.",
                    subject=f"{owner}.{column.name}",
                )
            continue
        owners = available.get(column.name.lower(), set())
        if not owners:
            return PolicyViolation(
                ViolationCode.UNKNOWN_COLUMN,
                f"There is no column {column.name} on the tables this query reads.",
                subject=column.name,
            )
        if len(owners) > 1:
            return PolicyViolation(
                ViolationCode.AMBIGUOUS_COLUMN,
                f"{column.name} exists on more than one of these tables "
                f"({', '.join(sorted(owners))}). Qualify it with a table name.",
                subject=column.name,
            )

    return PolicyViolation(
        ViolationCode.UNRESOLVABLE,
        "This query could not be resolved against the catalog. Name each table "
        "and column explicitly.",
    )


# ---------------------------------------------------------------------------
# 8. What each column is allowed to be used for — anywhere it appears
# ---------------------------------------------------------------------------


def _apply_column_policy(
    qualified: exp.Expression, catalog: Catalog
) -> tuple[set[ColumnRef], set[ColumnRef]]:
    """Check every column reference in every scope against its policy.

    Scopes rather than a flat walk, because an alias means different things in
    different parts of one statement: ``SELECT (SELECT x.a FROM b x) FROM c x``
    has two ``x``. Resolving that by a single dictionary would attribute a column
    to the wrong table — and therefore judge it by the wrong policy, which is the
    one kind of mistake this module may not make.
    """
    policies = _policy_index(catalog)
    touched: set[ColumnRef] = set()
    masked: set[ColumnRef] = set()

    for scope in traverse_scope(qualified):
        for column in scope.columns:
            source = scope.sources.get(column.table)
            if not isinstance(source, exp.Table):
                # A CTE or a derived table. Its own scope is in this same walk,
                # and its columns were checked against real tables there.
                continue

            ref = ColumnRef(source.db, source.name, column.name)
            decision = policies.get(_key(ref))
            if decision is None:
                raise PolicyViolation(
                    ViolationCode.UNKNOWN_COLUMN,
                    f"There is no column {column.name} on {source.db}.{source.name}.",
                    subject=str(ref),
                )
            if decision == POLICY_DENY:
                raise PolicyViolation(
                    ViolationCode.DENIED_COLUMN,
                    f"{ref} may not be queried — not in the result, and not in a "
                    "condition either. Remove it from the query.",
                    subject=str(ref),
                )
            touched.add(ref)
            if decision == POLICY_MASK:
                masked.add(ref)

    return touched, masked


def _policy_index(catalog: Catalog) -> dict[tuple[str, str, str], str]:
    return {
        _key(ColumnRef(table.schema_name, table.table_name, column.name)): column.policy
        for table in catalog.tables
        for column in table.columns
    }


def _key(ref: ColumnRef) -> tuple[str, str, str]:
    return (ref.schema.lower(), ref.table.lower(), ref.column.lower())
