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

import json
from collections.abc import Sequence
from decimal import Decimal
from typing import cast

import pytest

from dataagent.agent.charts import (
    MARKS,
    MAX_CATEGORIES,
    MAX_POINTS,
    MAX_SERIES,
    Chart,
    ChartRequest,
    Frame,
    axis_title,
    decide,
)
from dataagent.dal.artifacts import encode
from dataagent.dal.masking import MaskedFrame


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
    assert encoding["x"] == {"field": "label", "type": "nominal", "title": "Label"}
    assert encoding["y"] == {"field": "amount", "type": "quantitative", "title": "Amount"}


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
    # Banded by month, because both values are the first of one (**B-105**).
    assert encoding["x"] == {
        "field": "day",
        "type": "temporal",
        "title": "Day",
        "timeUnit": "yearmonth",
    }


# ---------------------------------------------------------------------------
# How coarse the time axis is (B-105)
# ---------------------------------------------------------------------------


def _x(chart: Chart) -> dict[str, object]:
    """The x encoding, narrowed. `spec` is `dict[str, object]` by design — it is
    a document, not a model — so a test that reads into it has to say so."""
    assert chart.spec is not None
    raw = chart.spec["encoding"]
    assert isinstance(raw, dict)
    x = cast("dict[str, object]", raw)["x"]
    assert isinstance(x, dict)
    return cast("dict[str, object]", x)


def test_monthly_bars_are_banded_by_month_not_scattered_on_a_calendar() -> None:
    """**The frame from the gate walk, verbatim** (**B-105**).

    Four monthly bars on a bare temporal axis are four instants on a continuous
    four-month domain: Vega ticks it by week, the bars are drawn at their default
    width, and the space between them reads as zero revenue. Wrong rather than
    plain, which is the failure `decide` refuses for Q1/Q2 and then drew here
    with real dates.
    """
    frame = _frame(
        [
            ("2026-04-01", 135950.59),
            ("2026-05-01", 145341.12),
            ("2026-06-01", 123650.61),
            ("2026-07-01", 122712.33),
        ],
        columns=("month", "revenue"),
    )

    chart = decide(frame, ChartRequest(mark="bar", x="month", y="revenue"))

    assert _x(chart)["timeUnit"] == "yearmonth"


def test_the_line_chart_had_the_same_defect_and_it_only_looked_fine() -> None:
    """The encoding that shipped for the trend question was identical to the bar
    one — a bare `temporal` x. It read as correct only because a line implies no
    width, so nothing in the picture claimed the space between two points."""
    frame = _frame([("2026-04-01", 1.0), ("2026-05-01", 2.0)], columns=("month", "revenue"))

    chart = decide(frame, ChartRequest(mark="line", x="month", y="revenue"))

    assert _x(chart)["timeUnit"] == "yearmonth"


def test_daily_data_is_not_rounded_up_to_months() -> None:
    """The grain is the coarsest unit every value **sits exactly on**, so a
    column with real days keeps them. Coarsening here would put three days in
    one band and silently sum them into a bar nobody asked for."""
    frame = _frame(
        [("2026-04-01", 1.0), ("2026-04-02", 2.0), ("2026-04-03", 3.0)],
        columns=("day", "amount"),
    )

    chart = decide(frame, ChartRequest(mark="bar", x="day", y="amount"))

    assert _x(chart)["timeUnit"] == "yearmonthdate"


def test_january_firsts_are_years() -> None:
    frame = _frame([("2024-01-01", 1.0), ("2025-01-01", 2.0)], columns=("year", "amount"))

    chart = decide(frame, ChartRequest(mark="bar", x="year", y="amount"))

    assert _x(chart)["timeUnit"] == "year"


def test_a_bar_on_timestamps_gets_a_discrete_axis_rather_than_instants() -> None:
    """No unit to band by, and a bar at an instant is the same defect in its
    general form. Ordinal is not a compromise for a bar chart — one band per
    thing compared is what a bar chart's axis is."""
    frame = _frame(
        [("2026-04-01T09:30:00", 1.0), ("2026-04-01T10:15:00", 2.0)],
        columns=("at", "amount"),
    )

    chart = decide(frame, ChartRequest(mark="bar", x="at", y="amount"))

    x = _x(chart)
    assert x["type"] == "ordinal"
    assert "timeUnit" not in x


