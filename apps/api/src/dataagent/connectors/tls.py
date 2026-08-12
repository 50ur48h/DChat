"""How encrypted a connection to a customer database has to be (B-013).

Before this module the answer was ``ssl="prefer"``, written into the connector:
use TLS when the server offers it, plaintext when it does not, and say nothing
either way. That is right for a compose container that serves no certificate and
wrong for a managed database reached across a network the customer does not
control — and the two are indistinguishable from the outside, which is the part
that made it worth fixing rather than documenting.

So the mode is a setting, and the safe direction is the default:

* A host on **this machine** — loopback, or a name listed in ``TLS_LOCAL_HOSTS``
  because it only exists inside a container network — may use ``prefer``.
* **Anything else** gets ``TLS_MODE``, which is typed as the encrypted subset, so
  there is no spelling of the configuration that sends credentials to a remote
  database in the clear.
* In production **nothing is local**. The locality test is skipped entirely, so a
  stray ``TLS_LOCAL_HOSTS`` in a prod environment cannot re-open the hole.

A data source may name its own mode, and the same rule judges it: tightening is
always allowed, and an optional mode is refused unless the host is local. The
refusal is a 422 at registration, where an admin can act on it, rather than a
surprise on the first query.

One honesty note that the wording of every message here depends on: ``require``
means *encrypted*, not *authenticated*. It stops passive eavesdropping and does
nothing about a server that is not the one you meant, because no certificate is
checked. Only ``verify-ca`` and ``verify-full`` do that. The evidence a test
produces says so in as many words, because "TLS: on" invites exactly the wrong
conclusion.
"""

from __future__ import annotations

import ssl
from collections.abc import Sequence
from ipaddress import ip_address
from pathlib import Path
from typing import get_args

from dataagent.config import EncryptedTlsMode, Settings, TlsMode

__all__ = [
    "ENCRYPTED_TLS_MODES",
    "OPTIONAL_TLS_MODES",
    "TLS_MODES",
    "VERIFYING_TLS_MODES",
    "TlsPolicyError",
    "describe_verification",
    "is_local_host",
    "odbc_parameters",
    "resolve_tls_mode",
    "ssl_parameter",
    "tls_detail",
]

#: Every mode this product understands, in increasing order of strictness.
TLS_MODES: tuple[str, ...] = get_args(TlsMode)

#: The ones that guarantee an encrypted connection.
ENCRYPTED_TLS_MODES: tuple[str, ...] = get_args(EncryptedTlsMode)

#: The ones that permit a plaintext connection. ``prefer`` belongs here even
#: though it usually *does* encrypt: "usually" is not a property to build on.
OPTIONAL_TLS_MODES: frozenset[str] = frozenset(set(TLS_MODES) - set(ENCRYPTED_TLS_MODES))

#: The ones that also check who they are talking to.
VERIFYING_TLS_MODES: frozenset[str] = frozenset({"verify-ca", "verify-full"})

#: Names that always mean this machine. ``host.docker.internal`` is how a
#: container reaches its host, which is still a loopback hop in practice.
_LOCAL_NAMES: frozenset[str] = frozenset({"localhost", "host.docker.internal"})


class TlsPolicyError(ValueError):
    """A data source asked for less encryption than its address allows."""


def is_local_host(host: str, *, also_local: Sequence[str] = ()) -> bool:
    """Is this address on the machine the API runs on?

    Structural where it can be — a loopback literal is loopback in any
    deployment — and configured where it cannot: ``seed-pizza-pg`` is a name that
    exists only inside one compose network, and no amount of parsing reveals
    that. ``also_local`` is where that knowledge is declared, by whoever created
    the network.

    Private ranges are deliberately *not* local. ``10.0.0.5`` is somebody's
    network, and the whole point of the setting is that we do not know whose.
    """
    name = _normalise(host)
    if not name:
        return False
    if name in _LOCAL_NAMES or name.endswith(".localhost"):
        return True
    if any(name == _normalise(entry) for entry in also_local):
        return True
    try:
        return ip_address(name).is_loopback
    except ValueError:
        # A hostname, not an address. DNS is not consulted: resolution at
        # registration time would decide policy from an answer that can change.
        return False


