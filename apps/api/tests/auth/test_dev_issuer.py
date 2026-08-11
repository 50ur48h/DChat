"""The dev issuer must be usable locally and impossible in production."""

from __future__ import annotations

import time
from typing import Any

import jwt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from dataagent.auth.jwks import JwksCache
from dataagent.auth.jwt_validator import TokenValidator
from dataagent.auth.principal import TokenError
from dataagent.config import Settings
from dataagent.main import create_app


def _dev_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "auth_mode": "dev",
        "env": "local",
        "build_env": "dev",
        "oidc_audience": "dataagent-api",
        "dev_issuer_url": "http://localhost:8000/dev",
    }
    return Settings(**{**defaults, **overrides})


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


class _JwksFromDocument(JwksCache):
    def __init__(self, issuer: str, document: dict[str, Any]) -> None:
        super().__init__(issuer=issuer)
        self._keys = {
            key.key_id: key for key in jwt.PyJWKSet.from_dict(document).keys if key.key_id
        }
        self.discovered_issuer = issuer
        self._fetched_at = time.monotonic()


# ---------------------------------------------------------------------------
# It works
# ---------------------------------------------------------------------------


async def test_a_dev_token_passes_the_real_validator() -> None:
    """The point of the dev issuer: the same validation path, not a bypass.

    A dev mode that skipped verification would leave the verifier untested until
    the day it faces Entra.
    """
    settings = _dev_settings()
    app = create_app(settings=settings)

    async with await _client(app) as client:
        minted = await client.get(
            "/dev/token", params={"sub": "dev-user", "email": "d@example.com"}
        )
        keys = await client.get("/dev/jwks.json")

    assert minted.status_code == 200
    validator = TokenValidator(
        issuer=settings.resolve_authority(),
        audience=settings.resolve_audiences(),
        jwks=_JwksFromDocument(settings.resolve_authority(), keys.json()),
    )

    principal = await validator.validate(minted.json()["access_token"])

    assert principal.subject == "dev-user"
    assert principal.email == "d@example.com"


async def test_the_discovery_document_points_at_the_keys() -> None:
    app = create_app(settings=_dev_settings())

    async with await _client(app) as client:
        document = (await client.get("/dev/.well-known/openid-configuration")).json()

    assert document["issuer"] == "http://localhost:8000/dev"
    assert document["jwks_uri"] == "http://localhost:8000/dev/jwks.json"
    assert document["id_token_signing_alg_values_supported"] == ["RS256"]


async def test_the_keys_are_stable_within_one_application() -> None:
    """One keypair per app: a fresh key per request would invalidate every token."""
    app = create_app(settings=_dev_settings())

    async with await _client(app) as client:
        first = (await client.get("/dev/jwks.json")).json()
        second = (await client.get("/dev/jwks.json")).json()

    assert first == second


async def test_a_token_from_a_different_application_does_not_verify_here() -> None:
    """Two processes, two keypairs. Restarting the API invalidates dev tokens."""
    settings = _dev_settings()
    minting_app = create_app(settings=settings)
    other_app = create_app(settings=settings)

    async with await _client(minting_app) as client:
        token = (await client.get("/dev/token", params={"sub": "dev-user"})).json()["access_token"]
    async with await _client(other_app) as client:
        keys = (await client.get("/dev/jwks.json")).json()

    validator = TokenValidator(
        issuer=settings.resolve_authority(),
        audience=settings.resolve_audiences(),
        jwks=_JwksFromDocument(settings.resolve_authority(), keys),
    )

    with pytest.raises(TokenError) as caught:
        await validator.validate(token)

    assert caught.value.code == "bad_signature"


# ---------------------------------------------------------------------------
# It cannot happen in production
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "build_env"),
    [("prod", "dev"), ("local", "prod"), ("prod", "prod")],
)
def test_a_production_build_refuses_to_boot_with_the_dev_issuer(env: str, build_env: str) -> None:
    """Startup assertion, covering both halves of "production".

    A process that boots and then fails open is indistinguishable from one that
    works until somebody looks.
    """
    with pytest.raises(RuntimeError, match="not permitted in a production"):
        create_app(settings=Settings(auth_mode="dev", env=env, build_env=build_env))  # type: ignore[arg-type]


async def test_the_dev_routes_are_absent_when_auth_mode_is_not_dev() -> None:
    app = create_app(
        settings=Settings(auth_mode="entra", oidc_authority="https://issuer.example.com")
    )

    async with await _client(app) as client:
        minted = await client.get("/dev/token", params={"sub": "dev-user"})

    assert minted.status_code == 404
    assert not hasattr(app.state, "dev_issuer")


def test_entra_mode_without_an_authority_is_refused() -> None:
    """Nothing to discover keys from means every token taken on trust."""
    with pytest.raises(RuntimeError, match="requires OIDC_AUTHORITY"):
        Settings(auth_mode="entra", oidc_authority=None).resolve_authority()