def test_a_line_on_timestamps_stays_continuous() -> None:
    """A line over a working day is exactly the case a continuous time axis is
    for, and spacing the points evenly would misstate when they happened."""
    frame = _frame(
        [("2026-04-01T09:30:00", 1.0), ("2026-04-01T10:15:00", 2.0)],
        columns=("at", "amount"),
    )

    chart = decide(frame, ChartRequest(mark="line", x="at", y="amount"))

    x = _x(chart)
    assert x["type"] == "temporal"
    assert "timeUnit" not in x


def test_dates_and_strings_agree_about_the_grain() -> None:
    """Both representations reach `decide` — a driver that returned `date`
    objects, and the ISO strings a stored artifact carries. If they disagreed,
    the same result would chart differently depending on which side it came
    from, which is the seam B-103 was found at."""
    from datetime import date as _date

    as_objects = _frame([(_date(2026, 4, 1), 1.0), (_date(2026, 5, 1), 2.0)], columns=("m", "v"))
    as_text = _frame([("2026-04-01", 1.0), ("2026-05-01", 2.0)], columns=("m", "v"))

    request = ChartRequest(mark="bar", x="m", y="v")

    assert _x(decide(as_objects, request)) == _x(decide(as_text, request))


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


# ---------------------------------------------------------------------------
# What the axes are called (**B-098**)
# ---------------------------------------------------------------------------


def test_an_axis_is_captioned_in_the_readers_words_not_the_databases() -> None:
    """The picture used to be labelled `order_month` and `order_count` — the
    database's vocabulary, shown to somebody who never chose it."""
    frame = _frame([("2026-01", 3624)], columns=("order_month", "order_count"))

    chart = decide(frame, ChartRequest(mark="line", x="order_month", y="order_count"))

    assert chart.spec is not None
    encoding = chart.spec["encoding"]
    assert isinstance(encoding, dict)
    assert encoding["x"]["title"] == "Order month"  # pyright: ignore[reportIndexIssue]
    assert encoding["y"]["title"] == "Order count"  # pyright: ignore[reportIndexIssue]


def test_the_series_gets_a_title_too() -> None:
    frame = _frame(
        [("2026-01", 10.0, "north"), ("2026-01", 12.0, "south")],
        columns=("order_month", "amount", "store_region"),
    )

    chart = decide(
        frame, ChartRequest(mark="line", x="order_month", y="amount", series="store_region")
    )

    assert chart.spec is not None
    encoding = chart.spec["encoding"]
    assert isinstance(encoding, dict)
    assert encoding["color"]["title"] == "Store region"  # pyright: ignore[reportIndexIssue]


def test_an_abbreviation_is_not_lowercased_into_nonsense() -> None:
    assert axis_title("customer_id") == "Customer ID"
    assert axis_title("total_usd") == "Total USD"


def test_a_name_that_is_already_a_caption_is_left_alone() -> None:
    """A column called `Total Revenue` needs nothing from us, and a de-snake-caser
    that "fixed" it would be making the caption worse."""
    assert axis_title("Total Revenue") == "Total Revenue"
    assert axis_title("") == ""


# ---------------------------------------------------------------------------
# Through the seam, not around it (**B-103**)
# ---------------------------------------------------------------------------


