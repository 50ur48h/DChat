"""JWKS retrieval and caching.

Public keys are fetched from the issuer's OIDC discovery document and cached for
a TTL. Two behaviours are deliberate:

* **An unknown ``kid`` triggers exactly one refresh**, then fails. Identity
  providers rotate keys without warning, so refusing to refresh would cause an
  outage; refreshing on every unknown kid would let anyone with a made-up kid
  drive unbounded outbound requests.
* **Fetch failures do not evict a usable cache.** If the provider is briefly
  unreachable, continuing to accept tokens signed by keys we already hold is
  safer than rejecting every request.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx
import jwt
from jwt import PyJWK

from dataagent.auth.principal import TokenError

DISCOVERY_PATH = "/.well-known/openid-configuration"

#: Never refresh more often than this, however many unknown kids arrive.
MIN_REFRESH_INTERVAL_SECONDS = 30.0


def _no_keys() -> dict[str, PyJWK]:
    """A typed empty mapping — bare ``dict`` as a factory infers nothing."""
    return {}


@dataclass
class JwksCache:
    #: Where the discovery document lives. Not necessarily the issuer.
    issuer: str
    ttl_seconds: float = 3600.0
    timeout_seconds: float = 5.0
    _keys: dict[str, PyJWK] = field(default_factory=_no_keys, init=False)
    #: The issuer the provider says it is, read from its discovery document.
    #: Trusted over anything configured by hand: for a Microsoft Entra
    #: external tenant the discovery host and the issuer host differ
    #: (dchat.ciamlogin.com vs <tenant-id>.ciamlogin.com), and the same tenant
    #: also answers on login.microsoftonline.com with a *different* issuer.
    #: A hand-written guess fails every token with bad_issuer.
    discovered_issuer: str | None = field(default=None, init=False)
    _fetched_at: float = field(default=0.0, init=False)
    _last_attempt_at: float = field(default=0.0, init=False)

    async def key_for(self, kid: str) -> PyJWK:
        if kid in self._keys and not self._is_stale():
            return self._keys[kid]

        if kid not in self._keys or self._is_stale():
            await self._refresh()

        key = self._keys.get(kid)
        if key is None:
            raise TokenError("unknown_key", "Token was signed by an unrecognised key")
        return key

    def _is_stale(self) -> bool:
        return (time.monotonic() - self._fetched_at) > self.ttl_seconds

    async def _refresh(self) -> None:
        now = time.monotonic()
        if now - self._last_attempt_at < MIN_REFRESH_INTERVAL_SECONDS and self._keys:
            return
        self._last_attempt_at = now

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                discovery = await client.get(self.issuer.rstrip("/") + DISCOVERY_PATH)
                discovery.raise_for_status()
                document_json = discovery.json()
                jwks_uri = document_json["jwks_uri"]
                discovered_issuer = document_json["issuer"]

                document = await client.get(jwks_uri)
                document.raise_for_status()
                payload = document.json()
        except (httpx.HTTPError, KeyError, ValueError) as error:
            if self._keys:
                # Keep serving with the keys we already trust.
                return
            raise TokenError("jwks_unavailable", "Could not retrieve signing keys") from error

        self._keys = {key.key_id: key for key in jwt.PyJWKSet.from_dict(payload).keys if key.key_id}
        self.discovered_issuer = discovered_issuer
        self._fetched_at = now
