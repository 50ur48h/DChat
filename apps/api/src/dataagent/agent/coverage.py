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
    "Asked",
    "Coverage",
    "Period",
    "asked_for",
    "available_period",
    "coverage_note",
    "describe",
    "held_days",
    "limitation",
    "period_of_values",
]

#: Column types whose low/high is a period rather than a magnitude. Deliberately
#: a subset of `profiler._RANGED_TYPES`: an integer column has a range too, and
#: `2020` to `2024` on a column called `qty` is not a period.
TIME_TYPES: tuple[str, ...] = ("date", "timestamp", "datetime", "smalldatetime", "time")

#: A year and a month, which is the finest grain both sides can honestly express.
#: `2025-01-01`, `2025-01-01 00:00:00` and `2023-01` all reduce to the same thing;
#: `opening`, `7.30 pm` and `Q1` reduce to nothing and are never compared.
#:
#: **Anchored at both ends, and the month is 01-12** — the loose version matched a
#: prefix, and every cell of the answer's result passes through here. An order
#: code like `2024-0012` would have become "the year 2024, month 00" and dragged
#: a coverage sentence with it. A value is a period or it is not; something that
#: merely *starts* like one is not.
_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])(?:-\d{2})?(?:[ T][\d:.+\-]*)?$")


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


def coverage_note(available: Period | None, asked: Asked | None = None) -> str:
    """What the planner is told as fact (architecture 4.3), or nothing.

    **One note, not two.** The general fact — *this database holds these
    periods* — and the specific one — *the period you asked for is not among
    them* — compete for the same L0 slot, and shipping both would be the padding
    that teaches people to skip the layer. So the specific sentence is said when
    there is one, and the general sentence otherwise.

    **Not enforcement, and the module says so**: the composer's limitation and
    the refusal are what hold. This is the half that stops the wrong sentence
    being written at all — B-157's refusal, three months of 2025 declared missing
    while `dim_calendar` held every one of them, is a sentence a planner shown
    this range would not have written.
    """
    if asked is not None and asked.held is not None and asked.asked is not None:
        low, high = asked.held
        first, last = asked.asked[0], _before(asked.asked[1])
        if asked.verdict == "none":
            return (
                f"The question asks about {first} to {last}. This database holds "
                f"{low} to {high}, so **none** of the period asked for exists here. Say that "
                f"plainly and name both periods. Do not report a zero: no rows and no data are "
                f"different answers, and only one of them is true."
            )
        if asked.verdict == "partial" and asked.overlap is not None:
            begin, until = asked.overlap
            return (
                f"The question asks about {first} to {last}. This database holds {low} to "
                f"{high}, so only {begin} to {until} of it exists. Answer for the part that "
                f"exists and say which part does not — **narrowing to the data is correct here, "
                f"not a compromise**, and an answer that queries past {high} is describing rows "
                f"that are not there."
            )
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


# ---------------------------------------------------------------------------
# The period a question asked for, against the period the data holds (D-051)
# ---------------------------------------------------------------------------

#: A full ISO date, which is what a profiled `date` column stores and what
#: `critic.stated_range` resolves a question to. Kept apart from `_MONTH`
#: deliberately: **this comparison is done at day precision and the other one is
#: not**, and the reason is which sides are being compared. An answer's own rows
#: may hold a text period like `2023-01`, so months are the finest grain both
#: sides can honestly express there. Here both sides are real dates, and rounding
#: to months would call a question about December *covered* by data that stops on
#: the 15th.
_DAY = re.compile(r"^(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True, slots=True)
class Asked:
    """What a question wanted, against what the catalog holds.

    ``verdict`` is one of:

    * ``"covered"`` — every day asked for is inside the data.
    * ``"partial"`` — some of it is. ``overlap`` is the part that exists, and it
      is what an honest answer narrows to.
    * ``"none"`` — no day asked for exists. **The correct ending is a refusal**
      naming both periods; a confident zero is not (D-051).
    * ``"unknown"`` — the question named no period, or nothing is profiled. The
      common case, and the check must not fire on it.
    """

    verdict: str
    asked: tuple[str, str] | None = None
    held: tuple[str, str] | None = None
    overlap: tuple[str, str] | None = None


def _day(value: object) -> str | None:
    if value is None:
        return None
    match = _DAY.match(str(value).strip())
    return match.group(1) if match else None


def held_days(columns: Sequence[tuple[str, str | None, str | None]]) -> tuple[str, str] | None:
    """The widest span of full dates the catalog records, at day precision."""
    ends: list[str] = []
    for data_type, minimum, maximum in columns:
        if not any(kind in data_type.lower() for kind in TIME_TYPES):
            continue
        ends.extend(day for value in (minimum, maximum) if (day := _day(value)) is not None)
    if not ends:
        return None
    ends.sort()
    return ends[0], ends[-1]


def asked_for(wanted: tuple[str, str] | None, held: tuple[str, str] | None) -> Asked:
    """Compare the period a question named with the period the data holds.

    ``wanted`` is half-open — ``critic.stated_range``'s own convention, kept
    rather than converted, because the check and the critic have to resolve one
    period or they will disagree and the critic will block the corrected answer.
    ``held`` is inclusive, because that is what a profiled min and max are.
    """
    if wanted is None or held is None:
        return Asked(verdict="unknown", asked=wanted, held=held)
    start, end_exclusive = wanted
    low, high = held
    # `high` is the last day that exists; the asked range ends the day before
    # `end_exclusive`.
    last_asked = min(_before(end_exclusive), high)
    first_asked = max(start, low)
    if first_asked > last_asked:
        return Asked(verdict="none", asked=wanted, held=held)
    if start >= low and _before(end_exclusive) <= high:
        return Asked(
            verdict="covered", asked=wanted, held=held, overlap=(start, _before(end_exclusive))
        )
    return Asked(verdict="partial", asked=wanted, held=held, overlap=(first_asked, last_asked))


def _before(day: str) -> str:
    """The day before an exclusive end, as ISO text."""
    from datetime import date as _date

    return _date.fromordinal(_date.fromisoformat(day).toordinal() - 1).isoformat()
