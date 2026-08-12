"""Which columns hold personal data, and what a stored sample may look like.

Deterministic rules, in this order (architecture Part 5.2): the column's *name*,
then the *shape of its values* in the sample. An optional cheap-LLM pass may
suggest more in a later phase; it may never quietly un-suggest, because the
direction that matters is one-way.

Two decisions are worth stating plainly.

**Suspicion is enough to mask.** A column the rules suspect defaults to
``mask``, before anyone has looked at it. The cost of masking a harmless column
is that someone clicks Allow; the cost of the other mistake is a customer's
personal data sitting in our database in the clear. Those are not comparable,
so they are not traded off.

**Masking happens at write time, not at read time.** Everything in this module
exists to be called *before* a value reaches ``catalog_columns``. A raw email
address that arrives in the platform database and is hidden by a query later has
already been stored, backed up, and replicated — architecture M4 asks for the
sample to be masked as it is written, and that is the only version that is
actually true.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "SENSITIVE_NAME_PATTERNS",
    "Verdict",
    "classify_column",
    "is_textual",
    "mask_value",
    "semantic_role_of",
]

#: How much of a sample has to look like a thing before the column *is* that
#: thing. Deliberately not 1.0: real columns hold nulls, blanks and the odd
#: "n/a", and a rule that only fires on perfect data never fires.
_SHAPE_THRESHOLD = 0.6

#: Names that are evidence on their own. Word-boundary matched, so `email` fires
#: on `customer_email` and `email_address` but not on `emailed_at_least_once`…
#: which it would, and that is the acceptable direction of error.
SENSITIVE_NAME_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"e[-_]?mail", "email"),
    (r"phone|mobile|telephone|msisdn", "phone"),
    (r"ssn|social_security|national_id|nino|aadhaar|tax_id", "national_id"),
    (r"passport|driver_?licen[cs]e", "national_id"),
    (r"card_?number|pan\b|iban|bic\b|swift|account_?number|sort_?code", "payment"),
    (r"password|passwd|secret|token|api_?key|private_?key", "credential"),
    (r"date_?of_?birth|dob\b|birth_?date", "dob"),
    (r"salary|compensation|wage|income", "financial"),
    (r"address_?line|postcode|post_?code|zip_?code|street", "address"),
    (r"full_?name|first_?name|last_?name|surname|given_?name", "name"),
)

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.IGNORECASE)
#: At least nine digits, because eight is a date. `2025-02-01` matched a looser
#: pattern and every date column in the pizza database came back "suspected" —
#: found by profiling a real one, not by this file's own corpus, which is why
#: `test_a_date_is_not_a_phone_number` now exists.
_PHONE = re.compile(r"^\+?(?=(?:\D*\d){9,})[\d\s().-]{9,20}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]|$)")
_DIGITS = re.compile(r"\D")
_IBAN = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$", re.IGNORECASE)


_TIME_TYPES = ("date", "time", "timestamp", "datetime", "datetimeoffset", "smalldatetime")
_NUMBER_TYPES = (
    "int",
    "bigint",
    "smallint",
    "tinyint",
    "numeric",
    "decimal",
    "real",
    "double",
    "float",
    "money",
)


@dataclass(frozen=True, slots=True)
class Verdict:
    """What the rules concluded about one column.

    ``kind`` names *why* — "email", "payment", "credential" — which is what an
    Admin needs in order to disagree with it.
    """

    sensitivity: str
    kind: str | None
    semantic_role: str


def _looks_like(values: Sequence[str], pattern: re.Pattern[str]) -> bool:
    considered = [value for value in values if value.strip()]
    if not considered:
        return False
    matched = sum(1 for value in considered if pattern.match(value.strip()))
    return matched / len(considered) >= _SHAPE_THRESHOLD


def _luhn(value: str) -> bool:
    digits = [int(character) for character in _DIGITS.sub("", value)]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def _card_like(values: Sequence[str]) -> bool:
    considered = [value for value in values if value.strip()]
    if not considered:
        return False
    return sum(1 for value in considered if _luhn(value)) / len(considered) >= _SHAPE_THRESHOLD


def name_verdict(column_name: str) -> str | None:
    """What the column's name alone suggests, if anything."""
    lowered = column_name.lower()
    for pattern, kind in SENSITIVE_NAME_PATTERNS:
        if re.search(pattern, lowered):
            return kind
    return None


def is_textual(data_type: str) -> bool:
    """Is this a column whose values are worth pattern-matching?

    Value shapes are a heuristic about *text*. A date column holds dates and a
    numeric column holds numbers; asking whether those look like a phone number
    is asking a question with no useful answer, and getting a wrong one.
    """
    lowered = data_type.lower()
    return not any(marker in lowered for marker in (*_TIME_TYPES, *_NUMBER_TYPES))


def shape_verdict(values: Sequence[str]) -> str | None:
    """What the sampled values look like, if they agree on anything."""
    if any(_ISO_DATE.match(value.strip()) for value in values if value.strip()):
        # A belt for the braces above: even a text column full of ISO dates is a
        # date column, whatever its declared type says.
        return None
    if _looks_like(values, _EMAIL):
        return "email"
    if _looks_like(values, _IBAN):
        return "payment"
    if _card_like(values):
        return "payment"
    if _looks_like(values, _PHONE):
        return "phone"
    return None


def semantic_role_of(*, name: str, data_type: str, is_pk: bool, distinct: int, sampled: int) -> str:
    """measure | dimension | time | id | other (architecture Part 5.3)."""
    lowered_type = data_type.lower()
    lowered_name = name.lower()

    if any(marker in lowered_type for marker in _TIME_TYPES):
        return "time"
    if is_pk or lowered_name == "id" or lowered_name.endswith("_id"):
        return "id"
    if any(marker in lowered_type for marker in _NUMBER_TYPES):
        return "measure"
    if "bool" in lowered_type or lowered_type == "bit":
        return "dimension"
    # A text column with few distinct values is a category; one with many is
    # free text, and calling that a dimension would invite grouping by it.
    if sampled and distinct <= max(50, sampled // 20):
        return "dimension"
    return "other"


def classify_column(
    *,
    name: str,
    data_type: str,
    is_pk: bool,
    values: Sequence[str],
    distinct: int,
    sampled: int,
) -> Verdict:
    """Name first, then shape. Either is enough to suspect.

    A primary key is never suspected on its name alone — `id` matches nothing
    here, but a table keyed on `national_id` would, and masking a join key would
    break the catalog for no gain. Its *values* can still condemn it.
    """
    role = semantic_role_of(
        name=name, data_type=data_type, is_pk=is_pk, distinct=distinct, sampled=sampled
    )
    kind = name_verdict(name) or (shape_verdict(values) if is_textual(data_type) else None)
    if kind is None:
        return Verdict(sensitivity="none", kind=None, semantic_role=role)
    return Verdict(sensitivity="suspected", kind=kind, semantic_role=role)


def mask_value(value: str, kind: str | None = None) -> str:
    """A rendering that keeps the shape and loses the content.

    Format-preserving where it is cheap, because a masked sample still has to be
    useful: `a***@e***.com` tells a person the column holds addresses at this
    domain shape, which is the entire reason a sample is shown at all.
    """
    if not value:
        return value
    if kind == "email" and "@" in value:
        local, _, domain = value.partition("@")
        name, dot, tld = domain.rpartition(".")
        masked_domain = f"{name[:1]}***{dot}{tld}" if dot else f"{domain[:1]}***"
        return f"{local[:1]}***@{masked_domain}"
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:1]}***{value[-1:]}"
