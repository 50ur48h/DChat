"""The refusal vocabulary, pinned.

Two things are asserted here and both are about WP5.3, which is written against
these strings: the adversarial corpus names the code each attack must produce,
and audit rows group by them.

1. **The set is exactly this.** A new code is a deliberate addition with a test
   line, not a typo that quietly becomes a category nobody counts.
2. **Every code has a query that produces it.** A code no input can reach is a
   branch nobody has run — and in this module, an unreachable branch is a rule
   that is not actually enforced.
"""

from __future__ import annotations

import pytest
from catalog_fixture import refuse

from dataagent.dal.errors import PolicyViolation, ViolationCode
from dataagent.dal.policy import SourcePolicy

#: One statement per code. Kept in the order the validator applies its rules, so
#: this table reads as the pipeline from architecture 7.1 with an example each.
CASES: dict[ViolationCode, str] = {
    ViolationCode.PARSE_ERROR: "SELECT FROM WHERE",
    ViolationCode.EMPTY_STATEMENT: "",
    ViolationCode.MULTIPLE_STATEMENTS: "SELECT id FROM orders; SELECT id FROM orders",
    ViolationCode.STATEMENT_NOT_READ_ONLY: "UPDATE orders SET total = 0",
    ViolationCode.WRITE_OPERATION: (
        "WITH w AS (INSERT INTO orders (id) VALUES (1) RETURNING id) SELECT id FROM w"
    ),
    ViolationCode.SYSTEM_SCHEMA: "SELECT * FROM information_schema.tables",
    ViolationCode.DENIED_FUNCTION: "SELECT pg_sleep(1)",
    ViolationCode.UNKNOWN_FUNCTION: "SELECT nobody_knows(id) FROM orders",
    ViolationCode.TABLE_FUNCTION: "SELECT * FROM generate_series(1, 3)",
    ViolationCode.CROSS_DATABASE: "SELECT id FROM other_db.public.orders",
    ViolationCode.UNKNOWN_TABLE: "SELECT id FROM invoices",
    ViolationCode.AMBIGUOUS_TABLE: "SELECT id FROM staff",
    ViolationCode.UNKNOWN_COLUMN: "SELECT nosuchthing FROM orders",
    ViolationCode.AMBIGUOUS_COLUMN: (
        "SELECT id FROM public.orders o JOIN customers c ON c.id = o.customer_id"
    ),
    ViolationCode.DENIED_COLUMN: "SELECT tax_id FROM customers",
    ViolationCode.TOO_COMPLEX: "SELECT id FROM orders WHERE id = " + "(" * 300 + "1" + ")" * 300,
    # Parses, breaks no rule, and still cannot be resolved: one alias for two
    # tables. Fails closed rather than running something not understood.
    ViolationCode.UNRESOLVABLE: "SELECT o.id FROM public.orders o JOIN customers o ON o.id = o.id",
}


def test_the_set_of_codes_is_exactly_this() -> None:
    assert set(ViolationCode) == set(CASES)


@pytest.mark.parametrize(("code", "sql"), CASES.items(), ids=lambda value: str(value)[:40])
def test_each_code_has_a_statement_that_produces_it(
    code: ViolationCode, sql: str, pg: SourcePolicy
) -> None:
    assert refuse(sql, pg).code is code


@pytest.mark.parametrize("sql", CASES.values(), ids=lambda value: str(value)[:40])
def test_no_refusal_leaks_anything_but_identifiers(sql: str, pg: SourcePolicy) -> None:
    """A message is shown to the model and stored in an audit row, so it may
    name tables, columns and functions — and must not carry anything else:
    no file paths from an internal exception, no dialect internals, no SQL
    quoted back with a literal still in it."""
    violation = refuse(sql, pg)

    assert violation.message
    assert "Traceback" not in violation.message
    assert "sqlglot" not in violation.message.lower()
    assert "\\" not in violation.message


def test_a_violation_never_chains_the_parser_error(pg: SourcePolicy) -> None:
    """`raise ... from error` would keep sqlglot's message — which quotes the
    SQL, literals included — alive in __cause__, where the next traceback puts
    it in a log file."""
    violation = refuse("SELECT FROM WHERE 'a-value'", pg)

    assert violation.__cause__ is None
    assert violation.__context__ is None


def test_the_codes_are_plain_strings_over_the_wire() -> None:
    """A StrEnum, so an audit row and a JSON body carry `denied_column` rather
    than `ViolationCode.DENIED_COLUMN`."""
    violation = PolicyViolation(ViolationCode.DENIED_COLUMN, "no", subject="a.b.c")

    assert violation.as_dict() == {"code": "denied_column", "message": "no", "subject": "a.b.c"}
    assert f"{ViolationCode.DENIED_COLUMN}" == "denied_column"
