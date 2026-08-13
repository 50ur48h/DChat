"""Masking the values that come back (architecture Part 7.6).

Catalog samples were masked at write time (D-013), which said nothing about
query results: a sample of `customers.email` in the catalog is already
`a***@d***.com`, and a *query* for that column would still have returned the
address. This is where that stops.

Three rules decide what happens to a cell:

* **Which cells.** ``Validated.projections`` says, per output position, whether
  a masked column's value can be read out of it — the column itself, or anything
  built from it. Position rather than name, because ``SELECT c.email, s.email``
  returns two columns called ``email``.
* **How.** An Admin's ``mask_type`` if they set one, otherwise ``auto``, which
  reads the shape off the value itself: an address comes back as an address, a
  phone number keeps its last four digits, everything else is redacted. The
  column's *semantic role* is not consulted, because it answers a different
  question — ``measure | dimension | time | id`` is about how a column is used
  in analysis, not about what kind of personal data it holds. A projection that
  is *derived* from a masked column (``UPPER(email)``) is always redacted in
  full: the shape of the value is no longer known, so preserving it would be
  pretending rather than preserving.
* **NULL stays NULL.** Masking an absent value would invent a present one, and
  "this customer has no email" is not a secret about the customer.

The masked value is deliberately not reversible and not a token: nothing else in
the system can undo it, because nothing else in the system should.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from dataagent.catalog.browse import Catalog
from dataagent.connectors.base import ResultFrame
from dataagent.dal.validator import ColumnRef, Projection

__all__ = ["MaskStyle", "mask_frame", "mask_value", "styles_for"]

MASK_AUTO = "auto"
MASK_FULL = "full"
MASK_EMAIL = "email"
MASK_PHONE = "phone"
MASK_LAST4 = "last4"
MASK_HASH = "hash"

#: What a masked cell says when nothing about its shape is worth keeping.
REDACTED = "***"

#: Enough digits to be a phone number rather than a house number or a year.
_PHONE_DIGITS = 7

MaskStyle = str


@dataclass(frozen=True, slots=True)
class MaskedFrame:
    """A result whose sensitive cells have been replaced, and the record of it."""

    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    truncated: bool
    duration_ms: int
    #: Output column names that were masked, for the audit row and for a UI that
    #: should say so rather than let a person think `a***@d***.com` is the data.
    masked_columns: tuple[str, ...]

    @property
    def row_count(self) -> int:
        return len(self.rows)


def styles_for(catalog: Catalog) -> dict[ColumnRef, MaskStyle]:
    """The mask each masked column asks for, by name.

    Built from the catalog the validator already loaded, so this adds no read.
    """
    styles: dict[ColumnRef, MaskStyle] = {}
    for table in catalog.tables:
        for column in table.columns:
            if column.policy != "mask":
                continue
            ref = ColumnRef(table.schema_name, table.table_name, column.name)
            styles[ref] = column.mask_type or MASK_AUTO
    return styles


def mask_value(value: object, style: MaskStyle) -> object:
    """One cell. Anything that is not text is redacted rather than reshaped.

    A number, a date or a binary blob has no format worth preserving and every
    attempt to preserve one leaks part of it — a masked salary of ``9****`` says
    the salary has five digits.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return REDACTED
    if not value:
        return value

    if style == MASK_AUTO:
        return _mask_auto(value)
    if style == MASK_EMAIL:
        return _mask_email(value)
    if style == MASK_PHONE:
        return _mask_phone(value)
    if style == MASK_LAST4:
        return f"{REDACTED}{value[-4:]}" if len(value) > 4 else REDACTED
    if style == MASK_HASH:
        return f"sha256:{hashlib.sha256(value.encode()).hexdigest()[:12]}"
    return REDACTED


def mask_frame(
    frame: ResultFrame,
    projections: tuple[Projection, ...],
    styles: dict[ColumnRef, MaskStyle],
) -> MaskedFrame:
    """Apply the column policies to a result, by position.

    Falls back to masking *everything* when the projection list and the result
    disagree about how many columns there are. That should not happen — the
    canonical SQL names its projections — but the safe answer to "which cell was
    the address in?" when the answer is unknown is "assume all of them".
    """
    if len(projections) != len(frame.columns):
        return _mask_everything(frame)

    sensitive = [
        (index, _style(projection, styles))
        for index, projection in enumerate(projections)
        if projection.sensitive
    ]
    if not sensitive:
        return MaskedFrame(
            columns=frame.columns,
            rows=frame.rows,
            truncated=frame.truncated,
            duration_ms=frame.duration_ms,
            masked_columns=(),
        )

    rows: list[tuple[object, ...]] = []
    for row in frame.rows:
        values = list(row)
        for index, style in sensitive:
            values[index] = mask_value(values[index], style)
        rows.append(tuple(values))

    return MaskedFrame(
        columns=frame.columns,
        rows=tuple(rows),
        truncated=frame.truncated,
        duration_ms=frame.duration_ms,
        masked_columns=tuple(frame.columns[index] for index, _ in sensitive),
    )


def _style(projection: Projection, styles: dict[ColumnRef, MaskStyle]) -> MaskStyle:
    # A derived value — UPPER(email), a concatenation — has no shape left to
    # preserve, so it is redacted whatever the column's own style is.
    if projection.source is None:
        return MASK_FULL
    return styles.get(projection.source, MASK_FULL)


def _mask_everything(frame: ResultFrame) -> MaskedFrame:
    return MaskedFrame(
        columns=frame.columns,
        rows=tuple(tuple(REDACTED for _ in row) for row in frame.rows),
        truncated=frame.truncated,
        duration_ms=frame.duration_ms,
        masked_columns=frame.columns,
    )


def _mask_auto(value: str) -> str:
    """Keep the shape the value actually has, rather than one it was declared to.

    Deciding from the value is not weaker than deciding from a column name — the
    mask is applied either way, and this one cannot be wrong about a column whose
    contents are not what its name suggests.
    """
    if "@" in value:
        return _mask_email(value)
    if sum(character.isdigit() for character in value) >= _PHONE_DIGITS:
        return _mask_phone(value)
    return REDACTED


def _mask_email(value: str) -> str:
    local, separator, domain = value.partition("@")
    if not separator:
        return REDACTED
    name, _, extension = domain.rpartition(".")
    domain_hint = f"{name[:1]}***.{extension}" if name else REDACTED
    return f"{local[:1]}***@{domain_hint}"


def _mask_phone(value: str) -> str:
    digits = [character for character in value if character.isdigit()]
    return f"{REDACTED}{''.join(digits[-4:])}" if len(digits) >= 4 else REDACTED
