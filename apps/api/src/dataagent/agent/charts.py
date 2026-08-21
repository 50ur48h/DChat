"""Whether a result can be drawn, and the sentence for when it cannot (WP11.1).

Architecture Part 3 gives the shape: the agent emits a **validated Vega-Lite
spec**, the browser renders it, and no code runs server-side. This module is the
half that decides — given a masked result and the chart somebody asked for,
either a spec this platform will render, or a reason it will not.

**There is always one or the other, and never neither.** A chart that silently
does not appear looks like breakage, and the reader cannot tell a tool that
declined from a tool that crashed from a question nobody asked. That is B-087's
lesson — *say the thing that did not happen* — applied to pictures, and
`decide()` returns a `Chart` whose `spec` and `declined` are exactly one apiece.

**Two kinds of refusal, told apart because they blame different things.**

*The data cannot support it.* A category with more distinct values than a chart
has room for, a temporal axis on a column that holds no dates, a frame with
nothing numeric in it. These are ordinary and expected, and the sentence says
which column and how many, because a number is what makes a refusal actionable —
the same argument B-092 made for a card's value list.

*The spec is refused.* A mark outside the whitelist, an encoding channel nobody
declared, a URL. Here the data is fine and the *request* was not, so the sentence
does not blame the data. `data.url` is the one that matters: a spec is rendered
in the reader's browser, so a spec that could name an external address is a spec
that could exfiltrate a customer's aggregates to anybody who could talk the model
into writing one. Values travel inline or not at all.

**A chart is never drawn from a sample.** The artifact store holds the whole
masked result; a result larger than `MAX_POINTS` is declined rather than
truncated into a picture that looks complete. B-051 settled the general form of
this: a figure that can only come from part of the data is stated as such or not
at all, and a chart has nowhere to put that caveat.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

__all__ = [
    "MARKS",
    "MAX_CATEGORIES",
    "MAX_POINTS",
    "Chart",
    "ChartRequest",
    "Frame",
    "axis_title",
    "decide",
]

#: The marks a reader can interpret without a legend explaining the mark itself.
#: Closed, like the critic's filter operators and for the same reason: a mark the
#: renderer has not been proved against is one the product would claim to draw.
MARKS: tuple[str, ...] = ("bar", "line", "point", "area")

#: How many categories a bar chart can show before it stops being readable. Past
#: this the honest answer is the table, which has all of them.
MAX_CATEGORIES = 50

#: How many *coloured* series a chart may carry, which is a different and much
#: smaller number (**B-109**): it is the size of the categorical palette in
#: `globals.css`, and a ninth series would have to repeat a hue. Two series
#: wearing one colour is a chart that lies, and generating a ninth hue is how a
#: palette stops being a palette. Past this the honest answers are a filter, a
#: facet or the table — and the refusal says which.
MAX_SERIES = 8

#: How many points may travel inline in a spec. The spec is JSON in an HTTP
#: response and then in a browser's memory; a result larger than this is a table.
MAX_POINTS = 1_000

#: What a temporal axis will accept when the catalog has not already typed the
#: column as a date. Deliberately narrow: `2026-08-19` and `2026-08` are dates,
#: `August` and `Q3` are labels that a time axis would place at arbitrary points.
_ISO_DATE = re.compile(r"^\d{4}-\d{2}(-\d{2})?([T ]\d{2}:\d{2}(:\d{2})?)?")


@dataclass(frozen=True, slots=True)
class Frame:
    """A masked result, as the chart tool sees it."""

    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    #: Whether the store holds more than these rows. A truncated result cannot
    #: be charted honestly — see the module docstring.
    truncated: bool = False
    #: What the writer recorded each column as holding (**B-103**), or empty for
    #: a result stored before that was written down. Empty is not "text": it is
    #: *unknown*, and the difference decides whether a refusal blames the data or
    #: admits the platform lost the information — which is B-087's rule.
    column_types: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChartRequest:
    """The chart somebody asked for, in the terms a reader would use."""

    mark: str
    x: str
    y: str
    series: str | None = None
    title: str | None = None


@dataclass(frozen=True, slots=True)
class Chart:
    """Either a spec to render or a reason there is none. Never both, never neither."""

    spec: dict[str, object] | None = None
    declined: str | None = None
    #: Why, in a word, for the trace and for a test that wants to be specific
    #: about which rule fired rather than matching on prose.
    code: str | None = None

    def __post_init__(self) -> None:
        if (self.spec is None) == (self.declined is None):
            raise ValueError("a chart is a spec or a refusal, and exactly one of them")


def _decline(code: str, sentence: str) -> Chart:
    return Chart(declined=sentence, code=code)


def _is_number(value: object) -> bool:
    """**Decimal counts** (**B-103**). A money column arrives as one, because a
    `Decimal` cannot cross JSON as a number without rounding and is rebuilt from
    the type the writer declared. What is deliberately *not* here is a string
    that looks numeric: recognising one would draw a chart of an account number,
    and a wrong chart is worse than an absent one."""
    return isinstance(value, int | float | Decimal) and not isinstance(value, bool)


def _is_temporal(value: object) -> bool:
    if isinstance(value, date | datetime):
        return True
    return isinstance(value, str) and bool(_ISO_DATE.match(value))


#: The same shape `_ISO_DATE` admits, taken apart so the grain can be read off
#: it. Kept separate from that pattern rather than replacing it: one answers
#: "is this a date at all", which guards the axis type, and this one answers
#: "how coarse is it", which only decorates an axis already known to be temporal.
_ISO_PARTS = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})(?:-(?P<day>\d{2}))?"
    r"(?:[T ](?P<hour>\d{2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?)?"
)


@dataclass(frozen=True, slots=True)
class _Instant:
    """A temporal value reduced to the fields that decide its grain."""

    month: int
    day: int | None
    #: Whether anything below a day is set to something other than midnight.
    within_day: bool


def _instant(value: object) -> _Instant | None:
    """One temporal value taken apart, or None if it is not one.

    Both representations reach here — a driver that returned `date`/`datetime`
    objects, and the ISO strings a stored artifact carries — and they have to
    agree, or the same result would chart differently depending on whether it
    came from the database or from the store.
    """
    if isinstance(value, datetime):
        return _Instant(
            month=value.month,
            day=value.day,
            within_day=bool(value.hour or value.minute or value.second or value.microsecond),
        )
    if isinstance(value, date):
        return _Instant(month=value.month, day=value.day, within_day=False)
    if not isinstance(value, str):
        return None
    found = _ISO_PARTS.match(value)
    if found is None:
        return None
    clock = (found.group("hour"), found.group("minute"), found.group("second"))
    day = found.group("day")
    return _Instant(
        month=int(found.group("month")),
        day=int(day) if day is not None else None,
        within_day=any(part is not None and int(part) != 0 for part in clock),
    )


def _time_grain(values: Sequence[object]) -> str | None:
    """The coarsest Vega-Lite `timeUnit` every value sits exactly on, or None.

    **A monthly aggregate on a continuous day axis is a wrong chart, not a plain
    one.** Four monthly bars drawn without this landed at four instants on a
    four-month domain: thin spikes on a calendar ticked by week, with the gaps
    between them reading as zero revenue. The Q1/Q2 rule with real dates —
    `decide` refuses the values that are not dates and then drew this.

    **Read off the values, never off the column's name.** That is the line this
    module holds everywhere else: `_kind` judges `order_date` by what is in it,
    `axis_title` de-snake-cases rather than translating, and `_is_number` will
    not parse a string that looks numeric. A name like `month` proves nothing;
    every value falling on the first of one is a fact about the data.

    It is deliberately conservative in one direction only. Daily data that
    happens to contain nothing but firsts-of-month is banded by month, which
    places every point correctly and merely draws it coarser than it had to be.
    Nothing here can move a point to a time it is not.
    """
    instants = [_instant(value) for value in _present(values)]
    if not instants or any(instant is None for instant in instants):
        return None
    known = [instant for instant in instants if instant is not None]
    if any(instant.within_day for instant in known):
        # Something below a day is set, so a day is not the unit — leave the
        # axis continuous rather than collapsing points onto the same band.
        return None
    if any(instant.day not in (None, 1) for instant in known):
        return "yearmonthdate"
    if all(instant.month == 1 for instant in known):
        return "year"
    return "yearmonth"


def _stale_numeric(frame: Frame, name: str) -> bool:
    """Whether this column is unjudgeable rather than non-numeric (**B-103**).

    True only for a result stored before `column_types` existed, whose values
    are all strings that would parse as numbers. Both halves are needed: without
    the first this would fire on a genuine text column in a *new* result, where
    the writer said "text" and meant it; without the second it would fire on
    every old result, including the ones that really do hold words.

    This is not the guess the fix refused to make. Nothing is charted on the
    strength of it — it only chooses which true sentence to show, and the one it
    chooses admits the platform lost the information instead of blaming the data.
    """
    if frame.column_types:
        return False
    values = _present(_column_values(frame, name))
    if not values:
        return False
    return all(isinstance(value, str) and _parses_as_number(value) for value in values)


def _parses_as_number(value: str) -> bool:
    try:
        Decimal(value)
    except InvalidOperation:
        return False
    return True


def _column_values(frame: Frame, name: str) -> tuple[object, ...]:
    index = frame.columns.index(name)
    return tuple(row[index] for row in frame.rows if index < len(row))


def _present(values: Sequence[object]) -> tuple[object, ...]:
    return tuple(value for value in values if value is not None)


def _kind(values: Sequence[object]) -> Literal["quantitative", "temporal", "nominal"]:
    """What a column is, judged from the values rather than from its name.

    Values, because a column called `order_date` holding `Q1`/`Q2` is not a date
    and a time axis would place those two at arbitrary points — the failure the
    owner named as *dates that aren't dates*. Judged on the values that are
    actually present: a column that is half null is still a date column.
    """
    present = _present(values)
    if not present:
        return "nominal"
    if all(_is_number(value) for value in present):
        return "quantitative"
    if all(_is_temporal(value) for value in present):
        return "temporal"
    return "nominal"


def decide(frame: Frame, request: ChartRequest) -> Chart:
    """A spec this platform will render, or the sentence saying why not.

    Order matters only in that the message should name the first thing a person
    would fix. Everything here is checked against the frame that actually came
    back, never against what the model believed it had asked for.
    """
    if request.mark not in MARKS:
        return _decline(
            "unknown_mark",
            f"No chart was drawn: {request.mark!r} is not a chart type this product renders. "
            f"It draws {', '.join(MARKS)}.",
        )

    named = [name for name in (request.x, request.y, request.series) if name]
    missing = [name for name in named if name not in frame.columns]
    if missing:
        return _decline(
            "no_such_column",
            f"No chart was drawn: the result has no column called {missing[0]!r}. "
            f"It has {', '.join(frame.columns)}.",
        )

    if not frame.rows:
        return _decline("no_rows", "No chart was drawn: the query returned no rows to plot.")

    if frame.truncated or len(frame.rows) > MAX_POINTS:
        return _decline(
            "too_many_points",
            f"No chart was drawn: the result has {len(frame.rows):,} rows or more, and a chart "
            f"here shows at most {MAX_POINTS:,}. The table has all of them.",
        )

    y_values = _column_values(frame, request.y)
    if _kind(y_values) != "quantitative":
        if _stale_numeric(frame, request.y):
            # **B-103, and B-087's rule applied to a storage format.** This
            # result was written before column types were recorded, so the
            # platform genuinely cannot tell money from a postcode here. Saying
            # the column "does not hold numbers" would be a claim about the
            # customer's data when the fact is about us.
            return _decline(
                "types_not_recorded",
                "No chart was drawn: this result was stored before the platform "
                "recorded what each column holds, so a number cannot be told "
                "from text that looks like one. Ask the question again and the "
                "new result will chart.",
            )
        return _decline(
            "y_not_numeric",
            f"No chart was drawn: {request.y!r} does not hold numbers, so there is nothing "
            "to measure up the vertical axis.",
        )

    x_values = _column_values(frame, request.x)
    x_kind = _kind(x_values)

    if x_kind == "nominal":
        distinct = len({str(value) for value in x_values})
        if distinct > MAX_CATEGORIES:
            return _decline(
                "too_many_categories",
                f"No chart was drawn: {request.x!r} has {distinct:,} distinct values and a chart "
                f"here shows at most {MAX_CATEGORIES}. The table has all of them.",
            )
        if request.mark in ("line", "area"):
            # A line joins points in order, and nominal values have none — the
            # picture would imply a progression the data does not have.
            return _decline(
                "unordered_line",
                f"No chart was drawn: a {request.mark} chart joins points in order, and "
                f"{request.x!r} holds labels rather than dates or numbers. A bar chart shows "
                "these without implying a sequence.",
            )

    if request.series is not None:
        # **Colour is a claim, not decoration** (**B-109**, and docs/design.md's
        # second rule in as many words: *a hue is a claim that this thing has a
        # state or a category. Never colour for interest*). Splitting a series by
        # the same column that is already on an axis paints the axis a second
        # time: eighteen months in eighteen hues, a legend restating the tick
        # labels, and not one fact added.
        #
        # **Refused rather than quietly dropped.** A chart that came back mono
        # when colour was asked for, with nothing said, is B-060 in a picture —
        # the run knew a choice had been made and the reader did not.
        if request.series in (request.x, request.y):
            axis = "horizontal" if request.series == request.x else "vertical"
            return _decline(
                "colour_would_repeat_an_axis",
                f"No chart was drawn: {request.series!r} is already on the {axis} axis, so "
                "colouring by it would say the same thing twice. Colour is kept for a split "
                "the axes do not show — the same figures broken down by another column.",
            )
        series_distinct = len({str(value) for value in _column_values(frame, request.series)})
        if series_distinct > MAX_SERIES:
            return _decline(
                "too_many_series",
                f"No chart was drawn: {request.series!r} has {series_distinct:,} distinct values, "
                f"and a chart here colours at most {MAX_SERIES} at once — past that a ninth "
                "would repeat a colour and two series would look like one. The table has all "
                "of them.",
            )

    return Chart(spec=_spec(frame, request, x_kind))


#: Words a de-snake-cased column name should not start a sentence with in
#: lowercase, and abbreviations that look wrong capitalised. Small and blunt on
#: purpose: the catalog's own description is the good answer, and this is only
#: the fallback for a column nobody has described.
_ALWAYS_UPPER = {"id", "sku", "vat", "gst", "usd", "eur", "gbp", "myr", "kpi"}


def axis_title(column: str) -> str:
    """A column name in the reader's vocabulary rather than the database's (**B-098**).

    `order_month` becomes *Order month*. Deliberately a de-snake-casing and not
    a dictionary: the picture is captioned for a person, and a caption that is
    merely *tidier* than the raw name is already the whole of the improvement.
    Anything cleverer — expanding `qty`, guessing that `dt` means date — would be
    the platform inventing meaning it does not have, which is the failure this
    codebase keeps filing bugs about.

    An empty or already-spaced name is returned unchanged rather than mangled: a
    column called `Total Revenue` is a caption already.
    """
    if not column or " " in column:
        return column
    words = [word for word in column.replace("-", "_").split("_") if word]
    if not words:
        return column
    rendered = [word.upper() if word.lower() in _ALWAYS_UPPER else word.lower() for word in words]
    first = rendered[0]
    if first.lower() not in _ALWAYS_UPPER:
        first = first[:1].upper() + first[1:]
    return " ".join([first, *rendered[1:]])


def _plottable(value: object) -> object:
    """One cell, as the spec may carry it (**B-103**).

    A `Decimal` becomes a `float` **here and only here**. The spec is stored as
    JSONB and then rendered by Vega in a browser, whose only number type is a
    double — so precision beyond a double cannot be displayed however carefully
    it is carried, and carrying it anyway costs a `TypeError` at the point of
    storage rather than buying anything a reader could see.

    This is not a retreat from the exactness the rest of the fix is about. The
    **artifact** keeps the Decimal, so anything that computes on the result gets
    every digit; only the picture is approximate, and a picture always was.
    """
    if isinstance(value, Decimal):
        return float(value)
    return value


def _spec(
    frame: Frame,
    request: ChartRequest,
    x_kind: str,
) -> dict[str, object]:
    """The Vega-Lite spec, built here rather than accepted from the model.

    **The model chooses a chart; it does not write the document the browser
    renders.** Everything below is assembled from a closed vocabulary and the
    column names the frame actually has, so there is no field for a URL to arrive
    in and no channel that was not declared here. Validating a spec somebody else
    wrote would mean maintaining a denylist against a format that keeps growing;
    this way the only inputs are a mark from `MARKS`, names checked against
    `frame.columns`, and values already masked by the DAL.
    """
    # **B-098.** The axes used to carry the raw column names, so the picture was
    # captioned in the database's vocabulary — `order_month`, `order_count` — to
    # a reader who never chose those words. The title is one field on the
    # encoding, and it is built server-side like everything else here.
    x: dict[str, object] = {
        "field": request.x,
        "type": x_kind,
        "title": axis_title(request.x),
    }
    if x_kind == "temporal":
        # **B-105.** A temporal axis with no unit is a continuous one, and Vega
        # then ticks it by whatever suits the *span* rather than the data: four
        # monthly bars over four months got weekly gridlines, four thin spikes,
        # and gaps a reader is entitled to read as zero. The unit is read off the
        # values, so it says what the data is rather than what a column is called.
        grain = _time_grain(_column_values(frame, request.x))
        if grain is not None:
            x["timeUnit"] = grain
        elif request.mark == "bar":
            # Dates with a time on them, and a bar for each. There is no unit to
            # band by, and a bar at an instant on a continuous axis is the defect
            # above in its general form — so the axis becomes discrete, which is
            # what a bar chart's axis is anyway: one band per thing compared.
            x["type"] = "ordinal"

    encoding: dict[str, object] = {
        "x": x,
        "y": {"field": request.y, "type": "quantitative", "title": axis_title(request.y)},
    }
    if request.series is not None:
        encoding["color"] = {
            "field": request.series,
            "type": "nominal",
            "title": axis_title(request.series),
        }

    spec: dict[str, object] = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "mark": request.mark,
        "encoding": encoding,
        # Inline values, never a URL: a spec is rendered in the reader's browser,
        # so an address in it is a request that browser would make.
        "data": {
            "values": [
                {
                    name: _plottable(row[index])
                    for index, name in enumerate(frame.columns)
                    if index < len(row)
                }
                for row in frame.rows
            ]
        },
    }
    if request.title:
        spec["title"] = request.title
    return spec
