"""The corpus the classifier is judged against.

Two failure modes, and they are not symmetric. A **false negative** leaves
personal data unmasked in our own database, which is the failure this whole
subsystem exists to prevent. A **false positive** masks a harmless column until
somebody clicks Allow. So the true-positive cases below are requirements, and
the false-positive cases are a budget: the point of listing them is to notice
when the rules start firing on everything.
"""

from __future__ import annotations

import pytest

from dataagent.catalog.classify import (
    classify_column,
    is_textual,
    mask_value,
    name_verdict,
    semantic_role_of,
    shape_verdict,
)

EMAILS = ["ada@example.com", "grace@navy.mil", "linus@kernel.org"]
PHONES = ["+64 21 555 0100", "021 555 0101", "+1 (555) 010-2000"]
# A number that satisfies Luhn, from the test range card issuers publish for it.
CARDS = ["4111111111111111", "4012888888881881", "4222222222222"]
IBANS = ["GB82WEST12345698765432", "DE89370400440532013000", "FR1420041010050500013M02606"]
HARMLESS = ["Wellington", "Auckland", "Christchurch"]


# ---------------------------------------------------------------------------
# What the name alone says
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("column", "kind"),
    [
        ("email", "email"),
        ("customer_email", "email"),
        ("e_mail", "email"),
        ("EmailAddress", "email"),
        ("phone", "phone"),
        ("mobile_number", "phone"),
        ("ssn", "national_id"),
        ("national_id", "national_id"),
        ("passport_no", "national_id"),
        ("card_number", "payment"),
        ("iban", "payment"),
        ("sort_code", "payment"),
        ("password_hash", "credential"),
        ("api_key", "credential"),
        ("date_of_birth", "dob"),
        ("annual_salary", "financial"),
        ("address_line_1", "address"),
        ("postcode", "address"),
        ("first_name", "name"),
    ],
)
def test_a_name_that_announces_itself(column: str, kind: str) -> None:
    assert name_verdict(column) == kind


@pytest.mark.parametrize(
    "column",
    ["id", "order_date", "total_amount", "status", "city", "quantity", "created_at", "store_id"],
)
def test_ordinary_names_are_left_alone(column: str) -> None:
    """The false-positive budget. Every one of these masked would make the
    catalog useless without protecting anybody."""
    assert name_verdict(column) is None


# ---------------------------------------------------------------------------
# What the values say, whatever the column is called
# ---------------------------------------------------------------------------


def test_a_column_of_addresses_is_caught_however_it_is_named() -> None:
    """The case that matters most: `contact_1` holding email addresses."""
    assert shape_verdict(EMAILS) == "email"


def test_card_numbers_are_caught_by_luhn_not_by_length() -> None:
    assert shape_verdict(CARDS) == "payment"
    # Sixteen digits that are not a card: an order reference, say.
    assert shape_verdict(["1234567890123456", "1234567890123457"]) != "payment"


def test_ibans_and_phones_are_recognised() -> None:
    assert shape_verdict(IBANS) == "payment"
    assert shape_verdict(PHONES) == "phone"


@pytest.mark.parametrize(
    "values",
    [
        pytest.param(["2025-02-01", "2026-07-31", "2025-11-05"], id="ISO dates"),
        pytest.param(["2025-02-01 09:15:00", "2026-07-31 22:04:11"], id="timestamps"),
    ],
)
def test_a_date_is_not_a_phone_number(values: list[str]) -> None:
    """Found by profiling the real pizza database, not by this corpus.

    `2025-02-01` is ten characters of digits and separators, which an earlier
    pattern read as a phone number — so every date column in the demo came back
    "suspected" and would have been masked. Dates are what analysts filter on;
    masking them makes a catalog useless while protecting nobody.
    """
    assert shape_verdict(values) is None


@pytest.mark.parametrize(
    ("data_type", "textual"),
    [
        ("text", True),
        ("varchar(320)", True),
        ("nvarchar(max)", True),
        ("date", False),
        ("timestamp with time zone", False),
        ("integer", False),
        ("numeric(8, 2)", False),
    ],
)
def test_shapes_are_only_asked_about_text(data_type: str, textual: bool) -> None:
    """A numeric column of 16-digit order references is not a card number, and
    a date column is not a phone book."""
    assert is_textual(data_type) is textual


