"""The platform chooses the form; the model chose the result (WP13.20).

Every case here is judged from the values in the frame, never from a column's
name — `order_date` holding `Q1`/`Q2` is not a date, and a shape decision that
read the name would place those two at arbitrary points on a time axis.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from dataagent.agent.charts import Frame
from dataagent.agent.shape import shape_of


def _frame(columns: tuple[str, ...], *rows: tuple[object, ...], truncated: bool = False) -> Frame:
    return Frame(columns=columns, rows=rows, truncated=truncated)


# ---------------------------------------------------------------------------
# What gets drawn
# ---------------------------------------------------------------------------


def test_a_date_against_a_number_is_a_line() -> None:
    shape = shape_of(
        _frame(
            ("month", "revenue"),
            (date(2025, 1, 1), Decimal("100")),
            (date(2025, 2, 1), Decimal("120")),
        )
    )

    assert (shape.mark, shape.x, shape.y) == ("line", "month", "revenue")
    assert shape.series == ""


def test_a_category_against_a_number_is_a_bar() -> None:
    """**Bar, not line.** A line drawn between two unrelated categories implies a
    progression from one to the other, and there is none — the owner asked for
    bar to be preferred for categorical comparison and this is where that lives."""
    shape = shape_of(_frame(("outlet", "revenue"), ("A", Decimal("100")), ("B", Decimal("90"))))

    assert (shape.mark, shape.x, shape.y) == ("bar", "outlet", "revenue")


def test_two_entities_over_time_are_one_chart_with_two_series() -> None:
    """**The case this was built for.** *Outlet A and outlet B monthly* is one
    chart with a line each, not two answers. `ChartAsk.series` has existed since
    WP11.1 and was filled by nothing."""
    shape = shape_of(
        _frame(
            ("month", "outlet", "revenue"),
            (date(2025, 1, 1), "A", Decimal("100")),
            (date(2025, 1, 1), "B", Decimal("80")),
            (date(2025, 2, 1), "A", Decimal("120")),
            (date(2025, 2, 1), "B", Decimal("95")),
        )
    )

    assert shape.mark == "line"
    assert shape.x == "month"
    assert shape.series == "outlet"
    assert "split by outlet" in shape.reason


def test_a_date_beats_a_category_for_the_axis() -> None:
    """A result carrying both is a series over time, not a comparison between
    categories — drawing it as grouped bars throws the time axis away."""
    shape = shape_of(
        _frame(
            ("outlet", "month", "revenue"),
            ("A", date(2025, 1, 1), Decimal("100")),
            ("B", date(2025, 2, 1), Decimal("90")),
        )
    )

    assert shape.x == "month"
    assert shape.series == "outlet"


def test_dates_that_are_not_dates_are_a_category() -> None:
    """`_kind` judges the values. `Q1`/`Q2` in a column called `period` is
    nominal, so the axis is categorical and the mark is a bar."""
    shape = shape_of(_frame(("period", "revenue"), ("Q1", Decimal("100")), ("Q2", Decimal("90"))))

    assert shape.mark == "bar"


# ---------------------------------------------------------------------------
# What does not get drawn, and why
# ---------------------------------------------------------------------------


def test_a_single_figure_is_a_sentence() -> None:
    shape = shape_of(_frame(("total",), (Decimal("42"),)))

    assert not shape.draws
    assert "sentence" in shape.reason


def test_a_result_with_no_numbers_is_not_a_picture() -> None:
    shape = shape_of(_frame(("outlet", "manager"), ("A", "Ada"), ("B", "Grace")))

    assert not shape.draws
    assert "number" in shape.reason


def test_numbers_with_nothing_to_plot_them_against_are_not_a_picture() -> None:
    shape = shape_of(_frame(("revenue", "cost"), (Decimal("1"), Decimal("2"))))

    assert not shape.draws
    assert shape.reason


def test_a_truncated_result_is_refused_with_the_reason_being_about_the_data() -> None:
    """B-051 for pictures: a chart of a capped result looks complete while being
    partial, and a chart has nowhere to put that caveat."""
    shape = shape_of(_frame(("month", "revenue"), (date(2025, 1, 1), Decimal("1")), truncated=True))

    assert not shape.draws
    assert "row limit" in shape.reason


def test_an_empty_result_says_so_rather_than_proposing_a_mark() -> None:
    shape = shape_of(_frame(("month", "revenue")))

    assert not shape.draws
    assert "no rows" in shape.reason


def test_every_refusal_carries_a_reason() -> None:
    """B-087's discipline applied to form: a shape that declines must say what
    about the result made a picture the wrong answer, or the answer card shows a
    blank where a chart was expected and nobody can tell why."""
    refusals = [
        shape_of(_frame(("total",), (Decimal("42"),))),
        shape_of(_frame(("a", "b"), ("x", "y"))),
        shape_of(_frame(("month", "revenue"))),
        shape_of(_frame(("m", "v"), (date(2025, 1, 1), Decimal("1")), truncated=True)),
    ]

    assert all(not shape.draws and shape.reason for shape in refusals)


def test_a_series_too_wide_to_read_is_left_off_rather_than_drawn() -> None:
    """A legend of forty entries is not a legend. The chart is still drawn — the
    split is what is dropped, not the picture."""
    rows = tuple((date(2025, 1, 1), f"outlet-{index}", Decimal("1")) for index in range(40))
    shape = shape_of(_frame(("month", "outlet", "revenue"), *rows))

    assert shape.mark == "line"
    assert shape.series == ""
