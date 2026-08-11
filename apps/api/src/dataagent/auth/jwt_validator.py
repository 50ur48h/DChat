"""Bearer token validation.

The whole of authentication is here, and it is deliberately unforgiving:

* **Only RS256.** Passing an algorithm allowlist is what defeats the classic
  ``alg: none`` and RS256→HS256 confusion attacks, where a forged token asks to
  be verified with the public key as an HMAC secret.
* **Issuer and audience are required**, not optional niceties: a token minted by
  a different tenant of the same identity provider, for a different application,
  is a valid signature and an invalid credential.
* **Expiry, not-before and issued-at are all checked**, with a small leeway for
  clock skew and no more.

Every failure raises ``TokenError`` with a short code. Routes turn all of them
into one 401: telling a caller *why* their token was rejected tells an attacker
which half of the forgery worked.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import jwt

from dataagent.auth.jwks import JwksCache
from dataagent.auth.principal import Principal, TokenError

ALGORITHMS = ["RS256"]

#: Tolerance for clock drift between us and the identity provider.
LEEWAY_SECONDS = 30


class TokenValidator:
    def __init__(self, issuer: str | None, audience: str | Sequence[str], jwks: JwksCache) -> None:
        """``issuer`` pins the expected issuer; None means "trust discovery".

        Trusting discovery is the better default. A Microsoft Entra external
        tenant publishes its metadata at one host and issues tokens claiming
        another, and the *same* tenant also answers on login.microsoftonline.com
        with a different issuer — so a hand-written value is a coin flip that
        fails every token with bad_issuer. Pinning remains available for a
        provider whose discovery document cannot be trusted.
        """
        self._issuer = issuer
        self._audience = audience
        self._jwks = jwks

    async def validate(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as error:
            raise TokenError("malformed", "Token header could not be read") from error

        if header.get("alg") not in ALGORITHMS:
            # Checked before touching the key material, so a token asking to be
            # verified with 'none' never reaches a verifier at all.
            raise TokenError("bad_algorithm", "Token algorithm is not permitted")

        kid = header.get("kid")
        if not kid:
            raise TokenError("missing_kid", "Token does not name a signing key")

        key = await self._jwks.key_for(kid)

        # After key_for, discovery has run at least once.
        expected_issuer = self._issuer or self._jwks.discovered_issuer
        if not expected_issuer:  # pragma: no cover - only if discovery is empty
            raise TokenError("no_issuer", "No issuer is configured or discoverable")

        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=key,  # type: ignore[arg-type]
                algorithms=ALGORITHMS,
                audience=self._audience,
                issuer=expected_issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iss", "aud", "sub"], "verify_signature": True},
            )
        except jwt.ExpiredSignatureError as error:
            raise TokenError("expired", "Token has expired") from error
        except jwt.ImmatureSignatureError as error:
            raise TokenError("not_yet_valid", "Token is not valid yet") from error
        except jwt.InvalidAudienceError as error:
            raise TokenError("bad_audience", "Token was issued for another audience") from error
        except jwt.InvalidIssuerError as error:
            raise TokenError("bad_issuer", "Token was issued by another issuer") from error
        except jwt.InvalidSignatureError as error:
            raise TokenError("bad_signature", "Token signature does not verify") from error
        except jwt.MissingRequiredClaimError as error:
            raise TokenError("missing_claim", "Token is missing a required claim") from error
        except jwt.PyJWTError as error:
            raise TokenError("invalid", "Token is not valid") from error

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise TokenError("missing_claim", "Token has no usable subject")

        email = claims.get("email")
        name = claims.get("name")
        return Principal(
            subject=subject,
            email=email if isinstance(email, str) else None,
            name=name if isinstance(name, str) else None,
        )