def test_a_numeric_column_that_looks_like_a_card_is_not_treated_as_one() -> None:
    verdict = classify_column(
        name="reference",
        data_type="bigint",
        is_pk=False,
        values=CARDS,
        distinct=3,
        sampled=3,
    )

    assert verdict.sensitivity == "none"


def test_ordinary_values_are_left_alone() -> None:
    assert shape_verdict(HARMLESS) is None
    assert shape_verdict(["12.50", "3.00", "8.25"]) is None
    assert shape_verdict([]) is None


def test_a_mostly_clean_column_still_counts() -> None:
    """Real columns have holes in them; a rule that needs perfect data never
    fires on anything real."""
    assert shape_verdict([*EMAILS, "", "n/a"]) == "email"


def test_a_column_with_one_stray_address_does_not() -> None:
    assert shape_verdict([*HARMLESS, "ada@example.com"]) is None


# ---------------------------------------------------------------------------
# The verdict, and what follows from it
# ---------------------------------------------------------------------------


def test_suspicion_is_enough() -> None:
    verdict = classify_column(
        name="contact",
        data_type="text",
        is_pk=False,
        values=EMAILS,
        distinct=3,
        sampled=3,
    )

    assert verdict.sensitivity == "suspected"
    assert verdict.kind == "email"


def test_confirmed_is_never_something_the_rules_award_themselves() -> None:
    """`confirmed` is a person's word (architecture M4). The classifier may only
    ever reach `suspected`, however sure it is."""
    for values, name in ((EMAILS, "email"), (CARDS, "card_number"), (HARMLESS, "city")):
        verdict = classify_column(
            name=name, data_type="text", is_pk=False, values=values, distinct=3, sampled=3
        )
        assert verdict.sensitivity in {"none", "suspected"}


def test_a_key_named_like_an_identifier_is_not_masked_for_it() -> None:
    """Masking a join key would break the catalog and protect nobody."""
    verdict = classify_column(
        name="id", data_type="integer", is_pk=True, values=["1", "2"], distinct=2, sampled=2
    )

    assert verdict.sensitivity == "none"
    assert verdict.semantic_role == "id"


def test_a_key_whose_values_are_personal_still_is() -> None:
    """…but a table keyed on a national id is a different thing entirely."""
    verdict = classify_column(
        name="national_id",
        data_type="varchar(20)",
        is_pk=True,
        values=["AB123456C"],
        distinct=1,
        sampled=1,
    )

    assert verdict.sensitivity == "suspected"


# ---------------------------------------------------------------------------
# Semantic role
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "data_type", "is_pk", "expected"),
    [
        ("order_date", "date", False, "time"),
        ("created_at", "timestamp with time zone", False, "time"),
        ("id", "integer", True, "id"),
        ("store_id", "integer", False, "id"),
        ("total_amount", "numeric(8, 2)", False, "measure"),
        ("is_active", "boolean", False, "dimension"),
        ("channel", "text", False, "dimension"),
    ],
)
def test_what_a_column_is_for(name: str, data_type: str, is_pk: bool, expected: str) -> None:
    assert (
        semantic_role_of(name=name, data_type=data_type, is_pk=is_pk, distinct=3, sampled=100)
        == expected
    )


def test_free_text_is_not_a_dimension() -> None:
    """A column with a distinct value per row is not something to group by."""
    assert (
        semantic_role_of(name="notes", data_type="text", is_pk=False, distinct=980, sampled=1000)
        == "other"
    )


# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------


def test_a_masked_address_keeps_its_shape_and_loses_its_content() -> None:
    masked = mask_value("ada@example.com", "email")

    assert masked == "a***@e***.com"
    assert "ada" not in masked
    assert "example" not in masked


@pytest.mark.parametrize(
    "value", ["4111111111111111", "+64 21 555 0100", "AB123456C", "Sourabh Kumrawat"]
)
def test_nothing_recoverable_survives_a_generic_mask(value: str) -> None:
    masked = mask_value(value)

    assert value not in masked
    assert len(masked) <= 6
    # The first and last characters are all that remain, and only for values
    # long enough that they identify nobody on their own.
    assert masked.startswith(value[0])


def test_short_values_lose_everything() -> None:
    """Two characters with the first and last kept is two characters kept."""
    assert mask_value("ab") == "**"
    assert mask_value("abcd") == "****"


def test_masking_an_empty_value_is_not_an_error() -> None:
    assert mask_value("") == ""
