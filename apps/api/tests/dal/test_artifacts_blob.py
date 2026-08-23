"""The Blob artifact backend (WP12.2).

The local store's guarantees, asserted again against a different backend rather
than assumed to travel with the interface. Two of them matter:

* **A reference belonging to another organization is refused.** The local store
  gets this partly for free — a path that climbs out of the root fails the
  `is_relative_to` check. A blob name is not a path, so there is no filesystem to
  stop a perfectly well-formed name reaching another tenant's prefix, and the
  check has to be explicit. This file is why we know it is.
* **A missing blob is `None`, not an error**, because an expired or swept result
  is an ordinary outcome.

The client is a fake holding bytes in a dict, for the reason
`tests/secrets/test_keyvault.py` uses one: what is under test is this module's
logic, not the SDK's surface.
"""

from __future__ import annotations

import uuid

import pytest

from dataagent.config import Settings
from dataagent.dal.artifacts import (
    BlobArtifactStore,
    LocalArtifactStore,
    StoredArtifactError,
    artifact_store,
)

ORG = uuid.UUID("3fa85f64-5717-4562-b3fc-2c963f66afa6")
OTHER_ORG = uuid.UUID("11111111-2222-3333-4444-555555555555")
EXECUTION = uuid.UUID("9f1c7b2e-2b6d-4f0a-8a1e-6d2f5b3c4d5e")


class _Download:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def readall(self) -> bytes:
        return self._payload


class _BlobClient:
    def __init__(self, store: dict[str, bytes], name: str) -> None:
        self._store = store
        self._name = name

    async def upload_blob(self, payload: bytes, overwrite: bool = False) -> None:
        if self._name in self._store and not overwrite:
            raise RuntimeError("blob exists and overwrite was not asked for")
        self._store[self._name] = payload

    async def download_blob(self) -> _Download:
        from azure.core.exceptions import ResourceNotFoundError

        if self._name not in self._store:
            raise ResourceNotFoundError(f"no such blob: {self._name}")
        return _Download(self._store[self._name])


class FakeBlobService:
    """Enough of `BlobServiceClient` to exercise the store."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.containers: list[str] = []

    def get_blob_client(self, container: str, blob: str) -> _BlobClient:
        self.containers.append(container)
        return _BlobClient(self.blobs, blob)


def _store(service: FakeBlobService) -> BlobArtifactStore:
    return BlobArtifactStore(
        account_url="https://sttest.blob.core.windows.net/",
        container="artifacts",
        client=service,
    )


@pytest.mark.asyncio
async def test_put_then_get_round_trips_the_payload() -> None:
    service = FakeBlobService()
    store = _store(service)

    reference = await store.put(org_id=ORG, execution_id=EXECUTION, payload=b'{"rows":[]}')

    assert reference == f"{ORG}/{EXECUTION}.json"
    assert await store.get(org_id=ORG, reference=reference) == b'{"rows":[]}'


@pytest.mark.asyncio
async def test_the_key_carries_the_tenant_prefix() -> None:
    """Architecture 6.4: a listing is scoped by construction, not by a filter."""
    service = FakeBlobService()
    await _store(service).put(org_id=ORG, execution_id=EXECUTION, payload=b"{}")

    assert list(service.blobs) == [f"{ORG}/{EXECUTION}.json"]
    assert service.containers == ["artifacts"]


@pytest.mark.asyncio
async def test_another_organizations_reference_is_refused() -> None:
    """The guard that a blob name cannot get for free.

    `LocalArtifactStore` is protected twice — by the prefix check and by
    `is_relative_to` on the resolved path. There is no second line here, so this
    test is the whole of it.
    """
    service = FakeBlobService()
    store = _store(service)
    reference = await store.put(org_id=OTHER_ORG, execution_id=EXECUTION, payload=b"secret")

    with pytest.raises(StoredArtifactError, match="another organization"):
        await store.get(org_id=ORG, reference=reference)


@pytest.mark.asyncio
async def test_the_refused_read_would_otherwise_have_succeeded() -> None:
    """Asserts the hazard rather than only the guard: the blob really is there."""
    service = FakeBlobService()
    store = _store(service)
    reference = await store.put(org_id=OTHER_ORG, execution_id=EXECUTION, payload=b"secret")

    assert await store.get(org_id=OTHER_ORG, reference=reference) == b"secret"


@pytest.mark.asyncio
async def test_a_missing_blob_reads_as_none() -> None:
    """Expired, swept, or never written — an ordinary outcome, not an error."""
    store = _store(FakeBlobService())

    assert await store.get(org_id=ORG, reference=f"{ORG}/{EXECUTION}.json") is None


@pytest.mark.asyncio
async def test_a_rewrite_replaces_rather_than_failing() -> None:
    service = FakeBlobService()
    store = _store(service)

    await store.put(org_id=ORG, execution_id=EXECUTION, payload=b"first")
    await store.put(org_id=ORG, execution_id=EXECUTION, payload=b"second")

    assert await store.get(org_id=ORG, reference=f"{ORG}/{EXECUTION}.json") == b"second"


def test_an_account_url_is_required() -> None:
    with pytest.raises(StoredArtifactError, match="ARTIFACTS_ACCOUNT_URL"):
        BlobArtifactStore(account_url="", container="artifacts")


# --------------------------------------------------------------------------
# Reachability: `artifact_store()` really selects it (CLAUDE.md's rule).
# --------------------------------------------------------------------------


def test_the_factory_selects_blob_when_the_deployment_asks_for_it() -> None:
    settings = Settings(  # pyright: ignore[reportArgumentType]
        artifacts_backend="blob",
        artifacts_account_url="https://sttest.blob.core.windows.net/",
        artifacts_container="artifacts",
    )

    assert isinstance(artifact_store(settings), BlobArtifactStore)


def test_the_factory_still_defaults_to_local() -> None:
    """Nothing about a developer's stack changes."""
    assert isinstance(artifact_store(Settings()), LocalArtifactStore)
