"""The identity self-check, driven the way the deploy drives it.

**What this file has to establish, and it is two different things.** That the
check *passes* against a working configuration — otherwise it is a step that goes
red on every deploy and gets deleted — and that it *fails*, specifically, on each
way a permission can be missing. B-124's standing condition: a check that has
never been seen to fail is unproven, and this one guards a permission whose
absence already shipped once (**B-125**).

The local backends are the control. They are a real `LocalSecretsProvider` and a
real `LocalArtifactStore`, not fakes, so "the check can pass" is established
against code that actually stores things.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from dataagent.config import Settings
from dataagent.ops import selfcheck
from dataagent.secrets.base import SecretNotFoundError
from dataagent.secrets.local import LocalSecretsProvider

pytestmark = pytest.mark.asyncio


def _returns(store: Any) -> Callable[..., Any]:
    """A typed stand-in for `artifact_store`.

    A bare `lambda _settings=None: store` leaves pyright with an untyped
    parameter, and `reportUnknownLambdaType` is an error in this project rather
    than a warning.
    """

    def factory(settings: Settings | None = None) -> Any:
        return store

    return factory


def _settings(tmp_path: Path) -> Settings:
    """A configuration whose two backends are local and isolated per test."""
    return Settings(  # pyright: ignore[reportArgumentType]
        _env_file=None,  # pyright: ignore[reportCallIssue]
        env="ci",
        build_env="dev",
        secrets_backend="local",
        local_secrets_key=SecretStr(Fernet.generate_key().decode()),
        local_secrets_path=tmp_path / "secrets.json",
        artifacts_backend="local",
        artifacts_path=tmp_path / "artifacts",
    )


@pytest.fixture
def local_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = _settings(tmp_path)
    provider = LocalSecretsProvider(
        key=settings.require_local_secrets_key(), path=tmp_path / "secrets.json"
    )
    monkeypatch.setattr(selfcheck, "get_secrets_provider", lambda: provider)
    return settings


# ---------------------------------------------------------------------------
# The control: it passes against backends that work
# ---------------------------------------------------------------------------


async def test_both_checks_pass_against_working_backends(local_settings: Settings) -> None:
    """Without this, every failure test below is satisfied by a check that always
    fails — which is the same defect one layer up."""
    assert "write, read, delete" in await selfcheck.check_secrets(local_settings)
    assert "write and read" in await selfcheck.check_artifacts(local_settings)


async def test_run_reports_success(
    local_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exit code is what the deploy step reads."""
    monkeypatch.setattr(selfcheck, "get_settings", lambda: local_settings)

    assert await selfcheck.run() == 0


# ---------------------------------------------------------------------------
# The failures, one per way a permission goes missing
# ---------------------------------------------------------------------------


class _Refusing:
    """A provider that refuses whichever verb it was told to refuse.

    Modelled on the shape of the real failure: B-125's identity could `get` and
    could not `set`, so a provider that fails *uniformly* would not reproduce it.
    """

    def __init__(self, refuse: str) -> None:
        self._refuse = refuse
        self._stored: dict[str, dict[str, str]] = {}

    async def put(self, secret_ref: str, value: Mapping[str, str]) -> None:
        if self._refuse == "put":
            raise PermissionError("ForbiddenByRbac: setSecret")
        self._stored[secret_ref] = dict(value)

    async def get(self, secret_ref: str) -> dict[str, str]:
        if self._refuse == "get":
            raise PermissionError("ForbiddenByRbac: getSecret")
        if secret_ref not in self._stored:
            raise SecretNotFoundError(secret_ref)
        return self._stored[secret_ref]

    async def delete(self, secret_ref: str) -> None:
        if self._refuse == "delete":
            raise PermissionError("ForbiddenByRbac: deleteSecret")
        # "silent" keeps the value: a delete that reports success and changes
        # nothing, which every assertion except the final read would accept.
        if self._refuse != "silent":
            self._stored.pop(secret_ref, None)


@pytest.mark.parametrize(
    ("refuse", "expected"),
    [
        ("put", "could not WRITE a secret"),
        ("get", "could not READ it back"),
        ("delete", "could not DELETE a secret"),
        ("silent", "still readable"),
    ],
)
async def test_a_secrets_permission_that_is_missing_is_named(
    local_settings: Settings, monkeypatch: pytest.MonkeyPatch, refuse: str, expected: str
) -> None:
    """**The `silent` case is the one worth the parametrize.**

    A backend that accepts `delete` and keeps the value passes the write, the
    read and the delete. Only the read *after* the delete catches it, and the
    consequence is concrete: the credential of a removed data source outliving
    the data source.
    """
    monkeypatch.setattr(selfcheck, "get_secrets_provider", lambda: _Refusing(refuse))

    with pytest.raises(selfcheck.SelfCheckError, match=expected):
        await selfcheck.check_secrets(local_settings)


