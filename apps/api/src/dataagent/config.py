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

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["local", "ci", "dev", "prod"]
SecretsBackend = Literal["local", "keyvault"]

#: How much encryption a connection to a *customer's* database must have, spelled
#: the way libpq spells it so the words mean what an operator already thinks they
#: mean. ``allow`` is deliberately absent: "try plaintext first, then TLS" is a
#: mode nobody wants and everybody misreads.
TlsMode = Literal["disable", "prefer", "require", "verify-ca", "verify-full"]

#: The subset that guarantees encryption. The distinction is not decoration:
#: ``disable`` and ``prefer`` both permit a plaintext connection, and ``prefer``
#: does it silently, which is the failure mode B-013 was raised about.
EncryptedTlsMode = Literal["require", "verify-ca", "verify-full"]

#: Where the local backend keeps its encrypted file, relative to the repository
#: root. Gitignored, and every value inside it is encrypted regardless.
LOCAL_SECRETS_RELATIVE_PATH = Path("ops") / ".secrets" / "secrets.json"


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
        description=(
            "Audience every accepted token must carry. Comma-separated when one API "
            "is known by more than one name: Entra v2 access tokens carry the "
            "resource's client-ID GUID, while v1 tokens carry its api:// URI. Both "
            "name the same app registration, so accepting both is not a widening."
        ),
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

    secrets_backend: SecretsBackend = Field(
        default="local",
        description=(
            "Where customer credentials are kept. 'local' is the Fernet-encrypted "
            "file backend for development (DECISIONS D-001) and is refused in a "
            "production build or environment; 'keyvault' arrives in WP12.2."
        ),
    )
    #: SecretStr, unlike the DSNs above: this one key decrypts every customer
    #: credential this deployment holds, so it must not be readable from a repr,
    #: a traceback frame, or a settings dump.
    local_secrets_key: SecretStr | None = Field(
        default=None,
        description=(
            "Fernet key for the local secrets backend. Generated, never chosen: "
            "`make secrets.key`. Required when SECRETS_BACKEND=local."
        ),
    )
    local_secrets_path: Path | None = Field(
        default=None,
        description=(
            "Where the local backend keeps its encrypted file. Defaults to "
            "ops/.secrets/secrets.json beside the repository's .env."
        ),
    )

    #: Typed as the *encrypted* subset on purpose: there is no supported way to
    #: configure this deployment to talk to a remote database in plaintext. A
    #: `.env` that tries fails at startup, naming the field, rather than quietly
    #: sending credentials over somebody else's network.
    tls_mode: EncryptedTlsMode = Field(
        default="require",
        description=(
            "TLS a data source uses when its host is not on this machine. "
            "'require' encrypts without checking the certificate; 'verify-ca' and "
            "'verify-full' also validate it, and are what a managed cloud database "
            "should use once its CA is configured."
        ),
    )
    tls_mode_local: TlsMode = Field(
        default="prefer",
        description=(
            "TLS for a data source on this machine — loopback, or a host named in "
            "TLS_LOCAL_HOSTS. 'prefer' is the default because the compose "
            "databases serve no certificate; it is ignored entirely when ENV or "
            "BUILD_ENV is prod, where nothing counts as local."
        ),
    )
    tls_local_hosts: Annotated[tuple[str, ...], NoDecode] = Field(
        default=(),
        description=(
            "Extra hostnames that count as local, comma-separated. Loopback "
            "addresses and 'localhost' always do; this exists for container "
            "networks, where the pizza database answers to 'seed-pizza-pg' and "
            "there is no certificate for that name anywhere."
        ),
    )
    tls_ca_file: Path | None = Field(
        default=None,
        description=(
            "PEM bundle to validate customer database certificates against, for "
            "the verify-* modes. Unset means the system trust store, which is "
            "correct for managed cloud databases and wrong for a private CA."
        ),
    )

    dal_max_rows: int = Field(
        default=1000,
        gt=0,
        description=(
            "Ceiling on rows any one query may return. Written into the SQL, so "
            "the engine stops early, and enforced again when the rows are read."
        ),
    )
    dal_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        description=(
            "Deadline for one query against a customer's database. Their server "
            "is the one paying for a slow query, so this is short by default."
        ),
    )

    artifacts_path: Path = Field(
        default=Path("ops/artifacts"),
        description=(
            "Where query results are kept locally, one directory per org. Blob "
            "storage replaces this in Phase 12 behind the same interface."
        ),
    )
    artifact_retention_days: int = Field(
        default=30,
        gt=0,
        description=(
            "How long a stored result lives. A promise to the customer, so it is "
            "written onto the row rather than left to a cleanup script's mood."
        ),
    )

    # NoDecode: without it pydantic-settings JSON-decodes any complex-typed env
    # var, so the natural `CORS_ORIGINS=http://a,http://b` would be a boot error.
    cors_origins: Annotated[tuple[str, ...], NoDecode] = Field(
        default=("http://localhost:3000",),
        description="Browser origins allowed to call this API. The web app only.",
    )

    @field_validator("local_secrets_key", mode="before")
    @classmethod
    def _blank_key_is_no_key(cls, value: object) -> object:
        """``LOCAL_SECRETS_KEY=`` in a .env means "not set", not "the empty key".

        Without this, the commented placeholder in .env.example produces a
        SecretStr("") that fails much later, inside Fernet, with a message about
        key length rather than about configuration.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("cors_origins", "tls_local_hosts", mode="before")
    @classmethod
    def _split_list(cls, value: object) -> object:
        """Accept ``CORS_ORIGINS=http://a,http://b`` as well as a JSON array."""
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text.startswith("["):
            return json.loads(text)
        return tuple(item.strip() for item in text.split(",") if item.strip())

    @property
    def is_production(self) -> bool:
        """A production *build* or a production *environment* — either counts.

        Both halves matter: a dev image deployed to prod and a prod image with a
        careless ENV are the same mistake seen from opposite ends, and every
        weaker-mode-for-convenience switch in this file is refused for both.
        """
        return self.build_env == "prod" or self.env == "prod"

    def resolve_audiences(self) -> list[str]:
        """Every audience value that identifies *this* API.

        A token is still accepted only if it names this one registration; the
        list exists because Entra spells that registration differently depending
        on the token version it issued.
        """
        return [part.strip() for part in self.oidc_audience.split(",") if part.strip()]

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
        if self.auth_mode == "dev" and self.is_production:
            raise RuntimeError(
                "AUTH_MODE=dev is not permitted in a production build or environment: "
                "it would accept tokens this process minted for itself."
            )

    def assert_secrets_backend_is_production_safe(self) -> None:
        """Refuse to start a production build that keeps credentials in a file.

        The local backend's key lives in an environment variable next to the
        ciphertext it unlocks, which is fine on a laptop and unacceptable for
        customer credentials. Checked at startup for the same reason as the auth
        assertion above: a process that boots and then fails open looks healthy.
        """
        if self.secrets_backend == "local" and self.is_production:
            raise RuntimeError(
                "SECRETS_BACKEND=local is not permitted in a production build or "
                "environment: it keeps customer credentials in a file whose key "
                "sits beside it. Production uses Key Vault (DECISIONS D-001)."
            )

    def require_local_secrets_key(self) -> str:
        """The Fernet key, or a failure that names the command that makes one."""
        if self.local_secrets_key is None:
            raise RuntimeError(
                "LOCAL_SECRETS_KEY is not set, so data-source credentials cannot "
                "be encrypted. Generate one with `make secrets.key` and put it in "
                ".env. It is generated, never chosen."
            )
        return self.local_secrets_key.get_secret_value()

    def resolve_local_secrets_path(self) -> Path:
        """Where the local backend's encrypted file lives.

        Anchored to the repository root — the directory holding ``.env`` — so that
        `make api.dev` from any directory and `uvicorn` from apps/api use the same
        file. In a container there is no ``.env``, and the path is resolved
        against the working directory instead; compose sets it explicitly.
        """
        if self.local_secrets_path is not None:
            return self.local_secrets_path
        root = _REPO_ENV_FILE.parent if _REPO_ENV_FILE else Path()
        return root / LOCAL_SECRETS_RELATIVE_PATH

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