def _round_tripped(rows: list[tuple[object, ...]], columns: tuple[str, ...]) -> Frame:
    """A frame the way the chart tool really gets one: encoded, then read back.

    **This helper is the point of the section.** Seventeen tests above build a
    `Frame` by hand out of Python floats, and every one of them passed while
    charting money was impossible — because the defect never lived in `decide`,
    it lived in the round trip that `decide` is downstream of. A hand-built frame
    cannot catch this class, so this one goes through `encode` and `json.loads`
    exactly as `tools/chart.py::_frame_for` does.
    """
    payload = json.loads(
        encode(
            MaskedFrame(
                columns=columns,
                rows=tuple(rows),
                truncated=False,
                duration_ms=1,
                masked_columns=(),
            )
        )
    )
    stored = tuple(tuple(row) for row in payload["rows"])
    types = tuple(payload.get("column_types", ()))
    rebuilt = tuple(
        tuple(
            Decimal(value) if types[index] == "number" and isinstance(value, str) else value
            for index, value in enumerate(row)
        )
        for row in stored
    )
    return Frame(columns=columns, rows=rebuilt, column_types=types)


def test_money_survives_the_round_trip_and_draws(  # B-103
) -> None:
    """**The gate's own question.** Revenue is a `Decimal`, which cannot cross
    JSON as a number, so it travels as a string and is rebuilt from the type the
    writer recorded. Before this, the chart refused: *"'revenue' does not hold
    numbers"* — on a correct answer, about a column that is nothing but."""
    frame = _round_tripped(
        [("2026-05", Decimal("145341.12")), ("2026-06", Decimal("123650.61"))],
        columns=("month", "revenue"),
    )

    chart = decide(frame, ChartRequest(mark="line", x="month", y="revenue"))

    assert chart.declined is None, chart.declined
    assert chart.spec is not None


def test_the_digits_are_not_rounded_on_the_way(  # B-103
) -> None:
    """A float would round this, which is why the value travels as text. The
    point of the declared type is that it comes back exact."""
    frame = _round_tripped(
        [("a", Decimal("12345678901234.567890")), ("b", Decimal("0.000000000001"))],
        columns=("label", "amount"),
    )

    assert frame.rows[0][1] == Decimal("12345678901234.567890")
    assert frame.rows[1][1] == Decimal("0.000000000001")


def test_a_column_of_digits_that_is_text_is_still_text() -> None:
    """**The guess this fix refused to make.** An account number survives the
    round trip as text because the writer *said* text — and a chart layer that
    decided digits meant a number would make it a measure."""
    frame = _round_tripped([("north", "90210"), ("south", "10118")], columns=("region", "postcode"))

    chart = decide(frame, ChartRequest(mark="bar", x="region", y="postcode"))

    assert chart.code == "y_not_numeric"


def test_an_integer_still_charts_because_it_never_needed_rescuing() -> None:
    """The shape WP11.1 demoed, and the reason this went unnoticed: `int` crosses
    JSON untouched, so the one live proof in #82 was a `count(*)`."""
    frame = _round_tripped(
        [("2026-01", 3624), ("2026-02", 3311)], columns=("order_month", "order_count")
    )

    chart = decide(frame, ChartRequest(mark="line", x="order_month", y="order_count"))

    assert chart.declined is None


def test_a_result_stored_before_types_were_recorded_says_so() -> None:
    """**B-087's rule, applied to a storage format.** An old artifact carries no
    `column_types`, so the platform genuinely cannot tell money from a postcode.
    Saying the column "does not hold numbers" would blame the customer's data for
    something we lost."""
    old = Frame(
        columns=("month", "revenue"),
        rows=(("2026-05", "145341.12"), ("2026-06", "123650.61")),
        column_types=(),
    )

    chart = decide(old, ChartRequest(mark="line", x="month", y="revenue"))

    assert chart.code == "types_not_recorded"
    assert chart.declined is not None
    assert "stored before" in chart.declined
    assert "Ask the question again" in chart.declined


def test_an_old_result_that_really_is_text_is_not_excused() -> None:
    """The other half of the pair. Without this, every pre-B-103 result would
    claim the platform's fault, including the ones that genuinely hold words."""
    old = Frame(
        columns=("region", "name"),
        rows=(("north", "Alice"), ("south", "Bob")),
        column_types=(),
    )

    chart = decide(old, ChartRequest(mark="bar", x="region", y="name"))

    assert chart.code == "y_not_numeric"


# ---------------------------------------------------------------------------
# Colour is a claim, not decoration (B-109)
# ---------------------------------------------------------------------------


