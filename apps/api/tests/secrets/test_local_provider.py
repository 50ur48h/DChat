"""The local secrets backend, tested for what it must never do.

A round trip is the easy half. The half that matters is on disk: a file that a
developer, a backup job or a stray `docker cp` might see, and that must reveal
nothing about the credential it holds.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from dataagent.secrets.base import InvalidSecretRefError, SecretNotFoundError
from dataagent.secrets.local import (
    FILE_VERSION,
    InvalidSecretsKeyError,
    LocalSecretsProvider,
    SecretDecryptionError,
)

REF = "ds/6f8f0f7a-0000-4000-8000-000000000001/9c1f/credentials"
CREDENTIAL = {"username": "pizza_readonly", "password": "correct-horse-battery-staple"}
ROTATED = "rotated-and-different"


def _provider(path: Path, key: str | None = None) -> LocalSecretsProvider:
    return LocalSecretsProvider(
        key=key or Fernet.generate_key().decode(), path=path / "secrets.json"
    )


async def test_a_credential_survives_a_round_trip(tmp_path: Path) -> None:
    provider = _provider(tmp_path)

    await provider.put(REF, CREDENTIAL)

    assert await provider.get(REF) == CREDENTIAL


async def test_nothing_readable_reaches_the_file(tmp_path: Path) -> None:
    """The point of the backend. Neither half of the credential is on disk."""
    provider = _provider(tmp_path)
    await provider.put(REF, CREDENTIAL)

    on_disk = provider.path.read_text(encoding="utf-8")

    assert CREDENTIAL["password"] not in on_disk
    assert CREDENTIAL["username"] not in on_disk
    # The reference is not a secret and is expected to be there: it is what makes
    # an orphaned entry identifiable.
    assert REF in on_disk


async def test_the_file_is_a_versioned_document(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    await provider.put(REF, CREDENTIAL)

    document = json.loads(provider.path.read_text(encoding="utf-8"))

    assert document["version"] == FILE_VERSION
    assert list(document["secrets"]) == [REF]


async def test_putting_again_replaces_rather_than_appends(tmp_path: Path) -> None:
    """Credential rotation is a put, and the old value must not linger."""
    provider = _provider(tmp_path)
    await provider.put(REF, CREDENTIAL)

    await provider.put(REF, {"username": "pizza_readonly", "password": ROTATED})

    assert (await provider.get(REF))["password"] == ROTATED
    assert CREDENTIAL["password"] not in provider.path.read_text(encoding="utf-8")


async def test_a_missing_secret_is_a_named_failure(tmp_path: Path) -> None:
    provider = _provider(tmp_path)

    with pytest.raises(SecretNotFoundError):
        await provider.get(REF)


async def test_delete_removes_it_and_is_idempotent(tmp_path: Path) -> None:
    """Callers reach delete while cleaning up after a row that is already gone."""
    provider = _provider(tmp_path)
    await provider.put(REF, CREDENTIAL)

    await provider.delete(REF)
    await provider.delete(REF)

    with pytest.raises(SecretNotFoundError):
        await provider.get(REF)


async def test_another_key_cannot_read_what_this_one_wrote(tmp_path: Path) -> None:
    """Fernet authenticates as well as encrypts: the wrong key is a refusal."""
    await _provider(tmp_path).put(REF, CREDENTIAL)

    stranger = _provider(tmp_path)

    with pytest.raises(SecretDecryptionError):
        await stranger.get(REF)


async def test_a_tampered_value_is_refused_rather_than_trusted(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    await provider.put(REF, CREDENTIAL)

    document = json.loads(provider.path.read_text(encoding="utf-8"))
    token: str = document["secrets"][REF]
    document["secrets"][REF] = token[:-6] + "AAAAAA"
    provider.path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(SecretDecryptionError):
        await provider.get(REF)


async def test_a_file_from_the_future_is_not_half_understood(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    provider.path.write_text(json.dumps({"version": 99, "secrets": {}}), encoding="utf-8")

    with pytest.raises(SecretDecryptionError, match="version"):
        await provider.get(REF)


def test_a_key_that_is_not_a_key_fails_at_construction(tmp_path: Path) -> None:
    """Not at first use, in the middle of registering a data source."""
    with pytest.raises(InvalidSecretsKeyError, match="44"):
        LocalSecretsProvider(key="obviously-not-a-fernet-key", path=tmp_path / "secrets.json")


@pytest.mark.parametrize(
    "bad_ref",
    [
        "ds/../../etc/passwd",
        "/absolute/path",
        "ds/{org}/creds",
        "ds/org id/creds",
        "",
    ],
)
async def test_a_reference_that_could_escape_is_refused(tmp_path: Path, bad_ref: str) -> None:
    """A file backend turns a reference into a path. This is where that stops."""
    provider = _provider(tmp_path)

    with pytest.raises(InvalidSecretRefError):
        await provider.put(bad_ref, CREDENTIAL)


async def test_concurrent_writes_do_not_lose_entries(tmp_path: Path) -> None:
    """Read-modify-write on one file, from an async server. Every entry survives."""
    provider = _provider(tmp_path)
    refs = [f"ds/org/{index}/credentials" for index in range(25)]

    await asyncio.gather(*(provider.put(ref, {"password": ref}) for ref in refs))

    for ref in refs:
        assert (await provider.get(ref))["password"] == ref


async def test_the_directory_is_created_on_first_use(tmp_path: Path) -> None:
    provider = LocalSecretsProvider(
        key=Fernet.generate_key().decode(), path=tmp_path / "nested" / "deeper" / "secrets.json"
    )

    await provider.put(REF, CREDENTIAL)

    assert provider.path.is_file()
