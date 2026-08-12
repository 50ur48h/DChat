"""A local OIDC issuer, so the stack runs without an Entra tenant.

This module is **development scaffolding and a liability in production**. Three
independent things keep it out of a real deployment, because one would be a
convention rather than a control:

1. It is only imported and mounted when ``AUTH_MODE=dev`` (``main.py``).
2. Importing it raises unless the settings genuinely say development.
3. The Dockerfile's ``prod`` target deletes this file from the image, so even a
   misconfigured container has nothing to import.

It deliberately mints tokens through a real JWKS and real RS256 signatures rather
than short-circuiting validation. The token path exercised locally is therefore
the same code that will face Entra: a dev mode that bypasses the validator would
leave the validator untested until production.
"""

from __future__ import annotations

import time
import uuid
from typing import Annotated, Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, Depends, Query, Request
from jwt.algorithms import RSAAlgorithm

from dataagent.config import Settings

KEY_ID = "dev-issuer-key"
TOKEN_TTL_SECONDS = 8 * 3600

router = APIRouter(prefix="/dev", tags=["dev"])


class DevIssuer:
    """Signs tokens with a keypair generated fresh for each process.

    Not persisted anywhere: restarting the API invalidates previously issued dev
    tokens, which is the correct property for a credential that must never be
    depended upon.
    """

    def __init__(self, issuer: str, audience: str) -> None:
        self._issuer = issuer
        self._audience = audience
        self._private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    @property
    def jwks(self) -> dict[str, Any]:
        public_numbers = self._private_key.public_key()
        jwk = RSAAlgorithm.to_jwk(public_numbers, as_dict=True)
        return {"keys": [{**jwk, "kid": KEY_ID, "use": "sig", "alg": "RS256"}]}

    @property
    def discovery_document(self) -> dict[str, Any]:
        return {
            "issuer": self._issuer,
            "jwks_uri": f"{self._issuer}/jwks.json",
            "id_token_signing_alg_values_supported": ["RS256"],
            "response_types_supported": ["id_token"],
            "subject_types_supported": ["public"],
        }

    def mint(self, subject: str, email: str | None, name: str | None) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self._issuer,
            "aud": self._audience,
            "sub": subject,
            "iat": now,
            "nbf": now,
            "exp": now + TOKEN_TTL_SECONDS,
            "jti": uuid.uuid4().hex,
        }
        if email:
            claims["email"] = email
        if name:
            claims["name"] = name

        return jwt.encode(
            claims,
            self._private_key,  # type: ignore[arg-type]
            algorithm="RS256",
            headers={"kid": KEY_ID},
        )


def build_dev_issuer(settings: Settings) -> DevIssuer:
    """Construct the issuer, refusing anything that is not clearly development."""
    guard_development_only(settings)
    return DevIssuer(issuer=settings.dev_issuer_url, audience=settings.resolve_audiences()[0])


def guard_development_only(settings: Settings) -> None:
    if settings.auth_mode != "dev":
        raise RuntimeError("The dev token issuer is only available when AUTH_MODE=dev")
    if settings.build_env == "prod" or settings.env == "prod":
        raise RuntimeError(
            "The dev token issuer cannot run in a production build or environment. "
            "This is a bug in configuration, not something to work around."
        )


def _issuer(request: Request) -> DevIssuer:
    """The one issuer built for this application.

    Read from application state rather than constructed per request: a new
    keypair per call would publish a JWKS that cannot verify the token minted a
    moment earlier, and every dev login would fail for a reason no one enjoys
    debugging.
    """
    issuer = getattr(request.app.state, "dev_issuer", None)
    if not isinstance(issuer, DevIssuer):
        raise RuntimeError("The dev issuer is not mounted on this application")
    return issuer


@router.get("/.well-known/openid-configuration", summary="Dev OIDC discovery")
async def discovery(issuer: Annotated[DevIssuer, Depends(_issuer)]) -> dict[str, Any]:
    return issuer.discovery_document


@router.get("/jwks.json", summary="Dev signing keys")
async def jwks(issuer: Annotated[DevIssuer, Depends(_issuer)]) -> dict[str, Any]:
    return issuer.jwks


@router.get("/token", summary="Mint a development bearer token")
async def token(
    issuer: Annotated[DevIssuer, Depends(_issuer)],
    sub: Annotated[str, Query(description="Subject claim — stands in for an IdP user id")],
    email: Annotated[str | None, Query()] = None,
    name: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    return {
        "access_token": issuer.mint(subject=sub, email=email, name=name),
        "token_type": "Bearer",
        "expires_in": TOKEN_TTL_SECONDS,
    }
