"""What may be drawn, and what is said when it may not (WP11.1).

The plan asks for a spec-validator corpus — valid, sneaky-url, unknown-mark,
oversized — and the owner added the three that matter to a reader: a wrong
column, a category with thousands of values, and dates that are not dates.

**The property under all of them is one sentence**: a chart is a spec or a
refusal, never neither. A picture that silently fails to appear is
indistinguishable from a broken page, which is what B-087 was built to stop in
the definition layer and what this repeats for charts.
"""

from __future__ import annotations

import pytest

from dataagent.agent.charts import (
    MARKS,
    MAX_CATEGORIES,
    MAX_POINTS,
    Chart,
    ChartRequest,
    Frame,
    decide,
)


def _frame(rows: list[tuple[object, ...]], columns: tuple[str, ...] = ("label", "amount")) -> Frame:
    return Frame(columns=columns, rows=tuple(rows))


def _bars(count: int) -> Frame:
    return _frame([(f"item {index}", float(index)) for index in range(count)])


# ---------------------------------------------------------------------------
# What may be drawn
# ---------------------------------------------------------------------------


def test_a_category_and_a_number_make_a_bar_chart() -> None:
    chart = decide(_bars(3), ChartRequest(mark="bar", x="label", y="amount"))

    assert chart.declined is None
    assert chart.spec is not None
    assert chart.spec["mark"] == "bar"
    encoding = chart.spec["encoding"]
    assert isinstance(encoding, dict)
    assert encoding["x"] == {"field": "label", "type": "nominal"}
    assert encoding["y"] == {"field": "amount", "type": "quantitative"}


def test_values_travel_inline_and_no_url_can_appear_in_a_spec() -> None:
    """**The sneaky-url case, closed by construction rather than by a check.**

    The spec is assembled here from a closed vocabulary and the frame's own
    column names, so there is no field a URL could arrive in — the model chooses
    a chart, it does not write the document the reader's browser renders. That
    matters because a spec *is* rendered in that browser: an address in one is a
    request the browser would make, with a customer's aggregates in hand.
    """
    chart = decide(_bars(2), ChartRequest(mark="bar", x="label", y="amount"))

    assert chart.spec is not None
    data = chart.spec["data"]
    assert isinstance(data, dict)
    assert set(data.keys()) == {"values"}  # pyright: ignore[reportUnknownArgumentType]
    assert "url" not in repr(chart.spec)
    assert "http" not in repr(chart.spec).replace(
        "https://vega.github.io/schema/vega-lite/v5.json", ""
    )


def test_a_date_column_gets_a_time_axis() -> None:
    frame = _frame([("2026-01-01", 1.0), ("2026-02-01", 2.0)], columns=("day", "amount"))

    chart = decide(frame, ChartRequest(mark="line", x="day", y="amount"))

    assert chart.spec is not None
    encoding = chart.spec["encoding"]
    assert isinstance(encoding, dict)
    assert encoding["x"] == {"field": "day", "type": "temporal"}


# ---------------------------------------------------------------------------
# What may not, and what the reader is told
# ---------------------------------------------------------------------------


def test_an_unknown_mark_is_refused_and_the_sentence_does_not_blame_the_data() -> None:
    """The request was wrong, not the result. A message that said "your data
    cannot be drawn" would send somebody to look at a table that is fine."""
    chart = decide(_bars(3), ChartRequest(mark="treemap", x="label", y="amount"))

    assert chart.code == "unknown_mark"
    assert chart.declined is not None
    assert "treemap" in chart.declined
    for mark in MARKS:
        assert mark in chart.declined


def test_a_column_the_result_does_not_have_is_named() -> None:
    """The owner's wrong-column case. The sentence names the column that is
    missing *and* the ones that are there, because the reader's next move is to
    pick one of them."""
    chart = decide(_bars(3), ChartRequest(mark="bar", x="region", y="amount"))

    assert chart.code == "no_such_column"
    assert chart.declined is not None
    assert "region" in chart.declined
    assert "label" in chart.declined


