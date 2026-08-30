"""A number written down without its representation error (B-179)."""

from __future__ import annotations

import datetime as dt
import decimal
import uuid

from dataagent.dal.artifacts import encodable


def test_a_float_sum_loses_its_representation_error() -> None:
    """**What the owner actually read.** A month of sales came back from
    Postgres as `310817.08999999997` and reached the answer that way: seventeen
    digits, of which the last five are the binary artifact and not the money."""
    assert encodable(310817.08999999997) == 310817.09
    assert encodable(41060.11999999999) == 41060.12
    assert encodable(20469.380000000005) == 20469.38
    assert encodable(0.1 + 0.2) == 0.3


def test_a_small_number_keeps_its_magnitude() -> None:
    """Significant digits, not decimal places. Rounding to a fixed number of
    decimals would flatten a rate to zero, and a rate of `1.2e-7` is a real
    thing for a column to hold."""
    assert encodable(1.2345678e-7) == 1.2345678e-7
    assert encodable(1e300) == 1e300


def test_a_number_with_no_error_is_untouched() -> None:
    """The common case. Trimming must not move a figure anybody has."""
    for value in (0.0, 1.0, 2.5, 1973.0, 5449.39, -12.75):
        assert encodable(value) == value


def test_infinity_and_nan_are_not_quietly_made_into_numbers() -> None:
    """They are not noise — they are what the database returned, and turning one
    into a number would be worse than showing it."""
    assert encodable(float("inf")) == float("inf")
    assert encodable(float("-inf")) == float("-inf")
    nan = encodable(float("nan"))
    assert isinstance(nan, float) and nan != nan


def test_the_other_types_still_convert_as_they_did() -> None:
    """B-103 unified three copies of this rule; the float trim must not have
    changed what the other branches do."""
    assert encodable(None) is None
    assert encodable("Ayam Penyet") == "Ayam Penyet"
    assert encodable(7) == 7
    assert encodable(True) is True
    assert encodable(decimal.Decimal("12.30")) == "12.30"
    assert encodable(dt.date(2025, 12, 1)) == "2025-12-01"
    assert encodable(uuid.UUID(int=0)) == "00000000-0000-0000-0000-000000000000"
