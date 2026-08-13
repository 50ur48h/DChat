"""Rules 1-5: what shape of statement is allowed to exist at all.

Everything here is decided before the catalog is consulted, and everything here
is refused with a code the adversarial corpus (WP5.3) will name.
"""

from __future__ import annotations

import pytest
from catalog_fixture import build_source, refuse

from dataagent.dal import validator
from dataagent.dal.errors import ViolationCode
from dataagent.dal.policy import SourcePolicy
from dataagent.dal.validator import validate

# --- one statement ---------------------------------------------------------


def test_a_plain_select_is_allowed(either: SourcePolicy) -> None:
    result = validate("SELECT id FROM orders", source=either)

    assert "orders" in result.sql
    assert result.tables == (result.tables[0],)
    assert str(result.tables[0]) == "public.orders"


def test_a_second_statement_is_refused(either: SourcePolicy) -> None:
    violation = refuse("SELECT id FROM orders; DROP TABLE orders", either)

    assert violation.code is ViolationCode.MULTIPLE_STATEMENTS


def test_a_trailing_semicolon_is_not_a_second_statement(either: SourcePolicy) -> None:
    """The commonest false positive in a naive splitter, and it must not be one."""
    assert validate("SELECT id FROM orders;", source=either).sql


def test_a_comment_cannot_hide_a_second_statement(either: SourcePolicy) -> None:
    """`--` and `/* */` are the classic ways to make a splitter miscount. The
    parser is the thing that counts here, so both are simply comments."""
    assert validate("SELECT id FROM orders -- ; DROP TABLE orders", source=either).sql
    assert validate("SELECT id FROM orders /* ; DROP TABLE orders */", source=either).sql


def test_a_comment_is_not_carried_into_the_canonical_sql(either: SourcePolicy) -> None:
    """What is executed is what was validated, with nothing riding along."""
    result = validate("SELECT id /* rm -rf */ FROM orders", source=either)

    assert "rm -rf" not in result.sql


def test_nothing_at_all_is_refused(either: SourcePolicy) -> None:
    assert refuse("   ", either).code is ViolationCode.EMPTY_STATEMENT
    assert refuse("-- just a comment", either).code is ViolationCode.EMPTY_STATEMENT


def test_gibberish_is_refused_without_quoting_it_back(either: SourcePolicy) -> None:
    violation = refuse("SELECT FROM WHERE 'sekrit-value'", either)

    assert violation.code is ViolationCode.PARSE_ERROR
    assert "sekrit-value" not in violation.message


# --- statement type --------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO orders (id) VALUES (1)",
        "UPDATE orders SET total = 0",
        "DELETE FROM orders",
        "CREATE TABLE t (a int)",
        "DROP TABLE orders",
        "ALTER TABLE orders ADD COLUMN x int",
        "GRANT SELECT ON orders TO someone",
        "COMMIT",
    ],
)
def test_a_statement_that_is_not_a_select_is_refused(sql: str, either: SourcePolicy) -> None:
    violation = refuse(sql, either)

    assert violation.code in {
        ViolationCode.STATEMENT_NOT_READ_ONLY,
        ViolationCode.WRITE_OPERATION,
    }


def test_a_union_is_a_select(either: SourcePolicy) -> None:
    """Set operations read. Refusing them would push the agent into worse SQL."""
    result = validate(
        "SELECT id FROM public.orders UNION ALL SELECT id FROM archive.orders_2025",
        source=either,
    )

    assert {str(table) for table in result.tables} == {"public.orders", "archive.orders_2025"}


def test_union_smuggling_is_still_caught(either: SourcePolicy) -> None:
    """The second arm of a UNION is as much a statement as the first."""
    violation = refuse("SELECT id FROM orders UNION SELECT tax_id FROM customers", either)

    assert violation.code is ViolationCode.DENIED_COLUMN


def test_explain_of_a_select_is_allowed(either: SourcePolicy) -> None:
    result = validate("EXPLAIN SELECT id FROM orders", source=either)

    assert result.sql.startswith("EXPLAIN SELECT")


def test_explain_analyze_is_refused(either: SourcePolicy) -> None:
    """ANALYZE runs the query it claims to be planning."""
    assert refuse("EXPLAIN ANALYZE SELECT id FROM orders", either).code in {
        ViolationCode.STATEMENT_NOT_READ_ONLY,
        ViolationCode.PARSE_ERROR,
    }


