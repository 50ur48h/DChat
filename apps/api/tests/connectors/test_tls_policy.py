"""Which connections are allowed to be unencrypted, and which are not (B-013).

The setting exists because ``prefer`` fails open by design: it encrypts when the
server offers TLS and sends the credential in the clear when it does not, without
raising anything. So these tests are mostly about the refusals — a policy that
only ever says yes is indistinguishable from no policy at all.
"""

from __future__ import annotations

import ssl
from pathlib import Path

import pytest

from dataagent.config import Settings
from dataagent.connectors.tls import (
    ENCRYPTED_TLS_MODES,
    OPTIONAL_TLS_MODES,
    TLS_MODES,
    VERIFYING_TLS_MODES,
    TlsPolicyError,
    describe_verification,
    is_local_host,
    resolve_tls_mode,
    ssl_parameter,
    tls_detail,
)

#: Explicit rather than ambient: these tests must say the same thing on a machine
#: whose .env has opinions about TLS.
POLICY = Settings(env="ci", build_env="dev", tls_mode="require", tls_mode_local="prefer")
COMPOSE_POLICY = Settings(
    env="local", build_env="dev", tls_mode="require", tls_local_hosts=("seed-pizza-pg",)
)
PRODUCTION = Settings(env="prod", build_env="prod", tls_mode="require", tls_mode_local="prefer")


# ---------------------------------------------------------------------------
# What counts as "on this machine"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    ["localhost", "LOCALHOST", "localhost.", "127.0.0.1", "127.0.0.53", "::1", "[::1]"],
)
def test_loopback_is_local_however_it_is_spelled(host: str) -> None:
    assert is_local_host(host) is True


@pytest.mark.parametrize(
    "host",
    [
        "db.example.com",
        "10.0.0.5",
        "192.168.1.20",
        "seed-pizza-pg",
        "notlocalhost",
        "",
    ],
)
def test_everything_else_is_not(host: str) -> None:
    """Private ranges included: 10.0.0.5 is somebody's network, and the point of
    the policy is that we do not know whose."""
    assert is_local_host(host) is False


def test_a_container_name_is_local_only_because_someone_declared_it() -> None:
    assert is_local_host("seed-pizza-pg", also_local=("seed-pizza-pg",)) is True
    assert is_local_host("seed-pizza-pg", also_local=("mssql",)) is False


# ---------------------------------------------------------------------------
# The policy
# ---------------------------------------------------------------------------


def test_a_local_database_may_be_unencrypted_by_default() -> None:
    assert resolve_tls_mode(host="localhost", requested=None, settings=POLICY) == "prefer"


def test_anything_else_requires_tls_by_default() -> None:
    assert resolve_tls_mode(host="db.example.com", requested=None, settings=POLICY) == "require"


@pytest.mark.parametrize("mode", sorted(OPTIONAL_TLS_MODES))
def test_a_remote_database_may_not_ask_for_an_optional_mode(mode: str) -> None:
    with pytest.raises(TlsPolicyError, match=r"only \s*permitted for a database on this machine"):
        resolve_tls_mode(host="db.example.com", requested=mode, settings=POLICY)


@pytest.mark.parametrize("mode", ENCRYPTED_TLS_MODES)
def test_tightening_is_always_allowed(mode: str) -> None:
    """Including for a local database: a developer with a certificate should be
    able to test the strict path locally."""
    assert resolve_tls_mode(host="localhost", requested=mode, settings=POLICY) == mode
    assert resolve_tls_mode(host="db.example.com", requested=mode, settings=POLICY) == mode


def test_a_declared_container_host_may_be_unencrypted() -> None:
    assert resolve_tls_mode(host="seed-pizza-pg", requested=None, settings=COMPOSE_POLICY) == (
        "prefer"
    )


def test_in_production_nothing_is_local() -> None:
    """The failure this guards against is a TLS_LOCAL_HOSTS that follows a
    configuration file from a laptop into a deployment."""
    production_with_a_local_list = Settings(
        env="prod",
        build_env="prod",
        tls_mode="require",
        tls_mode_local="prefer",
        tls_local_hosts=("seed-pizza-pg", "localhost"),
    )

    assert resolve_tls_mode(host="localhost", requested=None, settings=PRODUCTION) == "require"
    assert (
        resolve_tls_mode(
            host="seed-pizza-pg", requested=None, settings=production_with_a_local_list
        )
        == "require"
    )
    with pytest.raises(TlsPolicyError):
        resolve_tls_mode(host="127.0.0.1", requested="prefer", settings=PRODUCTION)


