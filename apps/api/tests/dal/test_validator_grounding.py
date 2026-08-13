"""Rules 6-8: the catalog is the authority, and a policy applies everywhere.

Grounding is the anti-hallucination gate (architecture 7.1): a table the catalog
has never seen does not exist as far as this service is concerned, and the
refusal names it so the agent can pick a real one.
"""

from __future__ import annotations

import pytest
from catalog_fixture import build_source, refuse

from dataagent.dal.errors import ViolationCode
from dataagent.dal.policy import SourcePolicy
from dataagent.dal.validator import validate

# --- tables ----------------------------------------------------------------


def test_an_unknown_table_is_refused_by_name(either: SourcePolicy) -> None:
    violation = refuse("SELECT id FROM invoices", either)

    assert violation.code is ViolationCode.UNKNOWN_TABLE
    assert violation.subject == "invoices"
    assert "invoices" in violation.message


def test_an_unknown_table_in_a_join_is_refused(either: SourcePolicy) -> None:
    violation = refuse("SELECT o.id FROM orders o JOIN invoices i ON i.id = o.id", either)

    assert violation.code is ViolationCode.UNKNOWN_TABLE


def test_a_name_in_two_schemas_must_be_qualified(either: SourcePolicy) -> None:
    """`staff` exists in public and in archive. Guessing would be a coin toss
    over which rows an answer was built from."""
    violation = refuse("SELECT id FROM staff", either)

    assert violation.code is ViolationCode.AMBIGUOUS_TABLE
    assert "archive" in violation.message and "public" in violation.message


def test_qualifying_resolves_the_ambiguity(either: SourcePolicy) -> None:
    result = validate("SELECT id FROM archive.staff", source=either)

    assert str(result.tables[0]) == "archive.staff"


def test_the_canonical_sql_names_the_catalogs_spelling(either: SourcePolicy) -> None:
    """Case-folded input, canonical output: what runs is what the catalog said,
    so the executor and the audit row cannot disagree about what was read."""
    result = validate("SELECT ID FROM PUBLIC.ORDERS", source=either)

    assert str(result.tables[0]) == "public.orders"
    assert "PUBLIC" not in result.sql


def test_a_query_that_reads_no_table_is_refused(either: SourcePolicy) -> None:
    assert refuse("SELECT 1", either).code is ViolationCode.UNKNOWN_TABLE


# --- columns ---------------------------------------------------------------


def test_an_unknown_column_is_refused_by_name(either: SourcePolicy) -> None:
    violation = refuse("SELECT nosuchthing FROM orders", either)

    assert violation.code is ViolationCode.UNKNOWN_COLUMN
    assert violation.subject == "nosuchthing"


def test_an_unknown_column_in_a_predicate_is_refused(either: SourcePolicy) -> None:
    violation = refuse("SELECT id FROM orders WHERE nosuchthing = 1", either)

    assert violation.code is ViolationCode.UNKNOWN_COLUMN


def test_an_ambiguous_column_says_so(either: SourcePolicy) -> None:
    violation = refuse(
        "SELECT id FROM public.orders o JOIN customers c ON c.id = o.customer_id", either
    )

    assert violation.code is ViolationCode.AMBIGUOUS_COLUMN
    assert "orders" in violation.message and "customers" in violation.message


def test_a_qualified_column_is_fine(either: SourcePolicy) -> None:
    result = validate(
        "SELECT o.id, c.city FROM public.orders o JOIN customers c ON c.id = o.customer_id",
        source=either,
    )

    assert {str(column) for column in result.columns} == {
        "public.orders.id",
        "public.orders.customer_id",
        "public.customers.id",
        "public.customers.city",
    }


# --- stars -----------------------------------------------------------------


def test_a_star_is_expanded_against_the_catalog(either: SourcePolicy) -> None:
    result = validate("SELECT * FROM menu_items", source=either)

    assert "price" in result.sql
    assert "*" not in result.sql
    assert {column.column for column in result.columns} == {"id", "name", "price"}


def test_a_star_over_a_denied_column_is_refused(either: SourcePolicy) -> None:
    """Expansion happens first, so `SELECT *` cannot be a way around a policy —
    and the refusal names the column, so the agent can list the others."""
    violation = refuse("SELECT * FROM customers", either)

    assert violation.code is ViolationCode.DENIED_COLUMN
    assert violation.subject == "public.customers.tax_id"


def test_a_star_over_a_masked_column_is_allowed_and_recorded(either: SourcePolicy) -> None:
    result = validate("SELECT id, email FROM customers", source=either)

    assert {str(column) for column in result.masked} == {"public.customers.email"}
    assert result.touches_sensitive


def test_count_star_is_not_a_star_expansion(either: SourcePolicy) -> None:
    result = validate("SELECT COUNT(*) FROM customers", source=either)

    assert result.masked == ()