def _split(rows: Sequence[tuple[object, ...]]) -> Frame:
    """A frame with a real split in it. `Sequence`, because `list` is invariant
    and a `list[tuple[str, str, float]]` is not a `list[tuple[object, ...]]`."""
    return Frame(columns=("month", "channel", "revenue"), rows=tuple(rows))


def test_a_split_the_axes_do_not_show_is_coloured() -> None:
    """What colour is *for*: the same figures broken down by another column."""
    frame = _split(
        [
            ("2026-04-01", "delivery", 100.0),
            ("2026-04-01", "pickup", 60.0),
            ("2026-05-01", "delivery", 120.0),
            ("2026-05-01", "pickup", 70.0),
        ]
    )

    chart = decide(frame, ChartRequest(mark="bar", x="month", y="revenue", series="channel"))

    assert chart.spec is not None
    encoding = chart.spec["encoding"]
    assert isinstance(encoding, dict)
    colour = cast("dict[str, object]", encoding)["color"]
    assert isinstance(colour, dict)
    assert cast("dict[str, object]", colour)["field"] == "channel"


def test_colouring_by_the_column_already_on_the_axis_is_refused() -> None:
    """**The request that started B-109.** The model asked for a separate colour
    per month on a chart whose horizontal axis is already the month — eighteen
    hues, a legend restating the tick labels, and not one fact added. design.md's
    second rule settles it: *a hue is a claim that this thing has a state or a
    category. Never colour for interest.*"""
    frame = _frame([("2026-04-01", 1.0), ("2026-05-01", 2.0)], columns=("month", "revenue"))

    chart = decide(frame, ChartRequest(mark="bar", x="month", y="revenue", series="month"))

    assert chart.spec is None
    assert chart.code == "colour_would_repeat_an_axis"
    assert "already on the horizontal axis" in (chart.declined or "")


def test_colouring_by_the_measure_is_refused_too() -> None:
    frame = _frame([("a", 1.0), ("b", 2.0)], columns=("label", "amount"))

    chart = decide(frame, ChartRequest(mark="bar", x="label", y="amount", series="amount"))

    assert chart.code == "colour_would_repeat_an_axis"
    assert "vertical axis" in (chart.declined or "")


def test_a_refusal_is_said_rather_than_the_colour_quietly_dropped() -> None:
    """**B-060 in a picture.** A chart that came back mono when colour was asked
    for, with nothing said, would leave the run knowing a choice had been made
    and the reader not. `decide` returns a spec or a sentence, never a spec with
    the request silently trimmed out of it."""
    frame = _frame([("a", 1.0), ("b", 2.0)], columns=("label", "amount"))

    chart = decide(frame, ChartRequest(mark="bar", x="label", y="amount", series="label"))

    assert chart.spec is None
    assert chart.declined
    assert chart.code


def test_more_series_than_the_palette_has_hues_is_refused() -> None:
    """A ninth series would repeat slot 1, and two series wearing one colour is a
    chart that lies. The cap is the palette's size, not a readability guess."""
    rows = [("2026-04-01", f"channel {index}", float(index)) for index in range(MAX_SERIES + 1)]

    chart = decide(_split(rows), ChartRequest(mark="bar", x="month", y="revenue", series="channel"))

    assert chart.code == "too_many_series"
    assert str(MAX_SERIES) in (chart.declined or "")
    # The number that makes a refusal actionable, as every other one here does.
    assert f"{MAX_SERIES + 1:,}" in (chart.declined or "")


def test_exactly_as_many_series_as_the_palette_has_is_drawn() -> None:
    rows = [("2026-04-01", f"channel {index}", float(index)) for index in range(MAX_SERIES)]

    chart = decide(_split(rows), ChartRequest(mark="bar", x="month", y="revenue", series="channel"))

    assert chart.spec is not None


def test_the_series_cap_is_far_below_the_category_cap() -> None:
    """They are different questions. Fifty bars along an axis is a busy chart;
    fifty colours is not a palette, and the axis is not what runs out."""
    assert MAX_SERIES < MAX_CATEGORIES
