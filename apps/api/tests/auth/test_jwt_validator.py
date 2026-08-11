"""The forgery matrix.

Each test mints a token that is wrong in exactly one way and requires the
validator to reject it. Signing is real RS256 against a real JWKS, so these
exercise the same path an Entra token will take.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from dataagent.auth.jwks import JwksCache
from dataagent.auth.jwt_validator import TokenValidator
from dataagent.auth.principal import TokenError

ISSUER = "https://issuer.example.com"
AUDIENCE = "dataagent-api"
KEY_ID = "test-key"

_TRUSTED_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_ATTACKER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks(key: rsa.RSAPrivateKey, kid: str = KEY_ID) -> dict[str, Any]:
    jwk = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    return {"keys": [{**jwk, "kid": kid, "use": "sig", "alg": "RS256"}]}


class _StubJwks(JwksCache):
    """A cache pre-loaded from a dict, so no test touches the network."""

    def __init__(self, document: dict[str, Any]) -> None:
        super().__init__(issuer=ISSUER)
        self._keys = {
            key.key_id: key for key in jwt.PyJWKSet.from_dict(document).keys if key.key_id
        }
        self.discovered_issuer = ISSUER
        self._fetched_at = time.monotonic()


def _validator(document: dict[str, Any] | None = None) -> TokenValidator:
    return TokenValidator(
        issuer=ISSUER, audience=AUDIENCE, jwks=_StubJwks(document or _jwks(_TRUSTED_KEY))
    )


def _mint(
    *,
    key: rsa.RSAPrivateKey = _TRUSTED_KEY,
    kid: str | None = KEY_ID,
    algorithm: str = "RS256",
    **overrides: Any,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-123",
        "email": "person@example.com",
        "name": "A Person",
        "iat": now,
        "nbf": now,
        "exp": now + 600,
        "jti": uuid.uuid4().hex,
    }
    claims.update(overrides)
    claims = {key_: value for key_, value in claims.items() if value is not None}
    headers = {"kid": kid} if kid else {}
    return jwt.encode(claims, key, algorithm=algorithm, headers=headers)  # type: ignore[arg-type]


async def test_a_good_token_yields_a_principal() -> None:
    principal = await _validator().validate(_mint())

    assert principal.subject == "user-123"
    assert principal.email == "person@example.com"
    assert principal.name == "A Person"


async def test_a_token_signed_by_the_wrong_key_is_rejected() -> None:
    with pytest.raises(TokenError) as caught:
        await _validator().validate(_mint(key=_ATTACKER_KEY))

    assert caught.value.code == "bad_signature"


async def test_an_expired_token_is_rejected() -> None:
    past = int(time.time()) - 3600

    with pytest.raises(TokenError) as caught:
        await _validator().validate(_mint(exp=past, nbf=past - 60, iat=past - 60))

    assert caught.value.code == "expired"


async def test_a_token_that_is_not_yet_valid_is_rejected() -> None:
    future = int(time.time()) + 3600

    with pytest.raises(TokenError) as caught:
        await _validator().validate(_mint(nbf=future, exp=future + 600))

    assert caught.value.code == "not_yet_valid"


async def test_a_token_for_another_audience_is_rejected() -> None:
    """A valid signature from the right issuer, minted for a different app."""
    with pytest.raises(TokenError) as caught:
        await _validator().validate(_mint(aud="some-other-api"))

    assert caught.value.code == "bad_audience"


async def test_a_token_from_another_issuer_is_rejected() -> None:
    with pytest.raises(TokenError) as caught:
        await _validator().validate(_mint(iss="https://evil.example.com"))

    assert caught.value.code == "bad_issuer"


async def test_an_unsigned_token_is_rejected() -> None:
    """The classic: `alg: none`, checked before any key is touched."""
    forged = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "user-123", "exp": int(time.time()) + 600},
        key=None,  # type: ignore[arg-type]
        algorithm="none",
        headers={"kid": KEY_ID},
    )

    with pytest.raises(TokenError) as caught:
        await _validator().validate(forged)

    assert caught.value.code == "bad_algorithm"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


async def test_an_hmac_token_signed_with_the_public_key_is_rejected() -> None:
    """RS256 to HS256 confusion: the public key used as a shared secret.

    Without an algorithm allowlist this verifies, because the "secret" is a value
    the attacker simply reads from the JWKS. Assembled by hand because PyJWT
    refuses to *mint* it — a good library, but the attacker is not using one.
    """
    public_pem = (
        _TRUSTED_KEY.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": KEY_ID}).encode())
    payload = _b64(
        json.dumps(
            {"iss": ISSUER, "aud": AUDIENCE, "sub": "user-123", "exp": int(time.time()) + 600}
        ).encode()
    )
    signature = hmac.new(
        public_pem.encode(), f"{header}.{payload}".encode(), hashlib.sha256
    ).digest()
    forged = f"{header}.{payload}.{_b64(signature)}"

    with pytest.raises(TokenError) as caught:
        await _validator().validate(forged)

    assert caught.value.code == "bad_algorithm"


async def test_a_token_naming_an_unknown_key_is_rejected() -> None:
    with pytest.raises(TokenError) as caught:
        await _validator().validate(_mint(kid="rotated-away"))

    assert caught.value.code == "unknown_key"


async def test_a_token_with_no_kid_is_rejected() -> None:
    with pytest.raises(TokenError) as caught:
        await _validator().validate(_mint(kid=None))

    assert caught.value.code == "missing_kid"


async def test_a_token_missing_its_subject_is_rejected() -> None:
    with pytest.raises(TokenError) as caught:
        await _validator().validate(_mint(sub=None))

    assert caught.value.code == "missing_claim"


async def test_garbage_is_rejected_without_raising_anything_odd() -> None:
    with pytest.raises(TokenError) as caught:
        await _validator().validate("not-a-token")

    assert caught.value.code == "malformed"


async def test_optional_claims_may_be_absent() -> None:
    principal = await _validator().validate(_mint(email=None, name=None))

    assert principal.subject == "user-123"
    assert principal.email is None
    assert principal.name is None


async def test_the_issuer_is_taken_from_discovery_when_not_pinned() -> None:
    """issuer=None means "believe the discovery document".

    This is the default because a Microsoft Entra external tenant publishes its
    metadata at <prefix>.ciamlogin.com while issuing tokens that claim
    <tenant-id>.ciamlogin.com — and the same tenant also answers on
    login.microsoftonline.com with a third value. Verified against a real tenant
    before this was written; a hand-configured guess would reject every token.
    """
    validator = TokenValidator(issuer=None, audience=AUDIENCE, jwks=_StubJwks(_jwks(_TRUSTED_KEY)))

    principal = await validator.validate(_mint())

    assert principal.subject == "user-123"


async def test_a_pinned_issuer_still_wins_when_it_disagrees() -> None:
    """Pinning remains available for a provider whose metadata is not trusted."""
    validator = TokenValidator(
        issuer="https://pinned.example.com",
        audience=AUDIENCE,
        jwks=_StubJwks(_jwks(_TRUSTED_KEY)),
    )

    with pytest.raises(TokenError) as caught:
        await validator.validate(_mint())

    assert caught.value.code == "bad_issuer"


async def test_either_name_for_the_same_api_is_accepted() -> None:
    """Entra spells one registration two ways depending on token version.

    A v2 access token carries the resource's client-ID GUID as `aud`; a v1 token
    carries its api:// URI. Both identify the same app registration, so both are
    accepted — this is one resource under two names, not two resources.
    """
    guid = "4ce7996e-0000-0000-0000-000000000000"
    validator = TokenValidator(
        issuer=None,
        audience=[f"api://{guid}", guid],
        jwks=_StubJwks(_jwks(_TRUSTED_KEY)),
    )

    for spelling in (f"api://{guid}", guid):
        principal = await validator.validate(_mint(aud=spelling))
        assert principal.subject == "user-123"


async def test_a_third_partys_audience_is_still_refused() -> None:
    """Listing two names must not become "accept anything"."""
    guid = "4ce7996e-0000-0000-0000-000000000000"
    validator = TokenValidator(
        issuer=None,
        audience=[f"api://{guid}", guid],
        jwks=_StubJwks(_jwks(_TRUSTED_KEY)),
    )

    with pytest.raises(TokenError) as caught:
        await validator.validate(_mint(aud="00000003-0000-0000-c000-000000000000"))

    assert caught.value.code == "bad_audience"


async def test_identity_falls_back_to_whatever_claim_the_provider_sent() -> None:
    """Entra populates email, preferred_username or upn depending on the tenant.

    Regression: with only `email` consulted, a real Entra sign-in showed the
    person their opaque subject id at `@unknown.invalid`.
    """
    principal = await _validator().validate(
        _mint(email=None, name=None, preferred_username="person@contoso.com", given_name="Person")
    )

    assert principal.email == "person@contoso.com"
    assert principal.name == "Person"


async def test_email_still_wins_when_it_is_present() -> None:
    principal = await _validator().validate(_mint(preferred_username="other@contoso.com"))

    assert principal.email == "person@example.com"