# --- denied columns, everywhere --------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT tax_id FROM customers",
        "SELECT id FROM customers WHERE tax_id = '1'",
        "SELECT id FROM customers ORDER BY tax_id",
        "SELECT COUNT(*) FROM customers GROUP BY tax_id",
        "SELECT COUNT(*) FROM customers HAVING MAX(tax_id) > '1'",
        "SELECT o.id FROM public.orders o JOIN customers c ON c.tax_id = 'x'",
        "SELECT id FROM public.orders WHERE customer_id IN "
        "(SELECT id FROM customers WHERE tax_id = '1')",
        "WITH c AS (SELECT tax_id FROM customers) SELECT * FROM c",
        "SELECT (SELECT MAX(tax_id) FROM customers) AS x FROM public.orders",
    ],
)
def test_a_denied_column_is_refused_wherever_it_appears(sql: str, either: SourcePolicy) -> None:
    """A predicate leaks values as surely as a projection: `WHERE tax_id LIKE
    'A%'` reads the column out one answer at a time."""
    violation = refuse(sql, either)

    assert violation.code is ViolationCode.DENIED_COLUMN
    assert violation.subject == "public.customers.tax_id"


def test_an_alias_does_not_launder_a_denied_column(either: SourcePolicy) -> None:
    violation = refuse("SELECT c.tax_id AS reference FROM customers c", either)

    assert violation.code is ViolationCode.DENIED_COLUMN


def test_a_cte_does_not_launder_a_denied_column(either: SourcePolicy) -> None:
    violation = refuse(
        "WITH safe AS (SELECT id, tax_id AS ref FROM customers) SELECT ref FROM safe", either
    )

    assert violation.code is ViolationCode.DENIED_COLUMN


def test_the_same_alias_in_two_scopes_is_judged_separately(either: SourcePolicy) -> None:
    """`x` means a different table in each half. Resolving aliases with one flat
    dictionary would attribute a column to the wrong table, and therefore judge
    it by the wrong policy."""
    result = validate(
        "SELECT (SELECT MAX(x.total) FROM public.orders x) AS a, x.name "
        "FROM menu_items x GROUP BY x.name",
        source=either,
    )

    assert {str(column) for column in result.columns} == {
        "public.orders.total",
        "public.menu_items.name",
    }


def test_a_cte_name_is_not_looked_up_in_the_catalog(either: SourcePolicy) -> None:
    result = validate(
        "WITH recent AS (SELECT id, total FROM orders) SELECT SUM(total) FROM recent",
        source=either,
    )

    assert {str(table) for table in result.tables} == {"public.orders"}


def test_a_cte_cannot_shadow_a_qualified_system_table(pg: SourcePolicy) -> None:
    """A bare name may be a CTE; a qualified one never is, so the shortcut that
    makes CTEs work cannot be used to smuggle a system table past the check."""
    violation = refuse(
        "WITH pg_user AS (SELECT id FROM orders) SELECT * FROM pg_catalog.pg_user", pg
    )

    assert violation.code is ViolationCode.SYSTEM_SCHEMA


def test_a_masked_column_in_a_predicate_is_allowed(either: SourcePolicy) -> None:
    """`mask` is not `deny`: aggregating over it is the intended use, and the
    values are masked on the way back out (WP5.2)."""
    result = validate(
        "SELECT COUNT(*) FROM customers WHERE email LIKE '%@example.com'", source=either
    )

    assert {str(column) for column in result.masked} == {"public.customers.email"}


def test_a_violation_carries_a_code_and_a_safe_message(either: SourcePolicy) -> None:
    violation = refuse("SELECT tax_id FROM customers", either)
    body = violation.as_dict()

    assert body["code"] == "denied_column"
    assert body["subject"] == "public.customers.tax_id"
    assert isinstance(body["message"], str) and body["message"]


def test_the_repr_does_not_carry_the_message(either: SourcePolicy) -> None:
    """A repr reaches logs by accident. The code and the subject are enough."""
    violation = refuse("SELECT id FROM invoices", either)

    assert "invoices" in repr(violation)
    assert violation.message not in repr(violation)


def test_the_result_is_a_validated_query_from_this_module(either: SourcePolicy) -> None:
    result = validate("SELECT id FROM orders", source=either)

    assert result.query.origin == "dataagent.dal.validator"
    assert result.query.dialect == either.dialect


def test_the_two_dialects_produce_their_own_spelling() -> None:
    """One catalog, two engines, two canonical statements — the identifiers are
    quoted the way each engine quotes them, which is what a connector will run."""
    postgres = validate("SELECT id FROM orders", source=build_source("pg")).sql
    sqlserver = validate("SELECT id FROM orders", source=build_source("mssql")).sql

    assert '"orders"' in postgres
    assert "[orders]" in sqlserver


def test_a_qualified_unknown_column_names_its_table(either: SourcePolicy) -> None:
    """`o.nosuch` is a different repair from a bare `nosuch`: the agent has told
    us which table it means, so the refusal answers about that table."""
    violation = refuse("SELECT o.nosuch FROM orders o", either)

    assert violation.code is ViolationCode.UNKNOWN_COLUMN
    assert violation.subject == "public.orders.nosuch"
