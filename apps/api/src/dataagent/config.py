"""Application settings.

The only sanctioned way to read configuration (plan §1.4). Secrets never appear
in code, compose files, or docs — they arrive as environment variables here, and
from Phase 3 customer credentials go exclusively through ``SecretsProvider``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["local", "ci", "dev", "prod"]


def find_env_file(start: Path) -> Path | None:
    """First ``.env`` at or above ``start``, or None.

    Resolved from the source file rather than the working directory, because
    `make migrate` runs Alembic with its cwd inside apps/api and a developer
    running uvicorn by hand may be anywhere.

    Searching upward rather than counting parents is not stylistic: inside the
    container this module lives at ``/app/src/dataagent/config.py``, which has
    fewer ancestors than the repository layout. A fixed index raised IndexError
    there — at import time, so the image would not start at all.
    """
    for parent in start.resolve().parents:
        candidate = parent / ".env"
        if candidate.is_file():
            return candidate
    return None


#: In a container there is no .env and configuration comes from the environment,
#: which is how it should be; this is a local-development convenience only.
_REPO_ENV_FILE = find_env_file(Path(__file__))


class Settings(BaseSettings):
    """Settings resolved from the process environment, then from a local ``.env``.

    Every field has a default that is safe for local development, so the app boots
    with no configuration at all. Fields that must not have a usable default
    (real credentials) are introduced in the phase that needs them and validated
    at startup rather than silently defaulted.
    """

    model_config = SettingsConfigDict(
        # Later entries win, so a directory-local .env can override the repo one.
        env_file=(_REPO_ENV_FILE, ".env") if _REPO_ENV_FILE else ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    env: Environment = Field(
        default="local",
        description="Deployment environment. Guards dev-only features (plan §3.1).",
    )
    build_env: Literal["dev", "prod"] = Field(
        default="dev",
        description=(
            "Which image target built this process. Set by the Dockerfile. From Phase 2 "
            "the prod target physically excludes the dev token issuer."
        ),
    )
    git_sha: str = Field(
        default="unknown",
        description="Commit the image was built from. Baked in at docker build time.",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    auth_mode: Literal["dev", "entra"] = Field(
        default="entra",
        description=(
            "'dev' mounts a local OIDC issuer so the stack runs without an Entra "
            "tenant, and is refused in a prod build or environment. The default is "
            "'entra' so that the weaker mode is always something someone chose: an "
            "environment that forgets to set this gets real identity, not a "
            "process willing to mint its own tokens."
        ),
    )
    oidc_authority: str | None = Field(
        default=None,
        description=(
            "Where the identity provider publishes /.well-known/openid-configuration. "
            "Required when AUTH_MODE=entra. For a Microsoft Entra external tenant: "
            "https://<domain-prefix>.ciamlogin.com/<tenant-id>/v2.0"
        ),
    )
    oidc_issuer: str | None = Field(
        default=None,
        description=(
            "Optional pin for the expected `iss` claim. Normally left unset: the "
            "issuer is read from the discovery document, which is authoritative "
            "and, for Entra external tenants, not guessable from the authority URL."
        ),
    )
    oidc_audience: str = Field(
        default="dataagent-api",
        description="Audience every accepted token must carry — this API, and no other.",
    )
    dev_issuer_url: str = Field(
        default="http://localhost:8000/dev",
        description="Where the dev issuer publishes its discovery document and JWKS.",
    )

    database_url: str | None = Field(
        default=None,
        description=(
            "Owner/migration DSN, e.g. postgresql+asyncpg://user:pw@host:5432/db. "
            "Used by Alembic only. Optional so the app still boots for /healthz."
        ),
    )
    app_database_url: str | None = Field(
        default=None,
        description=(
            "Runtime DSN, connecting as dataagent_app: no superuser, no BYPASSRLS, "
            "owner of nothing. Everything the request path touches goes through it."
        ),
    )

    # NoDecode: without it pydantic-settings JSON-decodes any complex-typed env
    # var, so the natural `CORS_ORIGINS=http://a,http://b` would be a boot error.
    cors_origins: Annotated[tuple[str, ...], NoDecode] = Field(
        default=("http://localhost:3000",),
        description="Browser origins allowed to call this API. The web app only.",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept ``CORS_ORIGINS=http://a,http://b`` as well as a JSON array."""
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text.startswith("["):
            return json.loads(text)
        return tuple(origin.strip() for origin in text.split(",") if origin.strip())

    def resolve_authority(self) -> str:
        """Where to fetch the provider's discovery document."""
        if self.auth_mode == "dev":
            return self.dev_issuer_url
        if not self.oidc_authority:
            raise RuntimeError(
                "AUTH_MODE=entra requires OIDC_AUTHORITY. Without it there is "
                "nothing to discover signing keys from, and every token would "
                "have to be taken on trust."
            )
        return self.oidc_authority

    def assert_auth_is_production_safe(self) -> None:
        """Refuse to start a production build that trusts the dev issuer.

        Checked at application startup rather than at first request, because a
        service that boots and *then* fails open is indistinguishable from one
        that works until someone looks.
        """
        if self.auth_mode == "dev" and (self.build_env == "prod" or self.env == "prod"):
            raise RuntimeError(
                "AUTH_MODE=dev is not permitted in a production build or environment: "
                "it would accept tokens this process minted for itself."
            )

    def require_database_url(self) -> str:
        """The owner DSN, or a failure that names the fix.

        Code paths that genuinely need the database call this instead of reading
        the optional field, so a missing DSN surfaces as one clear message rather
        than an ``asyncpg`` error about connecting to ``None``.
        """
        if not self.database_url:
            raise RuntimeError(
                "DATABASE_URL is not set. Copy .env.example to .env (`make env`) "
                "and start the platform database with `make up`."
            )
        return self.database_url

    def require_app_database_url(self) -> str:
        """The runtime DSN. Never falls back to the owner DSN.

        A fallback here would mean that forgetting one environment variable
        silently promotes the API to the role that owns every table — which
        ``FORCE ROW LEVEL SECURITY`` would still contain, but which is exactly
        the kind of quiet privilege drift this separation exists to prevent.
        """
        if not self.app_database_url:
            raise RuntimeError(
                "APP_DATABASE_URL is not set. It must connect as dataagent_app, "
                "not as the owner. Run `make db.setup` after `make up`."
            )
        return self.app_database_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, read once.

    Cached so that reading configuration is never a hidden I/O cost on a request
    path. Tests that need different settings clear the cache explicitly.
    """
    return Settings()
