"""What form a result should take, decided by the platform (WP13.20).

Until now the model chose the chart and the platform only validated it:
`ChartAsk` carries `mark`, `x`, `y` and `series`, all model-filled, and
`charts.decide` either builds a spec or writes a refusal. So the *form* of an
answer was free-styled, and a bar chart, a line chart and no chart at all were
one temperature sample apart. The owner's line is that the platform should choose
the form from **the shape of the result**.

**From the values, never from the names.** `charts.kind_of` already classifies a
column as quantitative, temporal or nominal by looking at what is in it — a
column called `order_date` holding `Q1`/`Q2` is not a date — and this module adds
no name-reading of its own. That is the same rule `inference.py` holds to, one
layer up.

**What it decides, and the one case it was built for.** A temporal axis against a
number is a **line**; a category against a number is a **bar**, because a bar is
what a comparison between categories reads as and a line drawn between two
unrelated categories implies a progression that does not exist. And a *third*
column that is a category becomes a **series** rather than a second answer —
*outlet A and outlet B monthly* is one chart with two lines, which is the case
the owner named and the case `ChartAsk.series` was built for and never used.

**Whether to draw at all stays with the model.** This module answers *what form*,
not *whether* — a chart forced onto every answer is its own kind of noise, and
"is a picture worth it here" is a judgement about the question rather than about
the result's shape. The model's `of` is untouched.

**The model's fields are overridden, not removed.** Narrowing `ChartAsk` to stop
it sending a mark would mean deleting model-filled fields from a schema that is
`extra="forbid"`, and **D-044 is the warning**: removing `FinalizeIn.answered`
broke three producers found separately over two hours, one of which ships in the
product image. The fields stay and their values are replaced, which costs a few
tokens of output and no migration, no fixture sweep and no risk.
"""

from __future__ import annotations

from dataclasses import dataclass

from dataagent.agent.charts import MAX_CATEGORIES, MAX_SERIES, Frame, kind_of

__all__ = ["Shape", "shape_of"]


@dataclass(frozen=True, slots=True)
class Shape:
    """The form a result should take, and why — the *why* being for the trace.

    ``mark`` empty means no chart, and ``reason`` then says what about the result
    made a picture the wrong answer. B-087's discipline applied to form: say the
    thing that did not happen.
    """

    mark: str = ""
    x: str = ""
    y: str = ""
    series: str = ""
    reason: str = ""

    @property
    def draws(self) -> bool:
        return bool(self.mark)


def _columns_by_kind(frame: Frame) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {"quantitative": [], "temporal": [], "nominal": []}
    for index, name in enumerate(frame.columns):
        values = [row[index] for row in frame.rows if index < len(row)]
        found[kind_of(values)].append(name)
    return found


def _distinct(frame: Frame, name: str) -> int:
    index = frame.columns.index(name)
    return len({row[index] for row in frame.rows if index < len(row)})


def shape_of(frame: Frame) -> Shape:
    """The form this result should take.

    Order matters: the temporal axis is tried before the categorical one, because
    a result carrying both a date and a category is a series over time rather
    than a comparison between categories — *revenue by month per outlet* is a
    line chart with one line each, and drawing it as bars grouped by outlet
    throws the time axis away.
    """
    if not frame.rows or not frame.columns:
        return Shape(reason="the result has no rows to draw")
    if frame.truncated:
        # `decide` refuses this too. Saying it here as well means the reason a
        # reader sees is about the data rather than about a mark nobody chose.
        return Shape(
            reason="the result was cut off at the row limit, so a chart of it "
            "would look complete while being partial"
        )

    kinds = _columns_by_kind(frame)
    numbers = kinds["quantitative"]
    if not numbers:
        return Shape(reason="nothing in the result is a number to plot")
    if len(frame.rows) == 1 and len(frame.columns) == 1:
        return Shape(reason="a single figure is a sentence, not a picture")

    y = numbers[0]

    for axis, mark in (("temporal", "line"), ("nominal", "bar")):
        candidates = [name for name in kinds[axis] if name != y]
        if not candidates:
            continue
        x = candidates[0]
        if axis == "nominal" and _distinct(frame, x) > MAX_CATEGORIES:
            return Shape(
                reason=f"{x} has more distinct values than a chart has room for",
            )
        # A remaining category splits the marks rather than starting a second
        # answer. Only when it is small enough to read as a legend.
        rest = [
            name
            for name in kinds["nominal"]
            if name not in {x, y} and _distinct(frame, name) <= MAX_SERIES
        ]
        series = rest[0] if rest else ""
        why = f"{'a date' if axis == 'temporal' else 'a category'} against a number"
        if series:
            why += f", split by {series}"
        return Shape(mark=mark, x=x, y=y, series=series, reason=why)

    return Shape(reason="the result has numbers but nothing to plot them against")
