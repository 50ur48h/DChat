"""The reachability probe: a real socket, and the answers it must give."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from dataagent.connectors import probe
from dataagent.connectors.probe import check_reachable

HOST = "127.0.0.1"


@pytest.fixture
async def listening_port() -> AsyncIterator[int]:
    """A server on a port the operating system chose, closed afterwards."""
    server = await asyncio.start_server(_ignore, HOST, 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield int(port)
    finally:
        server.close()
        await server.wait_closed()


async def test_an_address_that_answers_is_reachable(listening_port: int) -> None:
    result = await check_reachable(host=HOST, port=listening_port)

    assert result.reachable is True
    assert "WP3.2" in result.detail


async def test_a_closed_port_is_an_answer_not_an_exception(closed_port: int) -> None:
    """The common case — a typo or a firewall — must not raise a 500."""
    result = await check_reachable(host=HOST, port=closed_port)

    assert result.reachable is False
    assert result.detail


async def test_the_detail_never_carries_the_address() -> None:
    """It is bound for an API response and a log line."""
    host = "pizza-db.internal.example.com"

    result = await check_reachable(host=host, port=5432, timeout_seconds=2)

    assert result.reachable is False
    assert host not in result.detail


async def test_a_hanging_address_times_out_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic: a real unroutable address behaves differently per network."""

    async def never_connects(*_: object, **__: object) -> tuple[object, object]:
        await asyncio.sleep(60)
        raise AssertionError("unreachable")  # pragma: no cover

    monkeypatch.setattr(probe.asyncio, "open_connection", never_connects)

    result = await check_reachable(host=HOST, port=5432, timeout_seconds=0.05)

    assert result.reachable is False
    assert "No answer within" in result.detail


@pytest.fixture
async def closed_port() -> int:
    """A port that was bound long enough to be sure it is free now.

    Asking the operating system for a port and then releasing it is the only
    reliable way to name a closed one: a hardcoded number is something a
    developer's machine will eventually be running, and the neighbour of a live
    port is a coin toss.
    """
    server = await asyncio.start_server(_ignore, HOST, 0)
    port = int(server.sockets[0].getsockname()[1])
    server.close()
    await server.wait_closed()
    return port


async def _ignore(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    writer.close()
