"""Where an uploaded document's original bytes go (architecture 5.5, 9.1).

Deliberately its own store rather than a widening of `dal/artifacts.py`, and the
reason is scope of review rather than taste. That interface is inside the
security boundary — CLAUDE.md requires human review on every `dal/` change — and
its `put(org_id, execution_id, payload)` shape is a *query result's* shape.
Reaching into it so a knowledge feature can save a `.md` file would change a
signature the executor depends on, in a package whose review bar exists for
different reasons.

What is **not** duplicated is the property that matters. Both stores refuse a
reference that does not begin with the caller's own organization, and both
re-resolve the path afterwards so a reference carrying `..` cannot climb out of
the root even though its prefix looks right. That check is copied here on
purpose: a store whose keys carry the tenant is only isolated if something
checks them, and "the other store checks" is not a property this one has.

Phase 12 puts both behind Blob (`{org_id}/docs/…`, per 9.1), which is where the
two should converge — **B-072**.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Protocol

__all__ = ["DocumentStore", "LocalDocumentStore", "StoredDocumentError", "extension_for"]

#: What each accepted format is saved as. The original's extension is kept so a
#: later download hands back something a person's machine can open, and so the
#: bytes on disk are recognisable to whoever is debugging an ingest.
_EXTENSIONS = {
    "text/markdown": ".md",
    "text/plain": ".txt",
    "application/pdf": ".pdf",
}


class StoredDocumentError(Exception):
    """A document could not be written or read back."""


def extension_for(mime: str) -> str:
    return _EXTENSIONS.get(mime, ".bin")


class DocumentStore(Protocol):
    """One uploaded original, put somewhere it can be read back."""

    async def put(
        self, *, org_id: uuid.UUID, document_id: uuid.UUID, payload: bytes, mime: str
    ) -> str:
        """Write, and return the reference that finds it again."""
        ...

    async def get(self, *, org_id: uuid.UUID, reference: str) -> bytes | None:
        """Read it back, or None if it is gone."""
        ...


class LocalDocumentStore:
    """Files under a directory. The development and single-node answer."""

    def __init__(self, root: Path) -> None:
        self._root = root

    async def put(
        self, *, org_id: uuid.UUID, document_id: uuid.UUID, payload: bytes, mime: str
    ) -> str:
        reference = f"{org_id}/docs/{document_id}{extension_for(mime)}"
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
            raise StoredDocumentError("That document belongs to another organization.")
        # Resolved and re-checked, so a reference carrying `..` cannot climb out
        # of the root even though the prefix looks right.
        candidate = (self._root / reference).resolve()
        root = self._root.resolve()
        if not candidate.is_relative_to(root):
            raise StoredDocumentError("That document reference is not inside the store.")
        return candidate
