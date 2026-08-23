"""The deployed backend: Azure Key Vault, reached by managed identity (WP12.2).

The other half of DECISIONS **D-001**. `LocalSecretsProvider` keeps customer
credentials in a Fernet-encrypted file whose key sits in an environment variable
beside it — fine on a laptop, and refused outright in a production build. This is
what production uses instead, behind the same three-method interface, so nothing
above `SecretsProvider` changes.

**No credential of its own, which is the point.** `DefaultAzureCredential` asks
the platform who this process is: the user-assigned managed identity the Bicep
attaches to the container app, granted `Key Vault Secrets Officer` on the vault
and nothing else. That is what makes architecture 9's *zero secrets in the
pipeline* true rather than aspirational — there is no vault password to leak,
because there is no vault password.

**A reference is not a Key Vault name, and the translation refuses rather than
guesses.** References look like `ds/{org_id}/{ds_id}/credentials`; Key Vault names
allow only letters, digits and hyphens, up to 127 characters. Mapping every
separator onto `-` would be lossy — `a.b`, `a/b` and `a_b` would become one name,
and two data sources sharing a secret is the worst possible outcome for this
module. So only `/` is translated, and a reference containing `.` or `_` is
**refused**. Nothing in this product builds one: `validate_ref` permits those
characters for the file backend's sake, and this backend is stricter than the
interface it implements, which is the safe direction to differ in.

The resulting name is legible on purpose —
`ds-{org_id}-{ds_id}-credentials`, 88 characters — because WP12.2's acceptance is
that somebody can run `az keyvault secret list` and recognise what they are
looking at without any value being shown.

**Imports are deferred into the constructor.** The Azure SDK is a hard dependency
of the image but an unnecessary import cost for every local process that will
never build this provider, and a missing-extras failure should name this class
rather than surface as an ImportError at module load in an unrelated test.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from dataagent.secrets.base import (
    InvalidSecretRefError,
    SecretNotFoundError,
    validate_ref,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from azure.keyvault.secrets.aio import SecretClient

__all__ = ["KeyVaultSecretsProvider", "secret_name_for"]

#: What Key Vault accepts for a secret name: letters, digits and hyphens, and it
#: must start with a letter. Enforced here rather than discovered from a 400.
_VALID_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,126}$")


def secret_name_for(secret_ref: str) -> str:
    """The vault name for a reference, or a refusal.

    Only ``/`` is translated. ``.`` and ``_`` are refused rather than folded onto
    the same hyphen, because folding them would let two distinct references
    resolve to one secret — and the two references this product builds are for
    two different customers' databases.
    """
    validate_ref(secret_ref)
    if "." in secret_ref or "_" in secret_ref:
        raise InvalidSecretRefError(
            f"Key Vault names cannot carry '.' or '_', and folding them onto '-' "
            f"would let two references share one secret: {secret_ref!r}"
        )
    name = secret_ref.replace("/", "-")
    if _VALID_NAME.match(name) is None:
        raise InvalidSecretRefError(
            f"{secret_ref!r} does not translate to a usable Key Vault name ({name!r})"
        )
    return name


class KeyVaultSecretsProvider:
    """A ``SecretsProvider`` backed by one Key Vault.

    One vault per deployment, one secret per data source. The value is the same
    JSON object the local backend encrypts, so a credential written by either
    backend has the same shape when read back.
    """

    def __init__(self, *, vault_url: str, client: SecretClient | None = None) -> None:
        if not vault_url:
            raise RuntimeError(
                "SECRETS_BACKEND=keyvault needs KEY_VAULT_URL. The deployment sets it "
                "from the vault the Bicep created; without it there is nowhere to "
                "store customer credentials and the API must not pretend otherwise."
            )
        self._vault_url = vault_url
        if client is not None:
            self._client = client
            return
        # Deferred: see the module docstring. A local process that never builds
        # this provider should not pay for the SDK's import.
        from azure.identity.aio import DefaultAzureCredential
        from azure.keyvault.secrets.aio import SecretClient as _SecretClient

        self._client = _SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())

    async def put(self, secret_ref: str, value: Mapping[str, str]) -> None:
        """Store (or replace) the credential.

        Key Vault versions rather than overwrites, so a rotation leaves the
        previous value recoverable for the vault's retention period. That is a
        property worth having and worth knowing about: *deleting* a data source
        must therefore delete the secret, not merely write an empty one.
        """
        name = secret_name_for(secret_ref)
        # `sort_keys` so an unchanged credential serialises identically and does
        # not create a new version on every re-registration.
        payload = json.dumps(dict(value), sort_keys=True, separators=(",", ":"))
        await self._client.set_secret(name, payload)

    async def get(self, secret_ref: str) -> dict[str, str]:
        """Fetch the credential, or raise ``SecretNotFoundError``."""
        from azure.core.exceptions import ResourceNotFoundError

        name = secret_name_for(secret_ref)
        try:
            secret = await self._client.get_secret(name)
        except ResourceNotFoundError as error:
            raise SecretNotFoundError(f"No credential stored under {secret_ref!r}") from error

        if secret.value is None:
            # A secret with no value is a row/store disagreement, not a key
            # problem — the same distinction the local backend draws.
            raise SecretNotFoundError(f"No credential stored under {secret_ref!r}")
        # The message deliberately names the reference and never the value.
        try:
            loaded = cast(dict[str, Any], json.loads(secret.value))
        except json.JSONDecodeError as error:
            raise SecretNotFoundError(
                f"The credential stored under {secret_ref!r} is not readable JSON"
            ) from error
        return {str(k): str(v) for k, v in loaded.items()}

    async def delete(self, secret_ref: str) -> None:
        """Remove the credential. Idempotent, as the interface requires.

        Callers reach this cleaning up after a data source that is already gone,
        so "it was not there" is success. Key Vault soft-deletes: the secret
        becomes unreadable immediately and is purgeable afterwards, which is what
        a retention policy is for and is not this method's business.
        """
        from azure.core.exceptions import ResourceNotFoundError

        name = secret_name_for(secret_ref)
        try:
            await self._client.delete_secret(name)
        except ResourceNotFoundError:
            return

    async def aclose(self) -> None:
        """Release the client's transport. Used by tests and by shutdown."""
        await self._client.close()
