"""What the coverage check will and will not say (B-157, D-058 as amended).

The narrowing is the thing these tests are mostly about. This check catches an
answer resting on a window the catalog does not describe; it does **not** catch a
false coverage statement in general, because nothing reads the answer's prose.
`test_a_false_sentence_over_a_correct_result_is_not_caught` is that limit written
down as a passing test rather than as a sentence in a docstring nobody re-reads —
someone who later reads "coverage check" and assumes the broader thing will find
it asserted here that it is not.
"""

from __future__ import annotations

from dataagent.agent.coverage import (
    Period,
    available_period,
    coverage_note,
    describe,
    limitation,
    period_of_values,
)


def _available(*columns: tuple[str, str, str]) -> Period | None:
    return available_period([(kind, low, high) for kind, low, high in columns])


# ---------------------------------------------------------------------------
# What the catalog says exists
# ---------------------------------------------------------------------------


def test_the_available_period_is_the_widest_across_dated_columns() -> None:
    period = _available(
        ("date", "2025-01-01", "2025-12-31"),
        ("timestamp without time zone", "2024-06-01 00:00:00", "2025-03-04 12:00:00"),
    )

    assert period == Period(earliest="2024-06", latest="2025-12")


def test_a_number_column_is_not_a_period_however_much_it_looks_like_one() -> None:
    """`bigint` from 2020 to 2024 is a range, not a range of dates. The subset of
    `_RANGED_TYPES` this module accepts is what keeps `qty` out of a sentence
    about time."""
    assert _available(("bigint", "2020", "2024")) is None


def test_a_text_period_column_contributes_nothing_and_that_is_deliberate() -> None:
    """**The case B-157 turned on.** `fact_sale_monthly_history.year_month` is
    TEXT holding `2023-01`, so the profiler gives it no range at all (B-051: an
    absent figure is safe and a sampled one is not). Widening `wants_range` to
    text to fix this would reopen exactly that, so the table the wrong answer came
    from stays invisible to this side of the comparison — and the check still
    works, because the *other* tables in the bundle are dated."""
    assert _available(("text", "2023-01", "2024-12")) is None
    assert _available(
        ("text", "2023-01", "2024-12"), ("date", "2025-01-01", "2025-12-31")
    ) == Period(earliest="2025-01", latest="2025-12")


def test_an_unprofiled_column_has_no_range_rather_than_an_empty_one() -> None:
    assert available_period([("date", None, None)]) is None


# ---------------------------------------------------------------------------
# What the answer covered
# ---------------------------------------------------------------------------


def test_the_answered_period_comes_from_the_rows_that_came_back() -> None:
    period, reason = period_of_values(["2023-01", "2024-12", "2023-07"], truncated=False)

    assert period == Period(earliest="2023-01", latest="2024-12")
    assert reason == ""


def test_a_truncated_result_abstains_rather_than_reporting_its_last_row() -> None:
    """B-051 in one argument. The last row of a capped result is a floor, and a
    coverage sentence built on one is a fact about our row limit wearing the
    clothes of a fact about the customer's data."""
    period, reason = period_of_values(["2025-01-01", "2025-02-01"], truncated=True)

    assert period is None
    assert "row limit" in reason


def test_values_that_are_not_periods_abstain_with_a_reason() -> None:
    period, reason = period_of_values(["opening", "7.30 pm", None], truncated=False)

    assert period is None
    assert reason, "an abstention with no reason is indistinguishable from a pass"


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


def test_an_answer_outside_what_the_catalog_records_is_reported() -> None:
    """B-157's first screenshot, in miniature: 24 figures for 2023-2024 while
    every dated column in the bundle ran through 2025."""
    coverage = describe(
        answered=Period(earliest="2023-01", latest="2024-12"),
        available=Period(earliest="2025-01", latest="2025-12"),
    )

    assert coverage.status == "outside"
    assert "2023-01 to 2024-12" in limitation(coverage)
    assert "2025-01 to 2025-12" in limitation(coverage)


def test_an_ordinary_question_about_one_month_says_nothing_at_all() -> None:
    """**Why the rule is containment and not "narrower".** *"Sales last month"*
    returns one month out of a year and is completely correct; a check that
    caveated it would be teaching people to skip caveats, which is the failure
    D-034's budget caveat and B-146's coverage floor were both written against."""
    coverage = describe(
        answered=Period(earliest="2025-12", latest="2025-12"),
        available=Period(earliest="2025-01", latest="2025-12"),
    )

    assert coverage.status == "contained"
    assert limitation(coverage) == ""


