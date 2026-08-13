"""The artifact store, and what it will and will not hand back."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dataagent.config import Settings
from dataagent.dal.artifacts import (
    LocalArtifactStore,
    StoredArtifactError,
    encode,
    expires_at,
    summarize,
)
from dataagent.dal.masking import MaskedFrame


def frame(*rows: tuple[object, ...]) -> MaskedFrame:
    return MaskedFrame(
        columns=("id", "email"),
        rows=rows,
        truncated=True,
        duration_ms=11,
        masked_columns=("email",),
    )


async def test_a_result_can_be_written_and_read_back(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    org_id, execution_id = uuid.uuid4(), uuid.uuid4()

    reference = await store.put(org_id=org_id, execution_id=execution_id, payload=b"{}")

    assert reference == f"{org_id}/{execution_id}.json"
    assert await store.get(org_id=org_id, reference=reference) == b"{}"


async def test_an_artifact_that_is_gone_is_none_rather_than_an_error(tmp_path: Path) -> None:
    """Expired, swept, or never written all answer the same way: the caller has
    to handle absence anyway, so absence is not exceptional."""
    store = LocalArtifactStore(tmp_path)
    org_id = uuid.uuid4()

    assert await store.get(org_id=org_id, reference=f"{org_id}/{uuid.uuid4()}.json") is None


async def test_another_organizations_reference_is_refused(tmp_path: Path) -> None:
    """Keys carry the tenant, and something has to check them or they are
    decoration (architecture Part 6.4)."""
    store = LocalArtifactStore(tmp_path)
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    reference = await store.put(org_id=theirs, execution_id=uuid.uuid4(), payload=b"secret")

    with pytest.raises(StoredArtifactError):
        await store.get(org_id=mine, reference=reference)


@pytest.mark.parametrize(
    "reference",
    ["{org}/../{other}/x.json", "{org}/../../etc/passwd", "{org}/nested/../../escape.json"],
)
async def test_a_reference_cannot_climb_out_of_its_own_prefix(
    reference: str, tmp_path: Path
) -> None:
    """The prefix check alone would pass all of these — they start with the
    right organization and then leave."""
    store = LocalArtifactStore(tmp_path)
    org_id, other = uuid.uuid4(), uuid.uuid4()

    with pytest.raises(StoredArtifactError):
        await store.get(org_id=org_id, reference=reference.format(org=org_id, other=other))


def test_a_summary_carries_the_shape_and_none_of_the_values() -> None:
    """It goes into a prompt, so what it must not carry is a value."""
    summary = summarize(frame((1, "a***@l***.com")))

    assert summary == {
        "columns": ["id", "email"],
        "row_count": 1,
        "truncated": True,
        "masked_columns": ["email"],
        "duration_ms": 11,
    }
    assert "a***" not in str(summary)


def test_the_encoded_result_is_readable_json() -> None:
    payload = json.loads(encode(frame((1, "a***@l***.com"), (2, None))))

    assert payload["columns"] == ["id", "email"]
    assert payload["rows"] == [[1, "a***@l***.com"], [2, None]]
    assert payload["truncated"] is True


def test_values_a_driver_invents_are_stored_as_text() -> None:
    """Decimals, dates and UUIDs each arrive as their own type from one engine
    or the other. This copy is for reading, not for arithmetic."""
    payload = json.loads(encode(frame((uuid.UUID(int=1), datetime(2026, 7, 1, tzinfo=UTC)))))

    assert payload["rows"] == [
        ["00000000-0000-0000-0000-000000000001", "2026-07-01 00:00:00+00:00"]
    ]


def test_retention_comes_from_settings() -> None:
    settings = Settings(
        env="ci", build_env="dev", git_sha="x", log_level="WARNING", artifact_retention_days=7
    )
    now = datetime(2026, 8, 13, tzinfo=UTC)

    assert (expires_at(settings, now=now) - now).days == 7
