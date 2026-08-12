"""The development backend: one Fernet-encrypted file (DECISIONS D-001).

Every value is encrypted individually with a key that lives outside the file, so
the file itself carries no plaintext. What it does reveal is which references
exist — org and data-source identifiers — which is why it is gitignored and why
the factory refuses to build this backend in production at all.

Fernet is AES-128-CBC with an HMAC-SHA256 authentication tag and a timestamp,
which means a tampered ciphertext fails to decrypt rather than decrypting to
something else. Key rotation is a re-registration of the data source, exactly as
architecture Part 7.3 describes.

All file work happens in a worker thread: this process is async end to end, and a
synchronous read on the event loop would stall every other request. The lock
around it makes read-modify-write atomic between coroutines, and the write itself
is a temp file plus ``os.replace``, so a crash mid-write cannot truncate the store.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from cryptography.fernet import Fernet, InvalidToken

from dataagent.secrets.base import SecretNotFoundError, validate_ref

__all__ = ["InvalidSecretsKeyError", "LocalSecretsProvider", "SecretDecryptionError"]

#: Bumped only if the on-disk layout changes. A file from the future is refused
#: rather than half-understood.
FILE_VERSION = 1


class InvalidSecretsKeyError(ValueError):
    """``LOCAL_SECRETS_KEY`` is not a Fernet key."""


class SecretDecryptionError(RuntimeError):
    """The stored ciphertext will not open with the configured key.

    Almost always a rotated or mistyped ``LOCAL_SECRETS_KEY``; the alternative is
    a tampered file, which Fernet's authentication tag turns into this same
    refusal rather than into a plausible-looking value.
    """


class LocalSecretsProvider:
    """A ``SecretsProvider`` backed by an encrypted JSON file."""

    def __init__(self, *, key: str, path: Path) -> None:
        try:
            self._fernet = Fernet(key)
        except (ValueError, TypeError) as error:
            # The message says nothing about the key's content beyond its length,
            # which is what makes the common mistake (a truncated paste) findable.
            raise InvalidSecretsKeyError(
                "LOCAL_SECRETS_KEY is not a valid Fernet key (got "
                f"{len(key)} characters; a key is 44). Generate one with "
                "`make secrets.key`."
            ) from error
        self._path = path
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        """Where this provider keeps its file. For diagnostics and tests."""
        return self._path

    # -- the protocol ------------------------------------------------------

    async def put(self, secret_ref: str, value: Mapping[str, str]) -> None:
        ref = validate_ref(secret_ref)
        token = self._fernet.encrypt(json.dumps(dict(value), sort_keys=True).encode()).decode()
        async with self._lock:
            await asyncio.to_thread(self._store, ref, token)

    async def get(self, secret_ref: str) -> dict[str, str]:
        ref = validate_ref(secret_ref)
        async with self._lock:
            entries = await asyncio.to_thread(self._read)

        token = entries.get(ref)
        if token is None:
            raise SecretNotFoundError(f"No secret is stored under {ref!r}")

        try:
            plaintext = self._fernet.decrypt(token.encode())
        except InvalidToken as error:
            raise SecretDecryptionError(
                f"The secret stored under {ref!r} cannot be decrypted with the "
                "configured LOCAL_SECRETS_KEY. Re-register the data source to "
                "store its credentials again under the current key."
            ) from error

        return _as_credential(json.loads(plaintext), ref)

    async def delete(self, secret_ref: str) -> None:
        ref = validate_ref(secret_ref)
        async with self._lock:
            await asyncio.to_thread(self._store, ref, None)

    # -- file handling, always on a worker thread --------------------------

    def _read(self) -> dict[str, str]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            # No file yet is the normal state of a fresh checkout, not an error.
            return {}

        document = _as_object(json.loads(raw))
        if document is None:
            raise SecretDecryptionError(f"{self._path} is not a secrets file")

        version = document.get("version")
        if version != FILE_VERSION:
            raise SecretDecryptionError(
                f"{self._path} is version {version!r}; this build understands "
                f"version {FILE_VERSION}"
            )

        entries = _as_object(document.get("secrets"))
        if entries is None:
            raise SecretDecryptionError(f"{self._path} has no secrets object")

        return {ref: str(token) for ref, token in entries.items()}

    def _store(self, ref: str, token: str | None) -> None:
        """Read, change one entry, write back. One thread hop, so no interleaving."""
        entries = self._read()
        if token is None:
            entries.pop(ref, None)
        else:
            entries[ref] = token
        self._write(entries)

    def _write(self, entries: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        _restrict(self._path.parent, 0o700)

        document = json.dumps(
            {"version": FILE_VERSION, "secrets": entries}, indent=2, sort_keys=True
        )
        # Unique per process: two developers' processes writing the same file
        # would otherwise race on one temp name and lose an entry.
        temp = self._path.with_name(f"{self._path.name}.{os.getpid()}.tmp")
        temp.write_text(document + "\n", encoding="utf-8")
        _restrict(temp, 0o600)
        # Atomic on POSIX and on Windows: the file is never half-written.
        os.replace(temp, self._path)


def _restrict(target: Path, mode: int) -> None:
    """Best-effort permissions. Windows ignores most of this, POSIX does not."""
    with contextlib.suppress(OSError):
        target.chmod(mode)


def _as_object(value: object) -> dict[str, object] | None:
    """Narrow a decoded JSON value to an object, or None if it is not one.

    JSON keys are always strings; the type system cannot know that from
    ``json.loads``, and one cast here beats a suppression at every use site.
    """
    if not isinstance(value, dict):
        return None
    return cast("dict[str, object]", value)


def _as_credential(payload: object, ref: str) -> dict[str, str]:
    fields = _as_object(payload)
    if fields is None:
        raise SecretDecryptionError(f"The secret stored under {ref!r} is not a credential")
    return {field: str(value) for field, value in fields.items()}