def test_a_category_with_thousands_of_values_says_how_many() -> None:
    """**The owner's 5,000-category case.** The number is the point: it is what
    tells a reader this is a property of their data rather than a fault, and it
    is the same argument B-092 made for a card's value list."""
    chart = decide(_bars(MAX_CATEGORIES + 1), ChartRequest(mark="bar", x="label", y="amount"))

    assert chart.code == "too_many_categories"
    assert chart.declined is not None
    assert str(MAX_CATEGORIES + 1) in chart.declined
    assert str(MAX_CATEGORIES) in chart.declined
    # And it points at the thing that does hold them all.
    assert "table" in chart.declined


def test_dates_that_are_not_dates_are_refused_rather_than_placed_arbitrarily() -> None:
    """**The owner's third case.** `Q1`/`Q2` on a time axis is not a missing
    chart, it is a *wrong* one: the renderer would place those at whatever
    instants it could parse them as, and the picture would look authoritative.
    Refusing is the only honest option, and the column is named."""
    frame = _frame([("Q1", 1.0), ("Q2", 2.0)], columns=("quarter", "amount"))

    chart = decide(frame, ChartRequest(mark="line", x="quarter", y="amount"))

    assert chart.code == "unordered_line"
    assert chart.declined is not None
    assert "quarter" in chart.declined
    # It says what would work, because the reader's question is "then what?"
    assert "bar chart" in chart.declined


def test_a_measure_that_holds_words_is_refused() -> None:
    frame = _frame([("north", "a lot"), ("south", "less")], columns=("region", "amount"))

    chart = decide(frame, ChartRequest(mark="bar", x="region", y="amount"))

    assert chart.code == "y_not_numeric"
    assert chart.declined is not None
    assert "amount" in chart.declined


def test_an_oversized_result_is_a_table_not_a_chart() -> None:
    """The plan's oversized case. The cap is about the spec as much as the
    picture: these values travel inline in JSON to the browser."""
    chart = decide(_bars(MAX_POINTS + 1), ChartRequest(mark="bar", x="label", y="amount"))

    assert chart.code == "too_many_points"
    assert chart.declined is not None
    assert f"{MAX_POINTS:,}" in chart.declined


def test_a_truncated_result_is_never_drawn_from_its_sample() -> None:
    """**B-051's rule, applied to pictures.** A chart has nowhere to put "this
    is the first N rows", so a result the store holds more of is declined rather
    than drawn from what happens to be in hand."""
    frame = Frame(columns=("label", "amount"), rows=(("a", 1.0), ("b", 2.0)), truncated=True)

    chart = decide(frame, ChartRequest(mark="bar", x="label", y="amount"))

    assert chart.code == "too_many_points"


def test_an_empty_result_says_so_rather_than_drawing_nothing() -> None:
    """An empty chart and a missing chart look identical on a page, and only one
    of them is honest about why."""
    chart = decide(_frame([]), ChartRequest(mark="bar", x="label", y="amount"))

    assert chart.code == "no_rows"
    assert chart.declined is not None
    assert "no rows" in chart.declined


# ---------------------------------------------------------------------------
# The property that holds under all of it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_",
    [
        ChartRequest(mark="bar", x="label", y="amount"),
        ChartRequest(mark="treemap", x="label", y="amount"),
        ChartRequest(mark="bar", x="nope", y="amount"),
        ChartRequest(mark="line", x="label", y="amount"),
        ChartRequest(mark="bar", x="label", y="label"),
    ],
)
def test_there_is_always_a_spec_or_a_reason(request_: ChartRequest) -> None:
    """**The one that makes the rest safe to rely on.** A caller that got
    neither would render nothing and say nothing, which is the failure the whole
    module exists to avoid — and the shape of it is a class invariant, so it
    holds for rules nobody has written yet."""
    chart = decide(_bars(3), request_)

    assert (chart.spec is None) != (chart.declined is None)
    if chart.declined is not None:
        assert chart.code
        assert chart.declined.startswith("No chart was drawn")


def test_the_invariant_is_enforced_by_the_type_itself() -> None:
    """A guard nobody can forget at a call site, because it is not at one."""
    with pytest.raises(ValueError, match="exactly one"):
        Chart()
    with pytest.raises(ValueError, match="exactly one"):
        Chart(spec={"mark": "bar"}, declined="No chart was drawn: …")
