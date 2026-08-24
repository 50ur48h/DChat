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
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol, cast, runtime_checkable

from dataagent.config import Settings, get_settings
from dataagent.dal.masking import MaskedFrame

__all__ = [
    "ArtifactStore",
    "BlobArtifactStore",
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


class BlobArtifactStore:
    """Azure Blob Storage. The deployed answer (WP12.2, architecture 6.4).

    The same key scheme as the local store — ``{org_id}/{execution_id}.json`` —
    so the tenant prefix is a property of the reference rather than of the
    backend, and the isolation check below is the same check for the same reason.
    Blob names are opaque to the service, so no translation is needed here; that
    is the difference from `KeyVaultSecretsProvider`, whose names are constrained.

    **Managed identity, no connection string.** The container app's user-assigned
    identity holds `Storage Blob Data Contributor` on the account, so this class
    holds no credential and there is none to leak. A connection string would have
    been fewer lines and would have put a key in the app's environment, which is
    the thing architecture 9 is built to avoid.

    **A missing blob is `None`, not an error**, exactly as the local store's
    missing file is: an expired or swept result is an ordinary outcome, and
    B-114 records what happens when the *reasons* for `None` are conflated —
    that conflation is one layer up, in `chart._frame_for`, and is not fixed here.
    """

    def __init__(
        self,
        *,
        account_url: str,
        container: str,
        client: object | None = None,
    ) -> None:
        if not account_url:
            raise StoredArtifactError(
                "ARTIFACTS_BACKEND=blob needs ARTIFACTS_ACCOUNT_URL. The deployment "
                "sets it from the storage account the Bicep created."
            )
        self._container_name = container
        if client is not None:
            self._service = client
            return
        # Deferred for the reason `secrets/keyvault.py` defers its own: the SDK
        # is in the image for the deployment's sake and costs every other process
        # an import it will never use.
        from azure.identity.aio import DefaultAzureCredential
        from azure.storage.blob.aio import BlobServiceClient

        # The credential is held rather than passed inline so `aclose` can close
        # it: closing the service client does not close a credential it was
        # handed, and both own an HTTP connection pool.
        self._credential: object = DefaultAzureCredential()
        self._service = BlobServiceClient(account_url=account_url, credential=self._credential)

    async def aclose(self) -> None:
        """Release the connection pools this store holds.

        **For short-lived processes**, which is where it matters: a long-running
        API closes them by exiting, but `dataagent.ops.selfcheck` is a one-shot
        job whose entire output is a diagnosis, and an unclosed `aiohttp` session
        prints `Unclosed client session` on the way out. Noise in the one tool
        whose job is clarity is worse than it sounds — it is two lines of
        traceback-shaped text under the sentence somebody needs to read.

        Not on the `ArtifactStore` protocol: the local store has nothing to close,
        and widening the protocol would oblige every test double to grow a method
        that does nothing. Callers that care use `getattr`.
        """
        for holder in (self._service, self._credential):
            close = getattr(holder, "close", None)
            if close is not None:
                with suppress(Exception):
                    await close()

    def _blob(self, reference: str) -> Any:
        service = cast(Any, self._service)
        return service.get_blob_client(container=self._container_name, blob=reference)

    async def put(self, *, org_id: uuid.UUID, execution_id: uuid.UUID, payload: bytes) -> str:
        reference = f"{org_id}/{execution_id}.json"
        await self._blob(reference).upload_blob(payload, overwrite=True)
        return reference

    async def get(self, *, org_id: uuid.UUID, reference: str) -> bytes | None:
        from azure.core.exceptions import ResourceNotFoundError

        self._check_tenant(org_id, reference)
        try:
            stream = await self._blob(reference).download_blob()
            return cast(bytes, await stream.readall())
        except ResourceNotFoundError:
            return None

    @staticmethod
    def _check_tenant(org_id: uuid.UUID, reference: str) -> None:
        """Refuse a reference belonging to another organization.

        The same guard `LocalArtifactStore._resolve` applies, and it earns its
        place here for a different reason: a blob name is not a path, so there is
        no `..` to climb with — but there is also no filesystem to stop a
        perfectly well-formed name from reaching another tenant's prefix.
        """
        if not reference.startswith(f"{org_id}/"):
            raise StoredArtifactError("That artifact belongs to another organization.")


def artifact_store(settings: Settings | None = None) -> ArtifactStore:
    """The store this deployment uses.

    Chosen by `ARTIFACTS_BACKEND`, the way `SECRETS_BACKEND` chooses the
    credential store. Defaults to local, so nothing about a developer's stack
    changes; a deployment sets `blob` and supplies the account URL.
    """
    resolved = settings if settings is not None else get_settings()
    if resolved.artifacts_backend == "blob":
        return BlobArtifactStore(
            account_url=resolved.artifacts_account_url or "",
            container=resolved.artifacts_container,
        )
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
