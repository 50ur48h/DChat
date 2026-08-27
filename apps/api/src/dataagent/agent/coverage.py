"""What periods this database can speak about, and whether an answer stayed inside them.

**This is deliberately narrower than "the coverage claim is checked", and the
narrowing is the first thing to read** (D-058 as amended, B-157). It catches *an
answer resting on a window the catalog does not describe*. It does **not** catch
a false statement about coverage in general: nothing here reads the answer's
prose, so a model that writes *"we only hold 2023 data"* over a correct 2025
result is not caught by this and never will be.

That limit is a choice rather than an omission. The alternative was to parse a
range assertion out of model-written prose, and a number-shaped verdict drawn
from evidence that cannot carry one is the same mistake as trusting a similarity
score because it has a number attached (owner, 2026-08-27). **A check that fails
silently on phrasing is worse than a narrower check that fails loudly**, so this
one measures two things it can actually measure and says plainly when it can
measure neither.

**Both sides are measurements, never claims.**

* *Available* comes from `CatalogColumn.min_val`/`.max_val`, which the profiler
  takes from the engine's own aggregate and never from a sample — B-051 forbids a
  derived range, and that is what makes this side worth comparing against.
* *Answered* comes from the rows the run actually returned, and only when the
  result was **not truncated**. A truncated result's last row is a floor, not a
  latest, and a coverage sentence built on one would be exactly the confident
  half-truth this module exists to prevent.

**Abstention is an outcome, not a silence.** A source nobody profiled, a result
that was truncated, a period column the profiler gives no range to — each ends
with a reason a person can read, because a run where the check could not fire
must be distinguishable from a run where it fired and passed. That is D-031's
rule for embeddings applied here, and it is why `Coverage.status` has three
values rather than two.

**Months, not days.** The two sides come from different places and often
different precisions: a `date` column profiles as `2025-01-01` while a text period
column holds `2023-01`. Comparing at month granularity is the finest thing both
can honestly express, and it is also the grain a coverage sentence is read at.
Anything that does not look like a year and a month is not compared at all.

**What triggers it, and why not "narrower".** The rule is *not contained*: the
answer covers a period the catalog's dated columns do not. "Narrower" was the
obvious first rule and it fires on every ordinary question — *"sales last month"*
legitimately returns one month out of a year — which would have taught people to
skip the caveat, the failure B-146's coverage floor and D-034's budget caveat were
both written against. Containment is quiet on that question and loud on B-157's,
where an answer built from a 2023-2024 back-cast sat beside a catalog that
records 2025 and nothing else.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "TIME_TYPES",
    "Coverage",
    "Period",
    "available_period",
    "coverage_note",
    "describe",
    "limitation",
    "period_of_values",
]

#: Column types whose low/high is a period rather than a magnitude. Deliberately
#: a subset of `profiler._RANGED_TYPES`: an integer column has a range too, and
#: `2020` to `2024` on a column called `qty` is not a period.
TIME_TYPES: tuple[str, ...] = ("date", "timestamp", "datetime", "smalldatetime", "time")

#: A year and a month at the start of a value, which is the finest grain both
#: sides can honestly express. `2025-01-01`, `2025-01-01 00:00:00` and `2023-01`
#: all reduce to the same thing; `opening`, `7.30 pm` and `Q1` reduce to nothing
#: and are therefore never compared.
_MONTH = re.compile(r"^(\d{4})-(\d{2})")


@dataclass(frozen=True, slots=True)
class Period:
    """A closed range of months, as ``YYYY-MM``."""

    earliest: str
    latest: str

    def __str__(self) -> str:
        return (
            self.earliest if self.earliest == self.latest else f"{self.earliest} to {self.latest}"
        )

    def contains(self, other: Period) -> bool:
        return self.earliest <= other.earliest and other.latest <= self.latest


@dataclass(frozen=True, slots=True)
class Coverage:
    """The verdict, and enough of the measurement to argue with it.

    ``status`` is one of:

    * ``"outside"`` — the answer covers a period the catalog does not describe.
      The only status that produces a limitation.
    * ``"contained"`` — the answer sits inside what the catalog records. Nothing
      to say, and saying nothing is correct.
    * ``"abstained"`` — the check could not run, and ``reason`` says why in words
      meant for a person.
    """

    status: str
    reason: str = ""
    answered: Period | None = None
    available: Period | None = None

    def as_payload(self) -> dict[str, object]:
        """For the trace. **The reason travels with the status**, because a run
        where this could not fire has to be distinguishable from one where it
        fired and passed — otherwise the absence of a caveat means two different
        things and a reader cannot tell which."""
        return {
            "status": self.status,
            "reason": self.reason,
            "answered": str(self.answered) if self.answered else None,
            "available": str(self.available) if self.available else None,
        }


def _month(value: object) -> str | None:
    """``YYYY-MM`` from anything that starts with one, else None."""
    if value is None:
        return None
    match = _MONTH.match(str(value).strip())
    return f"{match.group(1)}-{match.group(2)}" if match else None


def _span(values: Sequence[object]) -> Period | None:
    months = sorted(month for value in values if (month := _month(value)) is not None)
    return Period(earliest=months[0], latest=months[-1]) if months else None


def available_period(columns: Sequence[tuple[str, str | None, str | None]]) -> Period | None:
    """The widest period the catalog records, over ``(data_type, min, max)``.

    Only columns whose type is a time, and only where the profiler stored both
    ends. A source nobody has profiled produces nothing here, which the caller
    reports as an abstention rather than as an empty range — *no profile* and
    *no dates* are different facts and the second one is a claim.
    """
    ends: list[object] = []
    for data_type, minimum, maximum in columns:
        if not any(kind in data_type.lower() for kind in TIME_TYPES):
            continue
        ends.extend(value for value in (minimum, maximum) if value is not None)
    return _span(ends)


def period_of_values(values: Sequence[object], *, truncated: bool) -> tuple[Period | None, str]:
    """The period a result covers, or the reason it cannot be said.

    ``truncated`` is the whole of B-051 in one argument: the DAL caps what comes
    back, and the last row of a capped result is a floor rather than a latest.
    A coverage sentence built on one would read as a fact about the customer's
    data while being a fact about our row limit.
    """
    if truncated:
        return None, "the result was cut off at the row limit, so its last period is not its latest"
    period = _span(values)
    if period is None:
        return None, "no column in the result held a recognisable year and month"
    return period, ""


def describe(answered: Period | None, available: Period | None, *, reason: str = "") -> Coverage:
    """Compare the two measurements, or say why there was nothing to compare."""
    if reason:
        return Coverage(status="abstained", reason=reason, answered=answered, available=available)
    if available is None:
        return Coverage(
            status="abstained",
            reason="no dated column in this database has been profiled, so there is nothing to "
            "compare against",
            answered=answered,
        )
    if answered is None:
        return Coverage(
            status="abstained",
            reason="the answer rests on no result with a recognisable period",
            available=available,
        )
    if available.contains(answered):
        return Coverage(status="contained", answered=answered, available=available)
    return Coverage(status="outside", answered=answered, available=available)


def coverage_note(available: Period | None) -> str:
    """What the planner is told as fact (architecture 4.3), or nothing.

    **Not enforcement, and it is not pretending to be.** The limitation the
    composer attaches is what holds; this is the cheap half of the pair, and it
    is the half that stops the wrong sentence being written at all. B-157's
    refusal — three months of 2025 declared missing while `dim_calendar` held
    every one of them — is a sentence a planner shown this range would not have
    written.
    """
    if available is None:
        return ""
    return (
        f"The dated columns in this database's catalogued tables run from "
        f"{available.earliest} to {available.latest}. That is what has been measured, not a "
        f"limit on what you may ask: a table whose period column is text has no measured "
        f"range and is not represented here. Do not tell the reader a period is missing "
        f"without checking it against this."
    )


def limitation(coverage: Coverage) -> str:
    """The sentence a reader gets, and only for ``outside``.

    Says both measurements rather than a verdict, because the useful thing for a
    person is the pair — *this answer is about that period, and the catalog
    describes this other one* — and because a reader who disagrees can go and
    look at either.
    """
    if coverage.status != "outside" or coverage.answered is None or coverage.available is None:
        return ""
    return (
        f"This answer covers {coverage.answered}, which is outside the period the catalog "
        f"records for this database's dated columns ({coverage.available}). Either it rests on "
        f"a table whose period column has no measured range, or it reaches beyond the data "
        f"that was profiled — both are worth checking before relying on the figures."
    )