def resolve_tls_mode(*, host: str, requested: str | None, settings: Settings) -> str:
    """The mode this data source will connect with, or a refusal.

    ``requested`` is what an admin asked for, or ``None`` for "apply the policy".
    Raises ``TlsPolicyError`` — never quietly downgrades and never quietly
    upgrades, because both would make the mode shown on screen a fiction.
    """
    local = not settings.is_production and is_local_host(host, also_local=settings.tls_local_hosts)
    mode = requested if requested is not None else _default_mode(local=local, settings=settings)

    if mode not in TLS_MODES:
        raise TlsPolicyError(f"Unknown TLS mode {mode!r}. Choose one of: {', '.join(TLS_MODES)}.")
    if mode in OPTIONAL_TLS_MODES and not local:
        raise TlsPolicyError(
            f"TLS mode {mode!r} allows an unencrypted connection, which is only "
            f"permitted for a database on this machine — {host!r} is not one. "
            f"Use one of: {', '.join(ENCRYPTED_TLS_MODES)}."
        )
    return mode


def ssl_parameter(mode: str, ca_file: Path | None = None) -> str | ssl.SSLContext:
    """What the driver is handed for this mode.

    The modes that do not check a certificate are passed straight through:
    asyncpg accepts libpq's own names, and re-implementing that ladder would be
    a second place for it to be wrong.

    The verifying modes are built here instead, and that is not a stylistic
    choice. Handed the string ``verify-full``, asyncpg follows libpq and looks
    for a root certificate at ``~/.postgresql/root.crt`` — a path that does not
    exist in this application's container, so the strictest setting in the
    product would fail on every managed cloud database, whose certificate chains
    to a perfectly ordinary public CA. Building the context ourselves means the
    system trust store is the default and ``TLS_CA_FILE`` is the override, which
    is what an operator setting "verify-full" expects to have asked for.
    """
    if mode not in TLS_MODES:
        raise TlsPolicyError(f"Unknown TLS mode {mode!r}. Choose one of: {', '.join(TLS_MODES)}.")
    if mode not in VERIFYING_TLS_MODES:
        return mode

    try:
        context = ssl.create_default_context(cafile=str(ca_file) if ca_file else None)
    except OSError as error:
        # Names the setting, not the path: this message can reach a tenant
        # admin, and where a deployment keeps its certificates is not theirs.
        raise TlsPolicyError(
            "TLS_CA_FILE could not be read, so no certificate can be verified."
        ) from error

    context.verify_mode = ssl.CERT_REQUIRED
    # verify-ca trusts the chain and accepts any name in it; verify-full also
    # requires the certificate to name the host we asked for.
    context.check_hostname = mode == "verify-full"
    return context


def odbc_parameters(mode: str) -> dict[str, str]:
    """The same ladder, in Microsoft's ODBC driver's vocabulary (WP3.3).

    Three things are worth knowing about the mapping:

    * ``prefer`` asks for encryption. SQL Server has offered TLS since 2005 and
      generates a self-signed certificate when none is configured, so "encrypt if
      the server can" is satisfied by asking — and what we *report* afterwards is
      what actually happened, not what was asked for.
    * ``verify-ca`` is stricter here than its name promises. ``msodbcsql18`` has
      no chain-only mode: turning certificate validation on also checks the host
      name, so this behaves as ``verify-full``. Saying so is better than pretending
      to offer a distinction the driver does not have.
    * ``TLS_CA_FILE`` does not apply. The driver trusts the system store, so a
      private CA has to be installed in the image rather than pointed at — B-015.
    """
    if mode not in TLS_MODES:
        raise TlsPolicyError(f"Unknown TLS mode {mode!r}. Choose one of: {', '.join(TLS_MODES)}.")
    if mode == "disable":
        return {"encrypt": "no"}
    return {
        "encrypt": "yes",
        "trustservercertificate": "no" if mode in VERIFYING_TLS_MODES else "yes",
    }


def tls_detail(
    *, mode: str, encrypted: bool, version: str | None = None, cipher: str | None = None
) -> str:
    """The one line a test result shows about encryption.

    Written once, here, so that every connector says it the same way and so that
    the sentence can never be "TLS: on" — which is true of ``require`` and
    invites the conclusion that the server was authenticated, which it was not.
    """
    if not encrypted:
        return f"{mode} — this connection is NOT encrypted"

    negotiated = ", ".join(part for part in (version, cipher) if part)
    suffix = f" with {negotiated}" if negotiated else ""
    return f"{mode} — encrypted{suffix}; {describe_verification(mode)}"


def describe_verification(mode: str) -> str:
    """What an encrypted connection in this mode checked, and what it did not."""
    if mode == "verify-full":
        return "the certificate and host name were verified"
    if mode == "verify-ca":
        return "the certificate was verified against a trusted CA"
    return "the server certificate was not verified"


def _default_mode(*, local: bool, settings: Settings) -> str:
    return settings.tls_mode_local if local else settings.tls_mode


def _normalise(host: str) -> str:
    """Lower-cased, unbracketed, without the trailing dot of an absolute name."""
    name = host.strip().rstrip(".").lower()
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    return name
