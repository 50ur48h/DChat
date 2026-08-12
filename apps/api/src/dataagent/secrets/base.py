"""The provider protocol and the shape of a reference.

Three operations, no more: ``put``, ``get``, ``delete``. There is deliberately no
``list`` and no ``search`` — nothing in the product needs to enumerate the
credentials it holds, and an interface that cannot enumerate them is one an
attacker cannot use to enumerate them either.

A secret **reference** is not itself a secret. It is a path-like name built by the
application from identifiers it already knows (``ds/{org_id}/{ds_id}/credentials``)
and it is safe to store, log and return. It is validated anyway: a reference
becomes a key, a file path or a Key Vault name depending on the backend, and the
one place to refuse ``..`` is here rather than three times over.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Protocol

__all__ = [
    "InvalidSecretRefError",
    "SecretNotFoundError",
    "SecretsProvider",
    "validate_ref",
]

#: Conservative on purpose: letters, digits and the three separators a reference
#: needs. Everything the application builds fits comfortably inside it.
_VALID_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")


class SecretNotFoundError(LookupError):
    """Nothing is stored under that reference.

    Distinct from a decryption failure: this one means the row and the store
    disagree, which is a data problem, not a key problem.
    """


class InvalidSecretRefError(ValueError):
    """A reference that no backend will be asked to interpret."""


def validate_ref(secret_ref: str) -> str:
    """Return the reference, or refuse it.

    ``..`` is rejected explicitly rather than left to the pattern: a file backend
    turns a reference into a path, and ``ds/../../etc/passwd`` is made of
    characters the pattern otherwise allows.
    """
    if ".." in secret_ref or _VALID_REF.match(secret_ref) is None:
        raise InvalidSecretRefError(f"Not a usable secret reference: {secret_ref!r}")
    return secret_ref


class SecretsProvider(Protocol):
    """Where credentials live. Implementations must never log a value."""

    async def put(self, secret_ref: str, value: Mapping[str, str]) -> None:
        """Store (or replace) the credential under ``secret_ref``."""
        ...

    async def get(self, secret_ref: str) -> dict[str, str]:
        """Fetch the credential, or raise ``SecretNotFoundError``."""
        ...

    async def delete(self, secret_ref: str) -> None:
        """Remove the credential. Idempotent: deleting nothing is a success.

        Callers reach this while cleaning up after a data source that is already
        gone, and a second failure there would leave the caller with nothing
        useful to do about it.
        """
        ...
