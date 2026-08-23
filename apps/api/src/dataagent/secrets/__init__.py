"""Credential storage, behind one interface (architecture Part 7.3).

Customer database credentials never live in the platform database. They go to a
``SecretsProvider``; the platform database holds only a ``secret_ref`` pointing at
them. Locally that provider is a Fernet-encrypted file (DECISIONS D-001); from
WP12.2 it is Azure Key Vault reached through a managed identity, behind this same
interface.

The name of this package deliberately mirrors the standard library's ``secrets``.
Python 3 resolves ``import secrets`` absolutely, so modules elsewhere in the
package still get the standard library one — as ``invitations.service`` does when
it mints a token.
"""

from __future__ import annotations

from dataagent.secrets.base import (
    InvalidSecretRefError,
    SecretNotFoundError,
    SecretsProvider,
    validate_ref,
)
from dataagent.secrets.factory import build_secrets_provider, get_secrets_provider
from dataagent.secrets.keyvault import KeyVaultSecretsProvider, secret_name_for
from dataagent.secrets.local import LocalSecretsProvider, SecretDecryptionError

__all__ = [
    "InvalidSecretRefError",
    "KeyVaultSecretsProvider",
    "LocalSecretsProvider",
    "SecretDecryptionError",
    "SecretNotFoundError",
    "SecretsProvider",
    "build_secrets_provider",
    "get_secrets_provider",
    "secret_name_for",
    "validate_ref",
]
