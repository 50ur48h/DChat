"""How the data access layer refuses (architecture Part 7.4, 7.5).

A refusal has two audiences and they want different things.

The **agent** gets ``message``. It is written to be repaired from: it names the
identifier at fault and says what would be acceptable instead, because the model
rewriting its query is the normal path, not an error path. It is assembled here
from a code and an identifier — never from the raw SQL, and never from a value
read out of anyone's database, so a message can be shown, logged and stored
without checking what happened to be in it.

The **platform** gets ``code``. It is a fixed, machine-readable string: audit
rows group by it, the eval harness asserts on it, and the adversarial corpus in
WP5.3 names the code each attack must produce. Adding a code is a deliberate act
— the set is asserted in a test so a typo cannot quietly become a new category.

Nothing here says *why* a rule exists in the sense of internals: "this database
has no table named orders" is fine, "the catalog snapshot for source X is stale"
is not. The model is assumed hostile (7.4); it is told what it may do, not how
the thing telling it works.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["PolicyViolation", "ViolationCode"]


class ViolationCode(StrEnum):
    """Every way the validator can say no. Append-only in spirit: WP5.3's corpus
    and the audit trail both key off these strings."""

    #: The statement did not parse in the data source's own dialect.
    PARSE_ERROR = "parse_error"
    #: Nothing to run: empty string, or only a comment.
    EMPTY_STATEMENT = "empty_statement"
    #: More than one statement. The classic smuggling shape, refused before
    #: anything else is inspected.
    MULTIPLE_STATEMENTS = "multiple_statements"
    #: The top-level statement is not a SELECT (or an EXPLAIN of one).
    STATEMENT_NOT_READ_ONLY = "statement_not_read_only"
    #: A write, a schema change or transaction control found *anywhere* in the
    #: tree — including inside a CTE, which is where a top-level-only check
    #: fails.
    WRITE_OPERATION = "write_operation"
    #: A schema the engine keeps for itself. The agent reads metadata from the
    #: catalog service, never from the database's own dictionary.
    SYSTEM_SCHEMA = "system_schema"
    #: A function on the deny list: file access, sleeps, remote execution.
    DENIED_FUNCTION = "denied_function"
    #: A function this validator cannot identify. Refused because a function it
    #: cannot name is a function it cannot vouch for.
    UNKNOWN_FUNCTION = "unknown_function"
    #: A table-valued function where a table belongs.
    TABLE_FUNCTION = "table_function"
    #: A three-part name reaching for another database on the same server.
    CROSS_DATABASE = "cross_database"
    #: No such table in this data source's catalog. The anti-hallucination gate.
    UNKNOWN_TABLE = "unknown_table"
    #: The name matches catalogued tables in more than one schema.
    AMBIGUOUS_TABLE = "ambiguous_table"
    #: No such column on any table this query reads.
    UNKNOWN_COLUMN = "unknown_column"
    #: The name exists on more than one of this query's tables.
    AMBIGUOUS_COLUMN = "ambiguous_column"
    #: An Admin marked this column unqueryable. Rejected wherever it appears —
    #: a predicate leaks values just as surely as a projection does.
    DENIED_COLUMN = "denied_column"
    #: Too long, or nested too deeply to be checked without running the
    #: validator itself out of stack. A limit on the checker, not on SQL.
    TOO_COMPLEX = "too_complex"
    #: The query parsed and broke no rule, and still could not be resolved
    #: against the catalog. Fails closed rather than executing something whose
    #: shape is not understood.
    UNRESOLVABLE = "unresolvable"


# N818 wants an `Error` suffix. The name is `PolicyViolation` in the plan
# (WP5.1) and in architecture 7.4, it is what the audit rows and the agent's
# repair prompt will call it, and a class renamed to satisfy a lint rule would
# make three documents wrong about the same thing.
class PolicyViolation(Exception):  # noqa: N818
    """A query that will not be run, and the reason, in both languages.

    Carries no exception chain, and that takes care: the validator builds one of
    these inside an ``except`` block and raises it *outside*. ``raise … from
    None`` would not be enough — it stops a traceback from printing the parser's
    error while leaving the object on ``__context__``, and sqlglot's message
    quotes the SQL it choked on, literal values included.
    """

    def __init__(self, code: ViolationCode, message: str, *, subject: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        #: The identifier the refusal is about, when there is one: a table name,
        #: a column, a function. Structured so a caller can act on it without
        #: parsing prose.
        self.subject = subject

    def as_dict(self) -> dict[str, str | None]:
        """The shape the agent, the audit row and the API error body all use."""
        return {"code": str(self.code), "message": self.message, "subject": self.subject}

    def __repr__(self) -> str:
        return f"PolicyViolation(code={self.code!r}, subject={self.subject!r})"
