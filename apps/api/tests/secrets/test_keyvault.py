"""The Key Vault backend, and the translation that must not collide.

Two things are worth testing here and they are not the same thing. The provider's
behaviour — store, fetch, delete, and what a missing secret does — is ordinary.
The **name translation is not ordinary**, because a lossy one would let two data
sources resolve to a single secret, and the failure that produces is one customer
holding another's database credential. That is why `secret_name_for` refuses
characters it could technically fold, and why most of this file is about refusal.

The client is a fake rather than a mock of the SDK's surface: what is under test
is this module's logic, and a fake that stores bytes in a dict cannot drift from
an SDK signature the way a hand-written mock silently can.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from dataagent.config import Settings
from dataagent.secrets.base import InvalidSecretRefError, SecretNotFoundError
from dataagent.secrets.factory import build_secrets_provider
from dataagent.secrets.keyvault import KeyVaultSecretsProvider, secret_name_for

ORG = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
SRC = "9f1c7b2e-2b6d-4f0a-8a1e-6d2f5b3c4d5e"
REF = f"ds/{ORG}/{SRC}/credentials"


class _Secret:
    def __init__(self, value: str | None) -> None:
        self.value = value


class FakeSecretClient:
    """Enough of `azure.keyvault.secrets.aio.SecretClient` to exercise this module."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.set_calls: list[tuple[str, str]] = []
        self.closed = False

    async def set_secret(self, name: str, value: str) -> _Secret:
        self.set_calls.append((name, value))
        self.store[name] = value
        return _Secret(value)

    async def get_secret(self, name: str) -> _Secret:
        from azure.core.exceptions import ResourceNotFoundError

        if name not in self.store:
            raise ResourceNotFoundError(f"no such secret: {name}")
        return _Secret(self.store[name])

    async def delete_secret(self, name: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError

        if name not in self.store:
            raise ResourceNotFoundError(f"no such secret: {name}")
        del self.store[name]

    async def close(self) -> None:
        self.closed = True


def _provider(client: FakeSecretClient) -> KeyVaultSecretsProvider:
    return KeyVaultSecretsProvider(
        vault_url="https://kv-test.vault.azure.net/",
        client=client,  # pyright: ignore[reportArgumentType]
    )


# --------------------------------------------------------------------------
# The translation. Most of the risk in this module lives here.
# --------------------------------------------------------------------------


def test_a_reference_becomes_a_legible_vault_name() -> None:
    """WP12.2's acceptance is that `az keyvault secret list` is recognisable."""
    assert secret_name_for(REF) == f"ds-{ORG}-{SRC}-credentials"


@pytest.mark.parametrize("bad", [f"ds/{ORG}/some.source/credentials", f"ds/{ORG}/a_b/credentials"])
def test_characters_that_would_collide_are_refused_not_folded(bad: str) -> None:
    """The point of the whole module.

    `.` and `_` are legal in a reference and illegal in a vault name. Folding
    them onto `-` would map `a.b`, `a/b` and `a_b` to one secret — two customers'
    credentials at one address. Refusing is the only safe direction.
    """
    with pytest.raises(InvalidSecretRefError, match="share one secret"):
        secret_name_for(bad)


def test_the_fold_that_is_refused_would_really_have_collided() -> None:
    """Asserts the hazard, not just the guard.

    Without this, the test above passes just as well against a rule that refuses
    for no reason. These two references are distinct and their naive translations
    are identical.
    """
    a, b = f"ds/{ORG}/a.b/credentials", f"ds/{ORG}/a/b/credentials"
    assert a != b
    assert a.replace("/", "-").replace(".", "-") == b.replace("/", "-").replace(".", "-")


def test_a_traversing_reference_never_reaches_the_vault() -> None:
    with pytest.raises(InvalidSecretRefError):
        secret_name_for("ds/../../etc/passwd")


# --------------------------------------------------------------------------
# The three operations.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_then_get_round_trips_the_credential() -> None:
    client = FakeSecretClient()
    provider = _provider(client)

    await provider.put(REF, {"username": "pizza_readonly", "password": "s3cret"})

    assert await provider.get(REF) == {"username": "pizza_readonly", "password": "s3cret"}


@pytest.mark.asyncio
async def test_the_stored_value_is_json_under_the_translated_name() -> None:
    client = FakeSecretClient()
    await _provider(client).put(REF, {"password": "s3cret"})

    name, value = client.set_calls[0]
    assert name == f"ds-{ORG}-{SRC}-credentials"
    assert json.loads(value) == {"password": "s3cret"}


@pytest.mark.asyncio
async def test_an_unchanged_credential_serialises_identically() -> None:
    """Key Vault versions on every write, so a stable encoding is not cosmetic.

    Re-registering a source with the same credential should not add a version,
    and a dict whose keys arrive in a different order is the same credential.
    """
    client = FakeSecretClient()
    provider = _provider(client)

    await provider.put(REF, {"username": "u", "password": "p"})
    await provider.put(REF, {"password": "p", "username": "u"})

    assert client.set_calls[0][1] == client.set_calls[1][1]


@pytest.mark.asyncio
async def test_a_missing_secret_is_not_found_rather_than_an_sdk_error() -> None:
    with pytest.raises(SecretNotFoundError):
        await _provider(FakeSecretClient()).get(REF)


@pytest.mark.asyncio
async def test_a_secret_with_no_value_is_also_not_found() -> None:
    """A row/store disagreement, which is the same thing to the caller."""
    client = FakeSecretClient()
    client.store[f"ds-{ORG}-{SRC}-credentials"] = ""
    with pytest.raises(SecretNotFoundError):
        await _provider(client).get(REF)


@pytest.mark.asyncio
async def test_unreadable_json_is_not_found_rather_than_a_crash() -> None:
    client = FakeSecretClient()
    client.store[f"ds-{ORG}-{SRC}-credentials"] = "not json"
    with pytest.raises(SecretNotFoundError, match="not readable JSON"):
        await _provider(client).get(REF)


@pytest.mark.asyncio
async def test_deleting_something_that_is_not_there_is_success() -> None:
    """The interface requires it: callers reach delete cleaning up after a
    data source that is already gone, and a raise leaves them nothing to do."""
    await _provider(FakeSecretClient()).delete(REF)


@pytest.mark.asyncio
async def test_delete_removes_it() -> None:
    client = FakeSecretClient()
    provider = _provider(client)
    await provider.put(REF, {"password": "p"})

    await provider.delete(REF)

    assert client.store == {}


def test_a_vault_url_is_required() -> None:
    """Without one there is nowhere to put credentials, and the API must say so
    rather than construct a client that fails later on a request path."""
    with pytest.raises(RuntimeError, match="KEY_VAULT_URL"):
        KeyVaultSecretsProvider(vault_url="")


# --------------------------------------------------------------------------
# Reachability: the factory really selects this backend (CLAUDE.md's rule).
# --------------------------------------------------------------------------


def test_the_factory_builds_this_provider_when_the_deployment_asks_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live path, not the constructor.

    A provider that works when a test instantiates it directly and is never
    chosen by `build_secrets_provider` is the defect CLAUDE.md names — B-083,
    B-100, B-109. This asserts the selection, so the branch cannot rot.
    """
    built: dict[str, Any] = {}

    class _Recorder(KeyVaultSecretsProvider):
        def __init__(self, *, vault_url: str, client: object | None = None) -> None:
            built["vault_url"] = vault_url
            super().__init__(
                vault_url=vault_url,
                client=FakeSecretClient(),  # pyright: ignore[reportArgumentType]
            )

    monkeypatch.setattr("dataagent.secrets.keyvault.KeyVaultSecretsProvider", _Recorder)

    settings = Settings(  # pyright: ignore[reportArgumentType]
        env="prod",
        build_env="prod",
        auth_mode="entra",
        oidc_authority="https://example.invalid/v2.0",
        secrets_backend="keyvault",
        key_vault_url="https://kv-dataagent-dev-abc.vault.azure.net/",
    )
    provider = build_secrets_provider(settings)

    assert isinstance(provider, KeyVaultSecretsProvider)
    assert built["vault_url"] == "https://kv-dataagent-dev-abc.vault.azure.net/"


def test_choosing_keyvault_without_a_url_refuses_at_build_time() -> None:
    settings = Settings(  # pyright: ignore[reportArgumentType]
        env="prod",
        build_env="prod",
        auth_mode="entra",
        oidc_authority="https://example.invalid/v2.0",
        secrets_backend="keyvault",
    )
    with pytest.raises(RuntimeError, match="KEY_VAULT_URL"):
        build_secrets_provider(settings)


def test_production_still_refuses_the_local_backend() -> None:
    """WP12.2 gives production somewhere to go; it does not soften D-001."""
    settings = Settings(  # pyright: ignore[reportArgumentType]
        env="prod",
        build_env="prod",
        auth_mode="entra",
        oidc_authority="https://example.invalid/v2.0",
        secrets_backend="local",
        local_secrets_key=SecretStr(Fernet.generate_key().decode()),
    )
    with pytest.raises(RuntimeError, match="not permitted in a production"):
        build_secrets_provider(settings)
