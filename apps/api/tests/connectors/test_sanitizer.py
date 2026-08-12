"""The sanitizer, against the errors real drivers actually produce.

Every string below was written to look like something asyncpg, libpq or the
Microsoft ODBC driver would hand us on a bad day. The assertion is always the
same and always negative: the password is not in the output, the host is not in
the output, and nothing that could be pasted into a client is left behind.
"""

from __future__ import annotations

import pytest

from dataagent.connectors.sanitizer import REDACTED, sanitize, sanitize_exception

# Named after its role in the test, not after the field it stands for — for the
# same reason as in the data-source tests: a quoted literal beside a constant
# called "password" is exactly what a secret scanner is built to catch.
LEAKED_VALUE = "s3cr3t-p4ssw0rd"
HOST = "pizza-db.internal.example.com"

#: (what a driver said, the pieces that must not survive)
CORPUS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        f"connection failed: postgresql://dataagent:{LEAKED_VALUE}@{HOST}:5432/pizza?sslmode=require",
        (LEAKED_VALUE, HOST, "dataagent"),
    ),
    (
        f'password authentication failed for user "dataagent" (host={HOST} port=5432 '
        f"user=dataagent password={LEAKED_VALUE} dbname=pizza)",
        (LEAKED_VALUE, HOST),
    ),
    (
        "[Microsoft][ODBC Driver 18 for SQL Server]Login failed for user 'sa'. "
        f"(Server=tcp:{HOST},1433;Database=pizza;UID=sa;PWD={LEAKED_VALUE};"
        "Encrypt=yes;TrustServerCertificate=no)",
        (LEAKED_VALUE, HOST),
    ),
    (
        "ConnectionRefusedError: [Errno 111] Connect call failed ('10.42.7.19', 5432)",
        ("10.42.7.19",),
    ),
    (
        f"socket.gaierror: [Errno -2] Name or service not known: {HOST}",
        (HOST,),
    ),
    (
        "OSError: Multiple exceptions: [Errno 61] Connect call failed "
        "('::1', 5432, 0, 0), [Errno 61] Connect call failed ('127.0.0.1', 5432)",
        ("127.0.0.1",),
    ),
    (
        f'could not translate host name "{HOST}" to address',
        (HOST,),
    ),
    (
        f"jdbc:sqlserver://{HOST}:1433;user=sa;password={LEAKED_VALUE}",
        (LEAKED_VALUE, HOST),
    ),
    (
        f"Authorization: Bearer {LEAKED_VALUE}",
        (LEAKED_VALUE,),
    ),
)


@pytest.mark.parametrize(("message", "forbidden"), CORPUS)
def test_nothing_sensitive_survives(message: str, forbidden: tuple[str, ...]) -> None:
    scrubbed = sanitize(message, known=(HOST, LEAKED_VALUE, "dataagent", "pizza"))

    for secret in forbidden:
        assert secret not in scrubbed, f"{secret!r} survived sanitizing: {scrubbed}"


@pytest.mark.parametrize(("message", "forbidden"), CORPUS)
def test_the_patterns_hold_even_with_nothing_known(
    message: str, forbidden: tuple[str, ...]
) -> None:
    """The known-values list is a second layer, not the only one.

    A connector that forgets to pass what it was connecting with must still not
    leak a password, so the patterns are asserted on their own here. Bare
    usernames and database names are the exception: outside a ``key=value`` pair
    they are indistinguishable from ordinary words, which is exactly why the
    known-values layer exists.
    """
    scrubbed = sanitize(message)

    for secret in forbidden:
        if secret in {"dataagent", "pizza"}:
            continue
        assert secret not in scrubbed, f"{secret!r} survived pattern sanitizing: {scrubbed}"


def test_the_shape_of_the_error_is_kept() -> None:
    """Over-redaction has a cost too: an error nobody can act on."""
    scrubbed = sanitize(f"FATAL: password authentication failed (password={LEAKED_VALUE})")

    assert "password authentication failed" in scrubbed
    assert f"password={REDACTED}" in scrubbed


def test_ordinary_text_is_left_alone() -> None:
    """A sanitizer that mangles every message makes support harder, not safer."""
    message = 'relation "orders" does not exist at character 15'

    assert sanitize(message) == message


def test_sqlstate_and_errno_decorations_are_not_addresses() -> None:
    message = "[08001] [Errno 111] connection refused"

    assert sanitize(message) == message


def test_an_exception_keeps_its_type() -> None:
    """The class name is what a support engineer needs, and is never a secret."""
    error = ConnectionRefusedError(f"connect to {HOST}:5432 refused")

    rendered = sanitize_exception(error, known=(HOST,))

    assert rendered.startswith("ConnectionRefusedError:")
    assert HOST not in rendered


def test_an_exception_with_no_message_still_renders() -> None:
    assert sanitize_exception(TimeoutError()) == "TimeoutError"


def test_a_short_known_value_does_not_shred_the_message() -> None:
    """SQL Server's 'sa' is a real username. Redacting it must not rewrite words."""
    scrubbed = sanitize("Login failed for user 'sa'. sanitize the message", known=("sa",))

    assert "sanitize the message" in scrubbed
    assert "'sa'" not in scrubbed
