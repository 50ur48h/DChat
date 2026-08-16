"""Observe, and the duplicate rule's hash — the loop's two pure functions.

Both are deterministic on purpose (**D-024**), and this file is the payoff:
architecture 4.4 put Observe on a cheap model, which would have made "what does
the state carry after a query" a question only a provider could answer. In code
it is a function, and a function can be pinned exactly.

No database, no model, no fixtures.
"""

from __future__ import annotations

from dataagent.agent.loop import proposed_hash, summarize
from dataagent.agent.tools.sql import RunSqlOut


def _result(**overrides: object) -> RunSqlOut:
    values: dict[str, object] = {
        "execution_id": "x1",
        "columns": ["order_count"],
        "rows": [[3718]],
        "row_count": 1,
        "truncated": False,
        "masked_columns": [],
        "duration_ms": 12,
    }
    values.update(overrides)
    return RunSqlOut.model_validate(values)


# ---------------------------------------------------------------------------
# What the state is told about a result
# ---------------------------------------------------------------------------


def test_a_single_aggregate_keeps_its_value() -> None:
    """The common case, and the one where the value *is* the finding. A summary
    that said "1 row" about a count would throw away the answer."""
    summary = summarize(_result(), "count July orders")

    assert "3718" in summary
    assert "order_count" in summary


def test_a_wide_result_is_described_by_its_shape_alone() -> None:
    """The prompt must not grow with the size of a customer's table — and a
    summary carrying a hundred names would be carrying personal data into every
    later prompt, which is exactly what 4.4 forbids."""
    summary = summarize(
        _result(
            columns=["city", "name", "email", "total"],
            rows=[["Wellington", "Ada", "a***@e***.com", 4]],
            row_count=250,
        ),
        "list customers",
    )

    assert "250 rows" in summary
    assert "Wellington" not in summary
    assert "a***@e***.com" not in summary, "not even the masked value belongs in the state"


def test_a_single_wide_row_is_also_only_described() -> None:
    """One row can still be too wide to inline; the cell count is the rule, not
    the row count."""
    summary = summarize(
        _result(columns=["a", "b", "c", "d"], rows=[[1, 2, 3, 4]], row_count=1), "wide"
    )

    assert "1 row" in summary
    assert "a=" not in summary


def test_no_rows_is_said_plainly() -> None:
    """A query that matched nothing is information, not an error — and the next
    iteration should be able to act on it."""
    summary = summarize(_result(rows=[], row_count=0), "count July orders")

    assert "no rows matched" in summary


def test_a_masked_column_is_named_as_masked() -> None:
    """So a model reasoning about the result knows why a value looks the way it
    does, rather than treating `k***@e***.com` as data."""
    summary = summarize(
        _result(
            columns=["city", "email"],
            rows=[["Wellington", "k***"]],
            row_count=40,
            masked_columns=["email"],
        ),
        "by city",
    )

    assert "masked by policy" in summary


# ---------------------------------------------------------------------------
# Recognising a query already run
# ---------------------------------------------------------------------------


def test_the_same_statement_written_differently_still_matches() -> None:
    """Whitespace and case are not a different question. A loop that re-proposed
    its last query with a newline moved would otherwise pay for it again."""
    a = proposed_hash("SELECT count(*) FROM orders")
    b = proposed_hash("select  COUNT(*)\n  from orders  ")

    assert a == b


def test_a_different_statement_hashes_differently() -> None:
    assert proposed_hash("SELECT 1 FROM a") != proposed_hash("SELECT 1 FROM b")


def test_the_same_question_written_two_ways_is_not_caught() -> None:
    """**B-049**, pinned rather than glossed.

    The canonical form only exists after the DAL has validated, by which point
    the query is already spent — so this rule works on the proposal, and two
    genuinely different spellings of one question get past it. Asserted so the
    limitation is visible in the suite, and so closing B-049 has a test that
    changes.
    """
    assert proposed_hash("SELECT count(*) FROM orders") != proposed_hash(
        "SELECT COUNT(1) FROM public.orders"
    )
