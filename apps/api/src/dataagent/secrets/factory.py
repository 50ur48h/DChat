"""Choosing a backend — and refusing the wrong one.

One function decides, so "which store holds customer credentials here?" has a
single answer that a reviewer can read in ten lines. The production refusal lives
in ``Settings`` next to the equivalent check for the dev token issuer, and is
asserted again at application startup: a deployment that gets this wrong must not
boot, because a process that starts and then mishandles credentials looks healthy
from the outside.
"""

from __future__ import annotations

from functools import lru_cache

from dataagent.config import Settings, get_settings
from dataagent.secrets.base import SecretsProvider
from dataagent.secrets.local import LocalSecretsProvider

__all__ = ["build_secrets_provider", "get_secrets_provider"]


def build_secrets_provider(settings: Settings | None = None) -> SecretsProvider:
    """The provider this deployment is configured for."""
    resolved = settings if settings is not None else get_settings()
    resolved.assert_secrets_backend_is_production_safe()

    if resolved.secrets_backend == "keyvault":
        raise RuntimeError(
            "SECRETS_BACKEND=keyvault is not implemented yet — the Key Vault "
            "provider arrives with the Azure deployment in WP12.2 (DECISIONS "
            "D-001). Until then this deployment cannot hold credentials."
        )

    return LocalSecretsProvider(
        key=resolved.require_local_secrets_key(),
        path=resolved.resolve_local_secrets_path(),
    )


@lru_cache(maxsize=1)
def get_secrets_provider() -> SecretsProvider:
    """The process-wide provider, built on first use.

    Lazy for the same reason the token validator is: an API with no credential
    store configured must still answer /healthz, and must still fail closed on
    the routes that need one.
    """
    return build_secrets_provider()
