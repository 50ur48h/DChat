"""Scrubbing connector errors before anyone can read them.

Database drivers are helpful in exactly the wrong way. asyncpg quotes the DSN it
tried, ODBC repeats the whole connection string including ``PWD=``, and a refused
TCP connection names the address. Every one of those strings is a credential or a
piece of a customer's network, and every one of them is on its way to a log line
or an API response.

So no connector exception reaches either without passing through here first
(architecture Part 7.3: "never in logs"). Two layers do the work:

* **Patterns** — URIs, ``key=value`` pairs whose key is credential-ish, IP
  addresses, ``host:port`` pairs and the ``('host', port)`` tuple Python prints
  from a failed connect.
* **Known values** — the caller passes the host, username and database it was
  using, because the surest way to remove a value from a message is to know it.
  A driver that invents a new way to phrase an error still cannot leak them.

The bias is deliberate: over-redaction costs a support engineer some context,
under-redaction puts a password in a log file that is retained for a year.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

__all__ = ["REDACTED", "sanitize", "sanitize_exception"]

REDACTED = "[redacted]"

#: Applied in order. Each entry is (pattern, replacement) and every replacement
#: keeps the *shape* of what it removed — "password=[redacted]" still tells a
#: reader that the driver was complaining about a password.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # postgresql://user:secret@host:5432/db — everything after the scheme goes.
    (re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.\-]*)://[^\s'\"<>,;)\]]+"), rf"\1://{REDACTED}"),
    # An auth header carries a scheme before the credential, so "the next token"
    # would redact the word "Bearer" and leave the token standing.
    (
        re.compile(
            r"(?i)\bauthorization\s*[=:]\s*(?:bearer|basic|digest|negotiate)?\s*[^\s;,)\"']+"
        ),
        f"authorization={REDACTED}",
    ),
    # ODBC and libpq keyword form. The key survives; the value never does.
    (
        re.compile(
            r"(?i)\b(password|passwd|pwd|pass|secret|token|api[_-]?key|access[_-]?key|sas"
            r"|credential|authorization)\b\s*[=:]\s*(?:\"[^\"]*\"|'[^']*'|[^\s;,)\"']+)"
        ),
        rf"\1={REDACTED}",
    ),
    # The rest of a connection string: where it points and who it connects as.
    (
        re.compile(
            r"(?i)\b(hostaddr|hostname|host|server|address|addr|data\s+source"
            r"|initial\s+catalog|database|dbname|username|user\s+id|uid|user)\b"
            r"\s*[=:]\s*(?:\"[^\"]*\"|'[^']*'|[^\s;,)\"']+)"
        ),
        rf"\1={REDACTED}",
    ),
    # Resolver failures name the host in prose rather than as a key=value pair.
    # These two phrasings cover libpq and Python's getaddrinfo; a bare hostname
    # in any other sentence is left to the known-values layer, because in prose
    # it is indistinguishable from "socket.gaierror" or "public.orders".
    (re.compile(r"(?i)\bhost\s*name\s*\"[^\"]*\""), f'host name "{REDACTED}"'),
    (
        re.compile(
            r"(?i)\b(name or service not known|not known|unknown host|no such host)"
            r"\s*:\s*[^\s;,)\"']+"
        ),
        rf"\1: {REDACTED}",
    ),
    # SQL Server's tcp:host,1433 form, which carries no key to match on.
    (re.compile(r"(?i)\btcp:[^\s;,)\]]+(?:,\s*\d{1,5})?"), f"tcp:{REDACTED}"),
    # Python prints a failed connect as ('10.1.2.3', 5432) or ('db.corp', 5432).
    (
        re.compile(r"\((['\"])[^'\"]+\1\s*,\s*\d{1,5}\)"),
        f"({REDACTED}, {REDACTED})",
    ),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b"), REDACTED),
    # Bracketed IPv6, with or without a port. Needs a colon inside, so log
    # decorations like [Errno 111] and SQLSTATE [08001] are left alone.
    (re.compile(r"\[[0-9A-Fa-f]*:[0-9A-Fa-f:]+\](?::\d{1,5})?"), REDACTED),
    # A dotted name with a port is an address, whatever it is called.
    (
        re.compile(r"\b[A-Za-z0-9][A-Za-z0-9._-]*\.[A-Za-z][A-Za-z0-9-]{1,62}:\d{1,5}\b"),
        REDACTED,
    ),
)

#: Around a known value: not a word character, a dot or a dash. Without this,
#: redacting the username "sa" would rewrite every word containing it, and
#: redacting the host "10.0.0.5" would fire inside "110.0.0.55".
_LEFT = r"(?<![\w.\-])"
_RIGHT = r"(?![\w.\-])"


def sanitize(message: str, *, known: Iterable[str] = ()) -> str:
    """Return ``message`` with credentials, addresses and identities removed.

    ``known`` is whatever the caller was connecting with — host, username,
    database, and the password itself. Passing them is not optional discipline:
    it is what makes this robust against a driver phrasing an error in a way no
    pattern here anticipated.
    """
    scrubbed = message

    # Longest first, so redacting "db" cannot leave a fragment of "db.corp.net"
    # behind for the next replacement to miss.
    for value in sorted({v for v in known if v and v.strip()}, key=len, reverse=True):
        scrubbed = re.sub(_LEFT + re.escape(value.strip()) + _RIGHT, REDACTED, scrubbed)

    for pattern, replacement in _PATTERNS:
        scrubbed = pattern.sub(replacement, scrubbed)

    return scrubbed


def sanitize_exception(error: BaseException, *, known: Iterable[str] = ()) -> str:
    """A one-line, safe rendering of a connector failure.

    The exception *type* is kept — it is the part a support engineer actually
    needs, and a class name is never a secret.
    """
    detail = sanitize(str(error), known=known).strip()
    name = type(error).__name__
    return f"{name}: {detail}" if detail else name