async def test_the_write_failure_names_the_role_that_fixes_it(
    local_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing deploy step is worth exactly what its message is worth.

    B-125 was diagnosed from `ForbiddenByRbac ... setSecret`, and the fix was a
    role name nobody had to hand. This says it.
    """
    monkeypatch.setattr(selfcheck, "get_secrets_provider", lambda: _Refusing("put"))

    with pytest.raises(selfcheck.SelfCheckError) as raised:
        await selfcheck.check_secrets(local_settings)

    assert "Key Vault Secrets Officer" in str(raised.value)


class _BrokenStore:
    """An artifact store that writes nowhere, or reads back something else."""

    def __init__(self, mode: str) -> None:
        self._mode = mode

    async def put(self, *, org_id: uuid.UUID, execution_id: uuid.UUID, payload: bytes) -> str:
        if self._mode == "put":
            raise PermissionError("AuthorizationPermissionMismatch")
        return f"{org_id}/{execution_id}.json"

    async def get(self, *, org_id: uuid.UUID, reference: str) -> bytes | None:
        if self._mode == "get":
            raise PermissionError("AuthorizationPermissionMismatch")
        # A write that landed in another container is indistinguishable from this.
        return None if self._mode == "missing" else b"something else entirely"


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("put", "could not WRITE an artifact"),
        ("get", "could not READ it back"),
        ("missing", "is missing"),
        ("wrong", "not what was written"),
    ],
)
async def test_an_artifact_permission_that_is_missing_is_named(
    local_settings: Settings, monkeypatch: pytest.MonkeyPatch, mode: str, expected: str
) -> None:
    """**`missing` is the case Blob makes reachable.** `get` answers `None` for a
    blob that is not there, so a write that silently went to the wrong container
    reads back as an absence rather than an error — and without this assertion the
    check would report success on a store that keeps nothing."""
    store: Any = _BrokenStore(mode)
    monkeypatch.setattr(selfcheck, "artifact_store", _returns(store))

    with pytest.raises(selfcheck.SelfCheckError, match=expected):
        await selfcheck.check_artifacts(local_settings)


async def test_the_artifact_failure_names_the_role_that_fixes_it(
    local_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    store: Any = _BrokenStore("put")
    monkeypatch.setattr(selfcheck, "artifact_store", _returns(store))

    with pytest.raises(selfcheck.SelfCheckError) as raised:
        await selfcheck.check_artifacts(local_settings)

    assert "Storage Blob Data Contributor" in str(raised.value)


async def test_run_reports_failure_and_keeps_going(
    local_settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One broken backend must not hide the state of the other.

    A deploy that reports "Key Vault is wrong" and stops, when Blob is wrong too,
    costs a second fifteen-minute round trip to learn the second half.
    """
    store: Any = _BrokenStore("put")
    monkeypatch.setattr(selfcheck, "get_settings", lambda: local_settings)
    monkeypatch.setattr(selfcheck, "get_secrets_provider", lambda: _Refusing("put"))
    monkeypatch.setattr(selfcheck, "artifact_store", _returns(store))

    assert await selfcheck.run() == 1

    captured = capsys.readouterr()
    assert "could not WRITE a secret" in captured.err
    assert "could not WRITE an artifact" in captured.err
    assert "2 check(s) failed" in captured.err


async def test_a_probe_never_writes_where_credentials_live(
    local_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ds/` is where real data-source credentials are kept.

    A probe under that prefix could be caught by a sweep meant for them, or
    mistaken for one by a person reading the vault. This asserts the namespace is
    what the module's comment claims — the kind of promise that is otherwise only
    a comment.
    """
    seen: list[str] = []

    class _Recording(_Refusing):
        async def put(self, secret_ref: str, value: Mapping[str, str]) -> None:
            seen.append(secret_ref)
            await super().put(secret_ref, value)

    monkeypatch.setattr(selfcheck, "get_secrets_provider", lambda: _Recording("none"))
    await selfcheck.check_secrets(local_settings)

    assert seen and all(ref.startswith(f"{selfcheck.PROBE_NAMESPACE}/") for ref in seen)
    assert not any(ref.startswith("ds/") for ref in seen)
