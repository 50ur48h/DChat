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
        series_distinct = len({str(value) for value in _column_values(frame, request.series)})
        if series_distinct > MAX_CATEGORIES:
            return _decline(
                "too_many_series",
                f"No chart was drawn: {request.series!r} has {series_distinct:,} distinct values, "
                f"and a chart here shows at most {MAX_CATEGORIES} of them at once.",
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
    encoding: dict[str, object] = {
        "x": {"field": request.x, "type": x_kind, "title": axis_title(request.x)},
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
