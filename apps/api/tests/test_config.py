"""Tests for settings resolution."""

from __future__ import annotations

import pytest

from dataagent.config import Settings, get_settings


def test_defaults_are_safe_for_local_development() -> None:
    """The app must boot with no configuration at all."""
    settings = Settings()

    assert settings.env == "local"
    assert settings.build_env == "dev"
    assert settings.git_sha == "unknown"
    assert settings.cors_origins == ("http://localhost:3000",)


def test_cors_origins_from_env_accepts_a_comma_separated_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real path: compose and Container Apps both pass env vars as strings."""
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000, https://app.example.com")

    assert Settings().cors_origins == ("http://localhost:3000", "https://app.example.com")


def test_cors_origins_from_env_accepts_a_json_array(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", '["https://a.example.com"]')

    assert Settings().cors_origins == ("https://a.example.com",)


def test_cors_origins_rejects_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken origins list must fail at boot, not silently allow nothing."""
    monkeypatch.setenv("CORS_ORIGINS", '["https://a.example.com"')

    with pytest.raises(ValueError, match="cors_origins"):
        Settings()


def test_unknown_environment_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo in ENV must fail loudly at boot, not silently pick a weaker mode."""
    monkeypatch.setenv("ENV", "produciton")

    with pytest.raises(ValueError, match="env"):
        Settings()


def test_settings_are_immutable() -> None:
    settings = Settings()

    with pytest.raises(ValueError, match="frozen"):
        settings.git_sha = "tampered"  # pyright: ignore[reportAttributeAccessIssue]


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
