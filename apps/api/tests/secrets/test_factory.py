"""Which backend a deployment gets, and which one it is refused.

DECISIONS D-001 promises that a production image cannot fall back to the
development backend. That promise is worth exactly as much as this file.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from dataagent.config import Settings
from dataagent.main import create_app
from dataagent.secrets.factory import build_secrets_provider
from dataagent.secrets.local import LocalSecretsProvider

KEY = Fernet.generate_key().decode()


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "env": "local",
        "build_env": "dev",
        "secrets_backend": "local",
        "local_secrets_key": KEY,
    }
    return Settings(**(base | overrides))  # pyright: ignore[reportArgumentType]


def test_local_development_gets_the_file_backend(tmp_path: Path) -> None:
    provider = build_secrets_provider(_settings(local_secrets_path=tmp_path / "secrets.json"))

    assert isinstance(provider, LocalSecretsProvider)


@pytest.mark.parametrize("overrides", [{"env": "prod"}, {"build_env": "prod"}])
def test_production_refuses_the_local_backend(overrides: dict[str, object]) -> None:
    """Either signal is enough: the image it was built as, or where it is running."""
    with pytest.raises(RuntimeError, match="not permitted in a production"):
        build_secrets_provider(_settings(**overrides))


def test_a_production_process_refuses_to_boot_with_it() -> None:
    """Not merely at first use. A process that boots is assumed to be working."""
    with pytest.raises(RuntimeError, match="not permitted in a production"):
        create_app(settings=_settings(env="prod", auth_mode="entra", oidc_authority="https://x"))


def test_key_vault_now_exists_and_says_what_it_still_needs() -> None:
    """This test used to assert the placeholder that said *"arrives in WP12.2"*.

    WP12.2 is where it arrived, so the old assertion would now pass only if the
    backend were still missing. Replaced rather than deleted: what is worth
    holding is that choosing this backend without configuring it refuses at build
    time and names the variable, instead of constructing a client that fails
    later on a request path. The provider's own behaviour is in
    `tests/secrets/test_keyvault.py`.
    """
    with pytest.raises(RuntimeError, match="KEY_VAULT_URL"):
        build_secrets_provider(_settings(secrets_backend="keyvault"))


def test_a_missing_key_names_the_command_that_makes_one() -> None:
    with pytest.raises(RuntimeError, match=r"make secrets\.key"):
        build_secrets_provider(_settings(local_secrets_key=None))


def test_an_empty_key_counts_as_missing() -> None:
    """`LOCAL_SECRETS_KEY=` in a .env is a placeholder, not a key."""
    with pytest.raises(RuntimeError, match=r"make secrets\.key"):
        build_secrets_provider(_settings(local_secrets_key="   "))


def test_the_default_path_sits_beside_the_repositorys_env_file() -> None:
    settings = _settings()

    resolved = settings.resolve_local_secrets_path()

    assert resolved.parts[-3:] == ("ops", ".secrets", "secrets.json")
