"""Can this API reach that address at all?

The cheapest half of "test connection", and the half that needs no driver and no
credentials: open a TCP connection, then drop it. It answers the question that
actually goes wrong when someone registers a database — a typo in the host, a
port that is not published, a firewall in between — and it answers it before any
credential is put on the wire.

The other half, "these credentials work and they cannot write", belongs to the
connector and arrives in WP3.2 (architecture Part 5.2, M3 "read-only verified").
This module never grows into that: it deliberately knows nothing about engines.

One property worth naming: an Admin can make this process open a TCP connection
to an address of their choosing. That is inherent to registering a database — the
product's whole purpose is to connect where the customer says — and WP3.2's real
connector has exactly the same reach. It is bounded by being Admin-only, by a
short timeout, and by returning nothing but "answered" or "did not answer".
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime

from dataagent.connectors.sanitizer import sanitize_exception

__all__ = ["DEFAULT_TIMEOUT_SECONDS", "Reachability", "check_reachable"]

#: Short on purpose: a request is waiting on this, and an address that has not
#: answered in five seconds is not going to serve queries either.
DEFAULT_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class Reachability:
    """What the probe saw. ``detail`` is already sanitized and safe to return."""

    reachable: bool
    detail: str
    checked_at: datetime


async def check_reachable(
    *, host: str, port: int, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
) -> Reachability:
    """Open a TCP connection to ``host:port`` and close it again.

    Never raises: an unreachable address is an ordinary answer to this question,
    not an error. Details that a driver or the OS would happily include — the
    address, the resolver's opinion of it — are removed before returning, because
    this string is bound for an API response and a log line.
    """
    now = datetime.now(UTC)

    try:
        async with asyncio.timeout(timeout_seconds):
            _, writer = await asyncio.open_connection(host, port)
    except TimeoutError:
        return Reachability(
            reachable=False,
            detail=(
                f"No answer within {timeout_seconds:.0f}s. Check the host and port, "
                "and whether a firewall allows this service to reach them."
            ),
            checked_at=now,
        )
    except OSError as error:
        # ConnectionRefusedError, socket.gaierror and friends. The host is passed
        # as a known value so that a resolver echoing it back cannot leak it.
        return Reachability(
            reachable=False,
            detail=sanitize_exception(error, known=(host,)),
            checked_at=now,
        )

    writer.close()
    # The probe already has its answer; a tidy close is courtesy to the far end,
    # not something worth failing a request over.
    with contextlib.suppress(OSError):
        await writer.wait_closed()

    return Reachability(
        reachable=True,
        detail=(
            "The address accepted a connection. Credentials and read-only "
            "verification are checked once the connector ships (WP3.2)."
        ),
        checked_at=now,
    )
