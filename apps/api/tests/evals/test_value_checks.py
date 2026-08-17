"""The eval harness's own checking, tested (**B-066**).

The harness had no tests. It was exercised only by running it, and only in
FakeLLM mode — where the SQL comes from `golden.yaml`, so `expect.value_of`
always names a column the result really has. The defect was therefore invisible
in the one place it was ever run, and became visible the first time real money
was spent: five of the eight failures in a 12/20 live run were the check reading
`SELECT ... AS cancellation_rate` as a missing `cancelled_rate` column.

So the point of this file is not the fallback rules. It is that the *checker* is
now a thing with tests, exercised without a database, a model or a dollar.

The most important test here is
``test_a_named_column_with_the_wrong_value_is_never_rescued``. A fix that made
the harness more forgiving could easily make it forgiving of the product too,
and that would turn the twenty golden questions from a required check into
decoration.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_EVALS = Path(__file__).resolve().parents[4] / "ops" / "evals"
if str(_EVALS) not in sys.path:
    sys.path.insert(0, str(_EVALS))

# `check_case` is the whole checker and takes rows from the database; these tests
# drive the two halves that are pure — the value matcher, and the refusal rule
# through a hand-built `Result`.
from runner import MAX_SCAN_COLUMNS, Result, check_case, match_value  # noqa: E402


def _artifact(columns: list[str], rows: list[list[Any]]) -> dict[str, dict[str, Any]]:
    """One stored result, in the shape `artifacts_for` returns."""
    return {"execution-1": {"columns": columns, "rows": rows}}


# ---------------------------------------------------------------------------
# Rule 1 — by name, and it wins
# ---------------------------------------------------------------------------


def test_the_named_column_is_found_and_compared() -> None:
    """The FakeLLM path, unchanged: scripted SQL names the column the case
    expects, and that is an exact check."""
    match = match_value(_artifact(["order_count"], [[3718]]), "order_count", 3718, 0)

    assert match.ok
    assert match.how == "column"
    assert not match.weaker


def test_the_column_name_is_matched_case_insensitively() -> None:
    """A dialect or a driver may hand back `ORDER_COUNT`, and that is the same
    column."""
    match = match_value(_artifact(["ORDER_COUNT"], [[3718]]), "order_count", 3718, 0)

    assert match.ok
    assert match.how == "column"


def test_a_named_column_with_the_wrong_value_is_never_rescued() -> None:
    """**The test this fix exists to be safe under.**

    The fallbacks make the harness tolerant of a model's *naming*. They must not
    make it tolerant of a wrong *number*: a result that has the expected column
    and the wrong value in it fails, even when another cell beside it happens to
    hold the right one. Without this, the twenty golden questions stop being a
    check.
    """
    artifacts = _artifact(["order_count", "something_else"], [[9999, 3718]])

    match = match_value(artifacts, "order_count", 3718, 0)

    assert not match.ok
    assert match.how == "column", "the named column decided it, and nothing looked further"
    assert match.seen == 9999


# ---------------------------------------------------------------------------
# Rule 2 — the only value there is
# ---------------------------------------------------------------------------


def test_a_single_aggregate_is_compared_whatever_the_model_called_it() -> None:
    """B-066 itself. `SELECT ... AS cancellation_rate` is a correct answer to
    *"what proportion of orders were cancelled?"*, and the old check called it a
    missing column."""
    match = match_value(_artifact(["cancellation_rate"], [[0.0721]]), "cancelled_rate", 0.0721, 0)

    assert match.ok
    assert match.how == "only-value"
    assert match.weaker, "a pass by a weaker rule, and the report says so"


def test_a_single_aggregate_with_the_wrong_value_still_fails() -> None:
    """The fallback finds the value or it does not; it does not accept one."""
    match = match_value(_artifact(["cancellation_rate"], [[0.5]]), "cancelled_rate", 0.0721, 0)

    assert not match.ok


def test_tolerance_applies_to_the_fallback_as_it_does_to_the_column() -> None:
    match = match_value(_artifact(["rate"], [[0.07215]]), "cancelled_rate", 0.0721, 0.001)

    assert match.ok


# ---------------------------------------------------------------------------
# Rule 3 — a cell of a single row
# ---------------------------------------------------------------------------


def test_a_ranking_answer_is_found_among_the_cells_of_its_one_row() -> None:
    """*"Which store brought in the most revenue?"* is `ORDER BY ... LIMIT 1`,
    so the answer is one row of two or three columns and the model names them as
    it pleases."""
    match = match_value(
        _artifact(["shop", "total"], [["store-3", 41022.55]]), "store_id", "store-3", 0
    )

    assert match.ok
    assert match.how == "row-cell"


def test_a_wide_result_is_not_scanned_cell_by_cell() -> None:
    """A bound on the coincidence. Past a few columns, finding the number
    somewhere says less than it seems to, and a check that is nearly always
    satisfied is not a check."""
    columns = [f"c{n}" for n in range(MAX_SCAN_COLUMNS + 1)]
    row = [0] * MAX_SCAN_COLUMNS + [3718]

    match = match_value(_artifact(columns, [row]), "order_count", 3718, 0)

    assert not match.ok
    assert match.how == "absent"


def test_a_multi_row_result_is_not_scanned() -> None:
    """Only a single row is unambiguous. In fifty rows the expected value will
    turn up somewhere sooner or later whatever the query did."""
    match = match_value(
        _artifact(["store", "revenue"], [["store-1", 10.0], ["store-3", 3718]]),
        "store_id",
        3718,
        0,
    )

    assert not match.ok


# ---------------------------------------------------------------------------
# truth_any — one thing with two right spellings
# ---------------------------------------------------------------------------


def test_either_spelling_of_the_same_answer_is_accepted() -> None:
    """*"Which store brought in the most revenue?"* is answered by `3` and by
    `"Northgate"`. The second is the better answer — an internal key reaching a
    reader is itself a defect (B-061, B-020) — and the old case scored it as a
    failure because it accepted only the id."""
    by_name = _artifact(["store_name", "total_revenue"], [["Northgate", 783916.69]])
    by_id = _artifact(["store_id", "revenue"], [[3, 783916.69]])

    assert match_value(by_name, "store_id", [3, "Northgate"], 0).ok
    assert match_value(by_id, "store_id", [3, "Northgate"], 0).ok


def test_a_different_store_is_still_wrong_under_truth_any() -> None:
    """Widening what counts as the right answer must not widen it to any
    answer."""
    wrong = _artifact(["store_name", "total_revenue"], [["Riccarton", 336756.52]])

    assert not match_value(wrong, "store_id", [3, "Northgate"], 0).ok


def test_truth_any_reads_every_path_and_says_both_in_a_failure() -> None:
    result = Result(id=4, question="Which store brought in the most revenue?")
    case = {
        "truth_any": ["revenue.top_store_by_revenue", "revenue.top_store_by_name"],
        "expect": {"value_of": "store_id"},
    }
    truths = {"revenue": {"top_store_by_revenue": 3, "top_store_by_name": "Northgate"}}

    check_case(case, truths, result, rows=[], artifacts={}, live=False)

    assert not result.passed
    assert "'Northgate'" in " ".join(result.failures)
    assert "3" in " ".join(result.failures)


# ---------------------------------------------------------------------------
# Saying what went wrong
# ---------------------------------------------------------------------------


def test_the_failure_names_the_columns_that_were_actually_returned() -> None:
    """ "No result had a column called 'cancelled_rate'" is a sentence nobody can
    act on: it does not say whether the model aliased the column, answered a
    different question, or ran nothing at all."""
    result = Result(id=8, question="What proportion of orders were cancelled?")
    case = {"truth": "orders.cancelled_rate", "expect": {"value_of": "cancelled_rate"}}
    truths = {"orders": {"cancelled_rate": 0.0721}}

    check_case(
        case,
        truths,
        result,
        rows=[],
        artifacts=_artifact(["a", "b", "c", "d", "e"], [[1, 2, 3, 4, 5]]),
        live=False,
    )

    assert not result.passed
    assert "[a, b, c, d, e]" in " ".join(result.failures)


def test_nothing_returned_at_all_says_so() -> None:
    result = Result(id=8, question="q")
    case = {"truth": "x", "expect": {"value_of": "rate"}}

    check_case(case, {"x": 1}, result, rows=[], artifacts={}, live=False)

    assert not result.passed
    assert "no result returned any rows" in " ".join(result.failures)


# ---------------------------------------------------------------------------
# may_refuse — a question with two right answers
# ---------------------------------------------------------------------------


def _refusal_case() -> dict[str, Any]:
    return {
        "expect": {"may_refuse": True, "value_is": 0, "value_of": "order_count", "must_cite": 1}
    }


def test_an_allowed_refusal_is_not_a_failure() -> None:
    """#19 asks about a month past the end of the data. Answering **zero** is
    right; saying the data ends on 2026-07-31 is better, and is D-027's last
    clause working exactly as written."""
    result = Result(id=19, question="How many orders were placed in November 2026?")
    result.answered = False
    result.status = "completed"
    result.answer = "The orders data ends on 2026-07-31, so it does not cover November 2026."

    check_case(_refusal_case(), {}, result, rows=[], artifacts={}, live=False)

    assert result.passed
    assert result.refused


def test_a_failed_run_is_not_an_honest_refusal() -> None:
    """The runner reserves `failed` for the platform breaking, and a refusal is
    an *ending* rather than a failure (WP7.2b). The harness has to hold the same
    line: without this, `may_refuse` would turn an outage into a pass, since a
    crash leaves an error message that is easily longer than twenty
    characters."""
    result = Result(id=19, question="How many orders were placed in November 2026?")
    result.answered = False
    result.status = "failed"
    result.answer = "The model provider returned an error and the run could not continue."

    check_case(_refusal_case(), {}, result, rows=[], artifacts={}, live=False)

    assert not result.passed
    assert not result.refused


def test_an_allowed_refusal_must_still_say_why() -> None:
    """`may_refuse` is not "anything goes". A refusal that says nothing is not
    honesty, it is silence, and it would hide a run that simply fell over."""
    result = Result(id=19, question="How many orders were placed in November 2026?")
    result.answered = False
    result.status = "completed"
    result.answer = ""

    check_case(_refusal_case(), {}, result, rows=[], artifacts={}, live=False)

    assert not result.passed
    assert "refused without saying why" in " ".join(result.failures)


def test_a_question_that_may_refuse_is_still_checked_when_it_answers() -> None:
    """The other half. In CI the scripted SQL answers, and answering wrongly is
    still a failure — `may_refuse` widens what is acceptable, it does not stop
    the case checking anything."""
    result = Result(id=19, question="How many orders were placed in November 2026?")
    result.answered = True
    result.citations = 1
    result.answer = "There were 7 orders in November 2026."

    check_case(
        _refusal_case(),
        {},
        result,
        rows=[],
        artifacts=_artifact(["order_count"], [[7]]),
        live=False,
    )

    assert not result.passed


def test_a_run_that_refuses_without_permission_still_fails() -> None:
    """`may_refuse` is per case, so nothing here loosens the other eighteen."""
    result = Result(id=1, question="How many orders were placed in July 2026?")
    result.answered = False
    result.answer = "I could not work that out."

    check_case({"expect": {"must_cite": 1}}, {}, result, rows=[], artifacts={}, live=False)

    assert not result.passed


@pytest.mark.parametrize("column", ["order_count", "ORDER_COUNT", " order_count "])
def test_the_expected_column_name_is_forgiving_of_spelling_not_of_meaning(column: str) -> None:
    assert match_value(_artifact(["order_count"], [[3718]]), column, 3718, 0).ok
