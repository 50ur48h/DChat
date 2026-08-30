"""One connection per run, and what happens when it breaks (B-176)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from dataagent.dal.lease import ConnectionLease

pytestmark = pytest.mark.anyio

ORG = uuid.uuid4()
SOURCE = uuid.uuid4()


class _Connector:
    """Counts the closes, so a leak is a number rather than a suspicion."""

    def __init__(self) -> None:
        self.closed = 0

    async def aclose(self) -> None:
        self.closed += 1


def _built(monkeypatch: pytest.MonkeyPatch) -> list[_Connector]:
    """Replace the two calls a lease makes to build one, and record each build."""
    built: list[_Connector] = []

    async def _get_data_source(*_: Any, **__: Any) -> object:
        return object()

    async def _connector_for_view(*_: Any, **__: Any) -> _Connector:
        made = _Connector()
        built.append(made)
        return made

    from dataagent.datasources import service as datasources

    monkeypatch.setattr(datasources, "get_data_source", _get_data_source)
    monkeypatch.setattr(datasources, "connector_for_view", _connector_for_view)
    return built


async def test_a_run_builds_one_connection_however_many_queries_it_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**The whole point, as a number.** Every query used to build and close its
    own: a platform read, a Key Vault round trip and a TLS handshake, measured at
    3.4 seconds each and 23.5 seconds of the run that then ran out of steps."""
    built = _built(monkeypatch)
    lease = ConnectionLease(ORG, SOURCE)

    first = await lease.connector()
    for _ in range(7):
        assert await lease.connector() is first

    assert len(built) == 1


async def test_closing_the_run_closes_the_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connection to a customer's database held open by a run nobody is
    watching is worse than the handshake it saves."""
    built = _built(monkeypatch)
    lease = ConnectionLease(ORG, SOURCE)
    await lease.connector()

    await lease.aclose()

    assert built[0].closed == 1


async def test_closing_a_lease_that_never_opened_is_fine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runner closes in `finally`, so this runs on every path — including
    the ones that failed before a single query."""
    built = _built(monkeypatch)

    await ConnectionLease(ORG, SOURCE).aclose()

    assert built == []


async def test_a_forgotten_connection_is_replaced_not_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A query can fail for reasons the connection survives and for reasons it
    does not. After the second kind the run must not spend the steps it has left
    replaying a dead socket."""
    built = _built(monkeypatch)
    lease = ConnectionLease(ORG, SOURCE)
    first = await lease.connector()

    await lease.forget()
    second = await lease.connector()

    assert second is not first
    assert first.closed == 1
    assert len(built) == 2


async def test_a_connection_that_fails_to_close_does_not_fail_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing an already-broken socket can itself raise, and the point of
    forgetting is that this connection is already suspect."""
    _built(monkeypatch)
    lease = ConnectionLease(ORG, SOURCE)
    connector = await lease.connector()

    async def _raises() -> None:
        raise OSError("socket is gone")

    monkeypatch.setattr(connector, "aclose", _raises)
    await lease.aclose()  # must not raise

    assert await lease.connector() is not connector


async def test_a_lease_for_another_source_is_not_borrowed() -> None:
    """Belt and braces. A conversation names one database, so this is true on
    every real path — and handing one organization's connection to another's
    question is the single worst thing this product could do."""
    lease = ConnectionLease(ORG, SOURCE)

    assert lease.matches(ORG, SOURCE)
    assert not lease.matches(ORG, uuid.uuid4())
    assert not lease.matches(uuid.uuid4(), SOURCE)
