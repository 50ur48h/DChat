"""Application settings.

The only sanctioned way to read configuration (plan §1.4). Secrets never appear
in code, compose files, or docs — they arrive as environment variables here, and
from Phase 3 customer credentials go exclusively through ``SecretsProvider``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["local", "ci", "dev", "prod"]


class Settings(BaseSettings):
    """Settings resolved from the process environment, then from a local ``.env``.

    Every field has a default that is safe for local development, so the app boots
    with no configuration at all. Fields that must not have a usable default
    (real credentials) are introduced in the phase that needs them and validated
    at startup rather than silently defaulted.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, read once.

    Cached so that reading configuration is never a hidden I/O cost on a request
    path. Tests that need different settings clear the cache explicitly.
    """
    return Settings()
