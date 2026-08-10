"""Version resolution for the health payload."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError

import pytest

from dataagent import __version__, health


def test_resolve_version_uses_installed_package_metadata() -> None:
    assert health.resolve_version() == __version__


def test_resolve_version_falls_back_when_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Running from source without an install is a normal tooling case."""

    def _raise(_name: str) -> str:
        raise PackageNotFoundError("dataagent")

    monkeypatch.setattr(health, "package_version", _raise)

    assert health.resolve_version() == __version__