def test_an_answer_reaching_past_the_end_of_the_data_is_reported() -> None:
    coverage = describe(
        answered=Period(earliest="2025-06", latest="2026-03"),
        available=Period(earliest="2025-01", latest="2025-12"),
    )

    assert coverage.status == "outside"


def test_an_unprofiled_source_abstains_and_says_which_kind_of_nothing_it_is() -> None:
    coverage = describe(answered=Period(earliest="2025-01", latest="2025-03"), available=None)

    assert coverage.status == "abstained"
    assert "profiled" in coverage.reason
    assert limitation(coverage) == "", "an abstention must not produce a caveat"


def test_an_abstention_carries_its_reason_into_the_trace_payload() -> None:
    """The owner's requirement, asserted on the payload rather than on the object:
    a run where the check could not fire must be distinguishable from one where it
    fired and passed, and the trace is where a person looks."""
    period, reason = period_of_values([], truncated=True)
    payload = describe(answered=period, available=None, reason=reason).as_payload()

    assert payload["status"] == "abstained"
    assert payload["reason"], "the trace would show an abstention with no explanation"


def test_a_pass_is_told_apart_from_an_abstention_in_the_payload() -> None:
    passed = describe(
        answered=Period(earliest="2025-02", latest="2025-03"),
        available=Period(earliest="2025-01", latest="2025-12"),
    ).as_payload()

    assert passed["status"] == "contained"
    assert passed["reason"] == ""
    assert passed["answered"] == "2025-02 to 2025-03"
    assert passed["available"] == "2025-01 to 2025-12"


def test_a_false_sentence_over_a_correct_result_is_not_caught() -> None:
    """**The narrowing, written as a test so it cannot be forgotten.**

    A model that answers correctly from 2025 rows and then writes *"we only hold
    2023 data"* passes this check, because nothing here reads what it wrote. That
    is the deliberate limit (owner, 2026-08-27): parsing a range assertion out of
    prose would produce a number-shaped verdict from evidence that cannot carry
    one, and a check that fails silently on phrasing is worse than a narrower
    check that fails loudly.
    """
    coverage = describe(
        answered=Period(earliest="2025-01", latest="2025-12"),
        available=Period(earliest="2025-01", latest="2025-12"),
    )

    assert coverage.status == "contained", (
        "the result is inside the catalog's range; whatever prose was written "
        "about it is not this check's business"
    )


# ---------------------------------------------------------------------------
# What the planner is told
# ---------------------------------------------------------------------------


def test_the_planner_is_told_the_range_and_told_it_is_not_a_limit() -> None:
    """The capability note is not enforcement and must not read like it. It exists
    to stop B-157's refusal being written — three months of 2025 declared missing
    while `dim_calendar` held every one of them."""
    note = coverage_note(Period(earliest="2025-01", latest="2025-12"))

    assert "2025-01" in note and "2025-12" in note
    assert "not a limit on what you may ask" in note
    assert "text" in note, "a model told this range must know which tables it omits"


def test_there_is_no_note_when_there_is_no_measurement() -> None:
    assert coverage_note(None) == ""


def test_a_value_that_merely_starts_like_a_period_is_not_one() -> None:
    """**Every cell of the answer's result passes through the parser**, not just
    the date column — the check does not know which column is which, and asking
    it to guess would be name-matching by another route.

    So the pattern is anchored at both ends and the month is 01-12. The loose
    version matched a prefix, which would have read an order code like
    `2024-0012` as *"the year 2024, month 00"* and dragged a whole coverage
    sentence along behind it.
    """
    period, _ = period_of_values(["INV-2024-01", "2024-0012", "2024-13", "ORD-99"], truncated=False)

    assert period is None


def test_a_real_period_beside_noise_is_still_found() -> None:
    """The other half: anchoring must not make the parser so strict that a genuine
    date column stops being read because the row it sits in also holds a code."""
    period, _ = period_of_values(
        ["ORD-99", "2025-03-04", "Harbour", "2025-07-01 00:00:00"], truncated=False
    )

    assert period == Period(earliest="2025-03", latest="2025-07")
