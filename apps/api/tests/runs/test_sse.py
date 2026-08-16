"""The live tail of a trace, and what it guarantees (architecture 10.3).

The properties here are all about *delivery*, because the content is settled
elsewhere: `agent_events` is the source of truth and `read_events` is the reader,
so a streaming test that asserted on payloads would be re-testing WP7.1.

What is worth holding is that a client can leave and come back without losing
anything, that a stream ends when its run does, and that "who may read this" is
answered before a byte is sent.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

import pytest

from conftest import Tenant
from dataagent.runs import service
from dataagent.runs.events import EventWriter
from dataagent.runs.sse import event_stream, format_frame


async def _conversation_and_run(tenant: Tenant) -> uuid.UUID:
    view = await service.create_conversation(
        org_id=tenant.org_id, user_id=tenant.user_id, title="Trace"
    )
    asked = await service.post_message(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        conversation_id=view.id,
        content="How many orders?",
        idempotency_key=uuid.uuid4().hex,
    )
    return asked.run_id


async def _drain(stream: AsyncIterator[str], *, limit: int = 50) -> list[str]:
    """Collect frames until the stream ends or `limit` is reached."""
    frames: list[str] = []
    async for frame in stream:
        frames.append(frame)
        if len(frames) >= limit:
            break
    return frames


def _payloads(frames: list[str]) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for frame in frames:
        for line in frame.splitlines():
            if line.startswith("data: "):
                found.append(json.loads(line[len("data: ") :]))
    return found


# ---------------------------------------------------------------------------
# The frame
# ---------------------------------------------------------------------------


async def test_a_frame_carries_the_sequence_number_as_its_id(tenant: Tenant) -> None:
    """`id:` is what makes reconnection work at all — the browser sends it back
    as `Last-Event-ID`, and a frame without one is a frame a client cannot resume
    from."""
    run_id = await _conversation_and_run(tenant)
    writer = EventWriter(org_id=tenant.org_id, run_id=run_id)
    await writer.emit("plan_created", {"purpose": "count"})
    events = await service.list_events(org_id=tenant.org_id, run_id=run_id)

    frame = format_frame(events[-1])

    assert frame.startswith(f"id: {events[-1].seq}\n")
    assert "event: plan_created\n" in frame
    assert frame.endswith("\n\n"), "an SSE frame is terminated by a blank line"


# ---------------------------------------------------------------------------
# Leaving and coming back
# ---------------------------------------------------------------------------


async def test_a_stream_replays_everything_before_it_follows(tenant: Tenant) -> None:
    """Replay is the default, not a recovery path: connect and reconnect are the
    same operation, so there is no window in which an event can be missed."""
    run_id = await _conversation_and_run(tenant)
    writer = EventWriter(org_id=tenant.org_id, run_id=run_id)
    await writer.emit("run_started", {"question": "How many orders?"})
    await writer.emit("plan_created", {"purpose": "count"})
    await service.transition(org_id=tenant.org_id, run_id=run_id, status="running")
    await service.transition(org_id=tenant.org_id, run_id=run_id, status="completed")

    frames = await _drain(
        event_stream(org_id=tenant.org_id, run_id=run_id, user_id=tenant.user_id, poll_seconds=0.01)
    )

    types = [payload["type"] for payload in _payloads(frames)]
    assert types[:2] == ["run_started", "plan_created"]
    assert "run_finished" in types


async def test_a_reconnect_resumes_from_where_it_left_off(tenant: Tenant) -> None:
    """`?after=` is the explicit form of the same contract the poll uses."""
    run_id = await _conversation_and_run(tenant)
    writer = EventWriter(org_id=tenant.org_id, run_id=run_id)
    await writer.emit("run_started", {"question": "q"})
    await writer.emit("plan_created", {"purpose": "count"})
    seen = await service.list_events(org_id=tenant.org_id, run_id=run_id)
    await service.transition(org_id=tenant.org_id, run_id=run_id, status="running")
    await service.transition(org_id=tenant.org_id, run_id=run_id, status="completed")

    frames = await _drain(
        event_stream(
            org_id=tenant.org_id,
            run_id=run_id,
            user_id=tenant.user_id,
            after=seen[-1].seq,
            poll_seconds=0.01,
        )
    )

    types = [payload["type"] for payload in _payloads(frames)]
    assert "plan_created" not in types, "what the client already had is not sent again"
    assert "run_finished" in types


async def test_the_browsers_own_bookmark_is_honoured(tenant: Tenant) -> None:
    """`EventSource` reconnects on its own and sends `Last-Event-ID`. Answering it
    is what makes a dropped connection recover without the page doing anything."""
    run_id = await _conversation_and_run(tenant)
    writer = EventWriter(org_id=tenant.org_id, run_id=run_id)
    await writer.emit("run_started", {"question": "q"})
    await writer.emit("plan_created", {"purpose": "count"})
    seen = await service.list_events(org_id=tenant.org_id, run_id=run_id)
    await service.transition(org_id=tenant.org_id, run_id=run_id, status="running")
    await service.transition(org_id=tenant.org_id, run_id=run_id, status="completed")

    frames = await _drain(
        event_stream(
            org_id=tenant.org_id,
            run_id=run_id,
            user_id=tenant.user_id,
            last_event_id=str(seen[-1].seq),
            poll_seconds=0.01,
        )
    )

    types = [payload["type"] for payload in _payloads(frames)]
    assert "plan_created" not in types


async def test_a_malformed_bookmark_replays_rather_than_refusing(tenant: Tenant) -> None:
    """It arrives from a reconnecting browser. The worst outcome of ignoring it is
    a replay the client already has; refusing would break reconnection entirely."""
    run_id = await _conversation_and_run(tenant)
    writer = EventWriter(org_id=tenant.org_id, run_id=run_id)
    await writer.emit("run_started", {"question": "q"})
    await service.transition(org_id=tenant.org_id, run_id=run_id, status="running")
    await service.transition(org_id=tenant.org_id, run_id=run_id, status="completed")

    frames = await _drain(
        event_stream(
            org_id=tenant.org_id,
            run_id=run_id,
            user_id=tenant.user_id,
            last_event_id="not-a-number",
            poll_seconds=0.01,
        )
    )

    assert "run_started" in [payload["type"] for payload in _payloads(frames)]


# ---------------------------------------------------------------------------
# Ending
# ---------------------------------------------------------------------------


async def test_the_stream_ends_when_the_run_does(tenant: Tenant) -> None:
    """Otherwise a client holds a socket open on a run that finished an hour ago.

    Asserted by the generator *completing* rather than by counting frames: a
    stream that ends is the property, and a timeout here would mean it does not.
    """
    run_id = await _conversation_and_run(tenant)
    await service.transition(org_id=tenant.org_id, run_id=run_id, status="running")
    await service.transition(org_id=tenant.org_id, run_id=run_id, status="completed")

    async def collect() -> int:
        return len(
            [
                frame
                async for frame in event_stream(
                    org_id=tenant.org_id,
                    run_id=run_id,
                    user_id=tenant.user_id,
                    poll_seconds=0.01,
                )
            ]
        )

    assert await asyncio.wait_for(collect(), timeout=5) > 0


async def test_an_event_written_as_the_run_ends_is_still_delivered(tenant: Tenant) -> None:
    """The ordering trap, pinned.

    The status is read *before* the final read of the table. Reading it after
    would drop any event written between the two — in practice `run_finished`
    itself, which is the one event a client is waiting for.
    """
    run_id = await _conversation_and_run(tenant)
    await service.transition(org_id=tenant.org_id, run_id=run_id, status="running")

    async def finish_shortly() -> None:
        await asyncio.sleep(0.05)
        await service.transition(org_id=tenant.org_id, run_id=run_id, status="completed")

    task = asyncio.create_task(finish_shortly())
    frames = await asyncio.wait_for(
        _drain(
            event_stream(
                org_id=tenant.org_id,
                run_id=run_id,
                user_id=tenant.user_id,
                poll_seconds=0.01,
            )
        ),
        timeout=5,
    )
    await task

    assert "run_finished" in [payload["type"] for payload in _payloads(frames)]


async def test_a_stream_that_outlives_its_limit_stops_itself(tenant: Tenant) -> None:
    """A run that never ends must not hold a socket forever."""
    run_id = await _conversation_and_run(tenant)
    await service.transition(org_id=tenant.org_id, run_id=run_id, status="running")

    frames = await asyncio.wait_for(
        _drain(
            event_stream(
                org_id=tenant.org_id,
                run_id=run_id,
                user_id=tenant.user_id,
                poll_seconds=0.01,
                max_seconds=0.0,
            )
        ),
        timeout=5,
    )

    assert frames[-1].startswith(": "), "it says why it stopped, as an SSE comment"


# ---------------------------------------------------------------------------
# Who may read it
# ---------------------------------------------------------------------------


async def test_a_colleague_cannot_stream_your_run(tenant: Tenant) -> None:
    """The same ownership rule the poll enforces, and enforced *before* the first
    frame — a 404 delivered as an empty 200 stream is not a refusal anyone can
    see."""
    run_id = await _conversation_and_run(tenant)

    with pytest.raises(service.NotFoundError):
        await _drain(
            event_stream(
                org_id=tenant.org_id,
                run_id=run_id,
                user_id=tenant.other_user_id,
                poll_seconds=0.01,
            )
        )


# ---------------------------------------------------------------------------
# One URL, two deliveries
# ---------------------------------------------------------------------------


async def test_the_same_route_answers_json_or_a_stream(tenant: Tenant) -> None:
    """10.2 lists one events route, and it is one route: `Accept` chooses.

    Two URLs would be two contracts that could drift, and the chat UI polls this
    one today — so the poll must keep working exactly as it did.
    """
    from httpx import ASGITransport, AsyncClient

    from dataagent.auth.jwt_validator import TokenValidator
    from dataagent.auth.principal import Principal
    from dataagent.config import Settings
    from dataagent.main import create_app

    class _AsTenant(TokenValidator):
        def __init__(self) -> None:
            pass

        async def validate(self, token: str) -> Principal:
            return Principal(subject=f"sub-{tenant.user_id}", email="asker@example.com")

    run_id = await _conversation_and_run(tenant)
    writer = EventWriter(org_id=tenant.org_id, run_id=run_id)
    await writer.emit("run_started", {"question": "q"})
    await service.transition(org_id=tenant.org_id, run_id=run_id, status="running")
    await service.transition(org_id=tenant.org_id, run_id=run_id, status="completed")

    app = create_app(settings=Settings(auth_mode="dev", env="ci", build_env="dev"))
    app.state.token_validator = _AsTenant()
    path = f"/v1/orgs/{tenant.org_id}/runs/{run_id}/events"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        polled = await client.get(path, headers={"Authorization": "Bearer x"})
        streamed = await client.get(
            path, headers={"Authorization": "Bearer x", "Accept": "text/event-stream"}
        )

    assert polled.headers["content-type"].startswith("application/json")
    assert next(event["type"] for event in polled.json()["events"]) == "run_started"

    assert streamed.headers["content-type"].startswith("text/event-stream")
    assert "event: run_started" in streamed.text
    assert "id: " in streamed.text