def test_explain_of_a_write_is_refused(either: SourcePolicy) -> None:
    assert refuse("EXPLAIN DELETE FROM orders", either).code in {
        ViolationCode.STATEMENT_NOT_READ_ONLY,
        ViolationCode.WRITE_OPERATION,
    }


# --- writes hiding inside a read -------------------------------------------


def test_a_write_inside_a_cte_is_refused(pg: SourcePolicy) -> None:
    """The case that makes a top-level type check worthless: this parses as a
    SELECT, and PostgreSQL would execute the INSERT."""
    violation = refuse(
        "WITH moved AS (INSERT INTO orders (id) VALUES (1) RETURNING id) SELECT id FROM moved", pg
    )

    assert violation.code is ViolationCode.WRITE_OPERATION


def test_a_write_inside_a_subquery_is_refused(pg: SourcePolicy) -> None:
    violation = refuse(
        "SELECT id FROM orders WHERE id IN (WITH d AS (DELETE FROM orders RETURNING id) "
        "SELECT id FROM d)",
        pg,
    )

    assert violation.code is ViolationCode.WRITE_OPERATION


def test_select_into_is_a_write(either: SourcePolicy) -> None:
    assert refuse("SELECT * INTO copied FROM orders", either).code is ViolationCode.WRITE_OPERATION


def test_a_locking_read_is_refused(pg: SourcePolicy) -> None:
    """FOR UPDATE takes row locks on a database we promised only to read."""
    assert refuse("SELECT id FROM orders FOR UPDATE", pg).code is ViolationCode.WRITE_OPERATION


def test_session_settings_cannot_be_changed(pg: SourcePolicy) -> None:
    """`SET` is how a read-only session stops being one."""
    assert refuse("SET default_transaction_read_only = off", pg).code in {
        ViolationCode.WRITE_OPERATION,
        ViolationCode.STATEMENT_NOT_READ_ONLY,
    }


def test_syntax_the_parser_does_not_understand_is_refused(pg: SourcePolicy) -> None:
    """sqlglot turns what it cannot parse into a Command rather than an error.
    Approving those would approve exactly the statements least understood."""
    assert refuse("DO $$ BEGIN PERFORM 1; END $$", pg).code in {
        ViolationCode.WRITE_OPERATION,
        ViolationCode.STATEMENT_NOT_READ_ONLY,
    }


# --- system schemas and other databases ------------------------------------


@pytest.mark.parametrize(
    ("sql", "engine"),
    [
        ("SELECT * FROM pg_catalog.pg_user", "pg"),
        ("SELECT * FROM information_schema.tables", "pg"),
        ("SELECT * FROM sys.objects", "mssql"),
        ("SELECT * FROM INFORMATION_SCHEMA.COLUMNS", "mssql"),
    ],
)
def test_the_engines_own_dictionary_is_not_readable(sql: str, engine: str) -> None:
    violation = refuse(sql, build_source(engine))

    assert violation.code is ViolationCode.SYSTEM_SCHEMA


def test_a_system_schema_in_a_subquery_is_refused(pg: SourcePolicy) -> None:
    violation = refuse(
        "SELECT id FROM orders WHERE id IN (SELECT oid FROM pg_catalog.pg_class)", pg
    )

    assert violation.code is ViolationCode.SYSTEM_SCHEMA


def test_another_database_on_the_same_server_is_refused(mssql: SourcePolicy) -> None:
    violation = refuse("SELECT name FROM master.sys.databases", mssql)

    assert violation.code is ViolationCode.CROSS_DATABASE


# --- functions -------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "engine"),
    [
        ("SELECT pg_read_file('/etc/passwd')", "pg"),
        ("SELECT pg_sleep(10)", "pg"),
        ("SELECT id FROM orders WHERE id = (SELECT pg_sleep(10))::int", "pg"),
        ("SELECT xp_cmdshell('dir')", "mssql"),
        ("SELECT * FROM OPENROWSET('a', 'b', 'c')", "mssql"),
    ],
)
def test_an_escape_hatch_function_is_refused(sql: str, engine: str) -> None:
    violation = refuse(sql, build_source(engine))

    assert violation.code in {ViolationCode.DENIED_FUNCTION, ViolationCode.TABLE_FUNCTION}


