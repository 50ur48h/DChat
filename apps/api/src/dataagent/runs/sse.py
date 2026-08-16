"""The live tail of a run's trace (architecture 10.3, 10.2).

10.3 is unambiguous about what this is: *"`agent_events` is the single source of
truth; SSE is just its live tail."* Nothing is streamed that is not already a
durable row, and the stream is built out of the same `read_events` the poll uses.
Streaming changes **when** events are delivered, not what they are — which is the
property that makes a reconnect trivial rather than a synchronisation problem.

**Replay is the default, not a recovery path.** A stream always starts by sending
everything after the sequence number the client names, so "connect" and
"reconnect" are the same operation. There is no in-memory buffer to miss, no
window in which an event can be lost between the writer and a subscriber, and a
browser that was closed for an hour catches up by asking for what it has not
seen.

**`Last-Event-ID` is honoured**, because that is how the browser reconnects on
its own. `EventSource` reconnects automatically after a dropped connection and
sends the id of the last event it received; answering that correctly means
refresh, sleep, and a flaky network all recover without the page doing anything.
The explicit `?after=` wins when both are given, since a caller that names a
position means it.

**Same URL, negotiated.** 10.2 lists one events route, so this is the same path
the poll uses and `Accept: text/event-stream` is what selects the stream. A
second URL would have made the poll and the stream two contracts that could drift
apart, and the chat UI polls this route today.

**The stream ends when the run does.** A run reaches a terminal status and the
generator returns, so a client is not left holding a socket open on a run that
finished an hour ago. A heartbeat keeps the connection alive in between, because
proxies close idle connections and a silent stream is indistinguishable from a
dead one.

The tail is found by re-reading the table on an interval rather than by
`LISTEN`/`NOTIFY`. That is a deliberate first cut (**B-050**): it is one indexed
query per second per open stream against a table that is already written on every
step, and it keeps the delivery path identical to the replay path. Postgres
notification is the obvious upgrade and the seam is one function.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

from dataagent.runs import service
from dataagent.runs.events import RecordedEvent, read_events

__all__ = ["TERMINAL_STATUSES", "event_stream", "format_frame"]

#: How often the table is re-read for new rows. Fast enough that a step appears
#: to happen live, slow enough that an open stream is not a load problem.
POLL_SECONDS = 1.0

#: Sent when nothing has happened, so the connection is not mistaken for dead by
#: a proxy — an SSE comment, which clients ignore by specification.
HEARTBEAT_SECONDS = 15.0

#: How long a stream will follow a run that never ends. A run cannot outlive its
#: own wall-clock budget (4.4, 240s by default), so this is generous by design:
#: it exists to stop a socket living forever if a run is somehow stuck, not to
#: cut a working run short.
MAX_STREAM_SECONDS = 1800.0

#: A run in one of these will produce no further events, so the stream ends.
#: Mirrors `runs.service.TERMINAL_STATUSES`, imported rather than restated.
TERMINAL_STATUSES = service.TERMINAL_STATUSES


def format_frame(event: RecordedEvent) -> str:
    """One event as an SSE frame.

    ``id`` is the sequence number, which is what makes reconnection work: the
    browser sends it back as `Last-Event-ID` and the stream resumes from exactly
    there. ``event`` is 10.3's type, so a client can listen for one kind of thing
    without parsing every payload.
    """
    payload = json.dumps(
        {"seq": event.seq, "type": event.type, "payload": event.payload, "ts": event.ts.isoformat()}
    )
    return f"id: {event.seq}\nevent: {event.type}\ndata: {payload}\n\n"


def _resume_from(after: int, last_event_id: str | None) -> int:
    """Where to start: the explicit position, or the browser's own bookmark.

    ``?after=`` wins, because a caller that names a position means it. A
    malformed `Last-Event-ID` is ignored rather than refused — it arrives from a
    reconnecting browser, and the worst outcome of ignoring it is a replay the
    client already has, whereas refusing would break the reconnection entirely.
    """
    if after > 0 or not last_event_id:
        return max(0, after)
    try:
        return max(0, int(last_event_id.strip()))
    except ValueError:
        return 0


async def event_stream(
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    user_id: uuid.UUID | None,
    after: int = 0,
    last_event_id: str | None = None,
    poll_seconds: float = POLL_SECONDS,
    max_seconds: float = MAX_STREAM_SECONDS,
) -> AsyncIterator[str]:
    """Everything since ``after``, then everything as it happens, then stop.

    Ownership is checked **once, before the first frame**, by the same
    `list_events` the poll uses — so a caller who may not read this run gets the
    404 rather than an empty stream, and the check cannot drift from the poll's.

    The generator returns when the run is terminal *and* its remaining events
    have been sent. Ordering matters: the status is read **before** the final
    read of the table, so an event written between the two is still delivered.
    Reading it after would drop the last event of every run.
    """
    # Raises `NotFoundError` for a run that is not this caller's, which the route
    # turns into a 404 before the response starts streaming.
    seq = _resume_from(after, last_event_id)
    backlog = await service.list_events(org_id=org_id, run_id=run_id, user_id=user_id, after=seq)
    for event in backlog:
        seq = event.seq
        yield format_frame(event)

    loop = asyncio.get_running_loop()
    started = loop.time()
    quiet_since = started

    while True:
        view = await service.get_run(org_id=org_id, run_id=run_id, user_id=user_id)
        finished = view.status in TERMINAL_STATUSES

        # Read *after* asking whether the run finished, so an event written
        # between the two reads is still sent. The other order silently drops
        # the last event of every run — usually `run_finished`, which is the one
        # a client is waiting for.
        fresh = await read_events(org_id=org_id, run_id=run_id, after=seq)
        for event in fresh:
            seq = event.seq
            yield format_frame(event)
        if fresh:
            quiet_since = loop.time()

        if finished:
            return

        now = loop.time()
        if now - started > max_seconds:
            # A stream that has followed a run for half an hour is following a
            # run that is not coming back. Say so rather than holding the socket.
            yield ": stream timed out\n\n"
            return
        if now - quiet_since >= HEARTBEAT_SECONDS:
            yield ": keep-alive\n\n"
            quiet_since = now

        await asyncio.sleep(poll_seconds)
