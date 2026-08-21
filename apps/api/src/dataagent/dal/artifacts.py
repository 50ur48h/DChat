"""Where a result is kept (architecture Part 7.6, 10.1).

A result has two homes and the split is deliberate. The **platform database**
keeps the shape and a sample — enough to show a person what came back and to let
the critic look at it — and the **artifact store** keeps the whole thing, which
can be large and is of no interest to any query.

The store is an interface with one local implementation, the same arrangement
``SecretsProvider`` uses (D-001): files under a directory now, Blob in Phase 12,
and nothing above this line changes when that happens. Two properties hold in
both:

* **Everything written here is already masked.** The rows arrive from
  ``dal/masking.py``; there is no unmasked copy for a later bug to find.
* **Keys are org-prefixed** — ``{org_id}/{execution_id}.json`` — so a listing is
  scoped by construction rather than by a filter someone must remember, which is
  the same rule the Blob container follows (architecture Part 6.4).

Retention is a column on the row, not a habit of a cleanup script: the sweep can
be late, or absent, without the promise being wrong about what it is.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from dataagent.config import Settings, get_settings
from dataagent.dal.masking import MaskedFrame

__all__ = [
    "ArtifactStore",
    "LocalArtifactStore",
    "StoredArtifactError",
    "artifact_store",
    "encode",
    "expires_at",
    "summarize",
]


class StoredArtifactError(Exception):
    """The result could not be stored. Never raised at the caller of a query:
    losing the copy of an answer must not lose the answer."""


@runtime_checkable
class ArtifactStore(Protocol):
    """One result, put somewhere it can be read back."""

    async def put(self, *, org_id: uuid.UUID, execution_id: uuid.UUID, payload: bytes) -> str:
        """Write, and return the reference that finds it again."""
        ...

    async def get(self, *, org_id: uuid.UUID, reference: str) -> bytes | None:
        """Read it back, or None if it is gone — expired, swept, never written."""
        ...


class LocalArtifactStore:
    """Files under a directory. The development and single-node answer.

    Refuses a reference that does not begin with the caller's own organization,
    exactly as the local secrets backend refuses a mismatched prefix: a store
    whose keys carry the tenant is only isolated if something checks them.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    async def put(self, *, org_id: uuid.UUID, execution_id: uuid.UUID, payload: bytes) -> str:
        reference = f"{org_id}/{execution_id}.json"
        destination = self._resolve(org_id, reference)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        return reference

    async def get(self, *, org_id: uuid.UUID, reference: str) -> bytes | None:
        path = self._resolve(org_id, reference)
        return path.read_bytes() if path.is_file() else None

    def _resolve(self, org_id: uuid.UUID, reference: str) -> Path:
        prefix = f"{org_id}/"
        if not reference.startswith(prefix):
            raise StoredArtifactError("That artifact belongs to another organization.")
        # Resolved and re-checked, so a reference carrying `..` cannot climb out
        # of the root even though the prefix looks right.
        candidate = (self._root / reference).resolve()
        root = self._root.resolve()
        if not candidate.is_relative_to(root / str(org_id)):
            raise StoredArtifactError("That artifact reference is not a valid one.")
        return candidate


def artifact_store(settings: Settings | None = None) -> ArtifactStore:
    """The store this deployment uses. One line to change in Phase 12."""
    resolved = settings if settings is not None else get_settings()
    return LocalArtifactStore(resolved.artifacts_path)


def summarize(frame: MaskedFrame) -> dict[str, object]:
    """The shape of a result, with none of its values.

    Safe to store, safe to show, and safe to put in a prompt — which is the
    point: the agent reasons about a summary and reaches for rows only when it
    has a reason to.
    """
    return {
        "columns": list(frame.columns),
        "row_count": frame.row_count,
        "truncated": frame.truncated,
        "masked_columns": list(frame.masked_columns),
        "duration_ms": frame.duration_ms,
    }


def expires_at(settings: Settings | None = None, *, now: datetime | None = None) -> datetime:
    resolved = settings if settings is not None else get_settings()
    moment = now if now is not None else datetime.now(UTC)
    return moment + timedelta(days=resolved.artifact_retention_days)


#: The version of this artifact's envelope. Bumped when a reader has to be able
#: to tell what a writer knew. **1** is everything written before column types
#: were recorded (**B-103**); **2** carries `column_types`.
ARTIFACT_FORMAT = 2

#: What a column holds, recorded by the writer that still has the Python objects
#: rather than inferred later from JSON text (**B-103**). Closed, and small on
#: purpose: this exists to tell a consumer whether arithmetic is meaningful, not
#: to describe a type system.
ColumnType = Literal["number", "temporal", "text"]


def column_type(values: Iterable[object]) -> ColumnType:
    """What this column holds, from the values as the driver returned them.

    **Recorded, not guessed.** This runs where a `Decimal` is still a `Decimal`,
    so calling the column a number is reporting a fact. The alternative — asking
    a consumer to look at `"119558.51"` and decide — is guessing, and the guess
    is wrong for an account number, a postcode or a product code that happens to
    be all digits. That guess would draw a chart that is *wrong* rather than
    absent, which is the case `charts._kind`'s judge-the-values rule exists to
    prevent and is why B-103 was not fixed downstream.

    Nulls are skipped: a column that is half empty is still whatever its present
    values are. An all-null column is `text`, because nothing is known and
    claiming otherwise would be the same guess in the other direction.
    """
    present = [value for value in values if value is not None]
    if not present:
        return "text"
    if all(
        isinstance(value, int | float | Decimal) and not isinstance(value, bool)
        for value in present
    ):
        return "number"
    if all(isinstance(value, date | datetime) for value in present):
        return "temporal"
    return "text"


def encode(frame: MaskedFrame) -> bytes:
    """The whole (masked) result, as JSON.

    JSON rather than parquet for now: the results this holds are capped at a
    thousand rows, nothing reads them analytically yet, and a format that can be
    opened in any text editor is worth more during a security review than one
    that saves bytes nobody is short of. Parquet arrives with the first caller
    that wants a dataframe.

    **`column_types` is what makes the values usable again** (**B-103**). Every
    value here is JSON-safe, which for a `Decimal` means a string — see
    `encodable` — and a reader given only `"119558.51"` cannot tell money from a
    postcode. So the writer records what it knew. Carrying the digits as a JSON
    *number* instead was the obvious alternative and is not available honestly:
    the standard library cannot emit a `Decimal` unquoted without injecting raw
    text into the stream, and `float` would round a customer's money. A string
    with a declared type round-trips exactly, at any size and any precision.
    """
    return json.dumps(
        {
            "format": ARTIFACT_FORMAT,
            "columns": list(frame.columns),
            "column_types": [
                column_type(row[index] for row in frame.rows if index < len(row))
                for index in range(len(frame.columns))
            ],
            "rows": [[encodable(value) for value in row] for row in frame.rows],
            "truncated": frame.truncated,
            "masked_columns": list(frame.masked_columns),
        },
        separators=(",", ":"),
    ).encode()


def encodable(value: object) -> object:
    """Whatever a driver handed back, as something JSON can hold.

    Dates, decimals and UUIDs all arrive as their own types from one engine or
    the other, and `str` is the honest fallback: this copy exists to be read, and
    what is needed to compute with it is the column's type, which `encode`
    records beside it.

    **Public, and shared** (**B-103**). Three places used to convert values at
    this seam with three private copies of the same rule — the artifact blob, the
    `sample_rows` column, and the rows the composing model is shown. Only the
    chart computed on them, so only the chart broke, but three copies of a
    decision is three places for the next one to diverge.
    """
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)