def test_a_function_nobody_can_vouch_for_is_refused(pg: SourcePolicy) -> None:
    """Not on any deny list — that is the point. An unrecognised function is
    refused for being unrecognised, so the list is a courtesy, not the guard."""
    violation = refuse("SELECT some_extension_fn(id) FROM orders", pg)

    assert violation.code is ViolationCode.UNKNOWN_FUNCTION
    assert "some_extension_fn" in violation.message


def test_ordinary_functions_still_work(either: SourcePolicy) -> None:
    """Strict has to stay usable: this is the shape of most real questions."""
    result = validate(
        "SELECT COUNT(*) AS n, SUM(total) AS revenue FROM orders WHERE ordered_at >= '2026-07-01'",
        source=either,
    )

    assert "COUNT" in result.sql.upper()


def test_a_table_function_is_refused(pg: SourcePolicy) -> None:
    assert refuse("SELECT * FROM generate_series(1, 10)", pg).code is ViolationCode.TABLE_FUNCTION


# --- limits on the checker itself ------------------------------------------


def test_a_statement_too_deep_to_check_is_refused(either: SourcePolicy) -> None:
    """Found while measuring coverage: 300 nested parentheses raised a raw
    RecursionError out of the parser, which is a way past every rule below it
    — none of them ever ran — and a way to take the process down from a text
    box. It is now an ordinary refusal."""
    nested = "SELECT id FROM orders WHERE id = " + "(" * 300 + "1" + ")" * 300

    violation = refuse(nested, either)

    assert violation.code is ViolationCode.TOO_COMPLEX


def test_a_statement_longer_than_the_limit_is_refused(either: SourcePolicy) -> None:
    padded = "SELECT id FROM orders WHERE city = '" + "x" * 20_001 + "'"

    assert refuse(padded, either).code is ViolationCode.TOO_COMPLEX


def test_parentheses_inside_a_literal_are_not_nesting(either: SourcePolicy) -> None:
    """Depth is counted from tokens, not characters, so a string full of
    brackets is a string."""
    result = validate("SELECT id FROM customers WHERE city = '" + "(" * 60 + "'", source=either)

    assert result.sql


def test_ordinary_nesting_is_unaffected(either: SourcePolicy) -> None:
    result = validate(
        "SELECT id FROM public.orders WHERE customer_id IN "
        "(SELECT id FROM customers WHERE city IN (SELECT city FROM customers))",
        source=either,
    )

    assert result.sql


# --- the remaining EXPLAIN shapes ------------------------------------------


def test_explain_cannot_explain_an_explain(either: SourcePolicy) -> None:
    assert refuse("EXPLAIN EXPLAIN SELECT id FROM orders", either).code is (
        ViolationCode.STATEMENT_NOT_READ_ONLY
    )


def test_explain_with_nothing_after_it_is_refused(either: SourcePolicy) -> None:
    assert refuse("EXPLAIN", either).code is ViolationCode.STATEMENT_NOT_READ_ONLY


def test_an_unterminated_string_is_a_parse_error(either: SourcePolicy) -> None:
    """The tokenizer fails before the parser does; both end in the same refusal
    rather than in whatever exception the library felt like raising."""
    assert refuse("SELECT 'unterminated FROM orders", either).code is ViolationCode.PARSE_ERROR


def test_a_qualified_unknown_table_names_both_parts(either: SourcePolicy) -> None:
    violation = refuse("SELECT id FROM public.nosuchtable", either)

    assert violation.code is ViolationCode.UNKNOWN_TABLE
    assert violation.subject == "public.nosuchtable"


def test_a_recursion_failure_below_still_reaches_the_caller_as_a_refusal(
    either: SourcePolicy, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two ceilings above catch the shapes that can be seen coming. This is
    the backstop for the ones that cannot: whatever runs out of stack, a caller
    of the DAL sees a violation, never an interpreter-level error."""

    def boom(sql: str, *, source: SourcePolicy) -> object:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(validator, "_validate", boom)

    violation = refuse("SELECT id FROM orders", either)

    assert violation.code is ViolationCode.TOO_COMPLEX