def test_an_unknown_mode_is_refused_with_the_list_of_real_ones() -> None:
    with pytest.raises(TlsPolicyError, match="verify-full"):
        resolve_tls_mode(host="localhost", requested="ssl-please", settings=POLICY)


def test_the_configuration_itself_cannot_turn_encryption_off_everywhere() -> None:
    """TLS_MODE is typed as the encrypted subset, so this fails at startup rather
    than at the first connection to a customer's database."""
    with pytest.raises(ValueError, match="tls_mode"):
        Settings(tls_mode="prefer")  # pyright: ignore[reportArgumentType]


# ---------------------------------------------------------------------------
# What the driver is handed
# ---------------------------------------------------------------------------


@pytest.fixture
def certifi_bundle() -> Path:
    """A real PEM file to point TLS_CA_FILE at.

    certifi's, because OpenSSL refuses an empty or malformed bundle and the
    platform's own path is ``None`` on Windows — this test asserts about the
    context we build, not about which certificates are in it.
    """
    import certifi

    return Path(certifi.where())


@pytest.mark.parametrize("mode", [mode for mode in TLS_MODES if mode not in VERIFYING_TLS_MODES])
def test_the_non_verifying_modes_are_passed_to_the_driver_by_name(mode: str) -> None:
    """asyncpg accepts libpq's own names, so nothing is re-implemented here."""
    assert ssl_parameter(mode) == mode


@pytest.mark.parametrize("mode", sorted(VERIFYING_TLS_MODES))
def test_the_verifying_modes_use_the_system_trust_store_by_default(mode: str) -> None:
    """Not asyncpg's default, which is libpq's: a root certificate at
    ``~/.postgresql/root.crt``. That file does not exist in this application's
    container, so passing the string through would make the strictest setting in
    the product fail against every managed database with a public CA."""
    context = ssl_parameter(mode)

    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.get_ca_certs(), "no trusted certificates were loaded"


def test_a_configured_ca_bundle_is_used_instead(certifi_bundle: Path) -> None:
    full = ssl_parameter("verify-full", certifi_bundle)
    ca_only = ssl_parameter("verify-ca", certifi_bundle)

    assert isinstance(full, ssl.SSLContext)
    assert full.check_hostname is True
    assert full.verify_mode is ssl.CERT_REQUIRED
    assert isinstance(ca_only, ssl.SSLContext)
    assert ca_only.check_hostname is False, "verify-ca checks the CA, not the name"
    assert ca_only.verify_mode is ssl.CERT_REQUIRED


def test_a_ca_bundle_is_ignored_by_the_modes_that_do_not_verify(tmp_path: Path) -> None:
    assert ssl_parameter("require", tmp_path / "unused.pem") == "require"


def test_an_unreadable_bundle_is_refused_without_naming_the_path(tmp_path: Path) -> None:
    """The message can reach a tenant admin; where this deployment keeps its
    certificates is not theirs to learn."""
    missing = tmp_path / "nowhere" / "ca.pem"

    with pytest.raises(TlsPolicyError, match="TLS_CA_FILE") as raised:
        ssl_parameter("verify-full", missing)

    assert str(missing) not in str(raised.value)


def test_an_unknown_mode_never_reaches_the_driver() -> None:
    with pytest.raises(TlsPolicyError):
        ssl_parameter("ssl-please")


# ---------------------------------------------------------------------------
# What it says afterwards
# ---------------------------------------------------------------------------


def test_an_unencrypted_connection_says_so_in_capitals() -> None:
    detail = tls_detail(mode="prefer", encrypted=False)

    assert "NOT encrypted" in detail
    assert detail.startswith("prefer")


def test_require_does_not_claim_the_server_was_verified() -> None:
    """The distinction the wording exists for: 'require' is encryption without
    authentication, and reading it as both is the natural mistake."""
    detail = tls_detail(
        mode="require", encrypted=True, version="TLSv1.3", cipher="TLS_AES_256_GCM_SHA384"
    )

    assert "TLSv1.3" in detail
    assert "TLS_AES_256_GCM_SHA384" in detail
    assert "not verified" in detail


def test_the_verifying_modes_say_what_they_checked() -> None:
    assert "host name" in describe_verification("verify-full")
    assert "trusted CA" in describe_verification("verify-ca")
    assert "not verified" in describe_verification("prefer")
