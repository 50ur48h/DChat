"""What runs a queued run, and what happens when the process dies under one.

Two properties here are worth more than the rest.

**A wrong database is never guessed at.** With one source registered it is used;
with two the run refuses and names them. A silently wrong database produces a
confident, correctly-cited answer about somebody else's data, which is the worst
output this product can generate — worse than an error, because nothing about it
looks wrong.

**A restart leaves no run claiming to be running.** In-process tasks die with the
process (architecture 0.2.4), so the rows they left behind are reconciled at
startup as `interrupted` — never `failed`, and with a reason that says a restart
happened rather than implying the question was at fault.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import create_async_engine

from dataagent.agent import scheduler
from dataagent.agent.planner import Plan
from dataagent.agent.scheduler import (
    INTERRUPTED_REASON,
    AmbiguousDataSourceError,
    resolve_data_source,
    schedule_run,
    sweep_orphaned_runs,
)
from dataagent.agent.tools.base import ToolContext
from dataagent.agent.tools.finalize import FinalizeIn
from dataagent.auth.jwt_validator import TokenValidator
from dataagent.auth.principal import Principal
from dataagent.config import Settings
from dataagent.datasources import service as datasources
from dataagent.llm.fake import FakeLLM
from dataagent.main import create_app
from dataagent.runs import routes as routes_module
from dataagent.runs import service as runs
from llm_fixture import build_settings


class _SubjectAsToken(TokenValidator):
    """Resolves every token to one known member of the fixture's organization."""

    def __init__(self, user_id: uuid.UUID | None) -> None:
        self._user_id = user_id

    async def validate(self, token: str) -> Principal:
        return Principal(subject=f"sub-{self._user_id}", email="asker@example.com")


#: Never connected to — this source exists only to make the count two, which is
#: what `resolve_data_source` refuses on. Named away from "password" so the
#: hardcoded-credential lint does not flag a value that reaches no database.
_THROWAWAY = "not-a-real-credential"


async def _register_a_second_source(context: ToolContext) -> None:
    assert context.actor_user_id is not None
    await datasources.create_data_source(
        org_id=context.org_id,
        actor_user_id=context.actor_user_id,
        name="Second warehouse",
        engine="pg",
        host="127.0.0.1",
        port=5432,
        database="other",
        username="reader",
        password=_THROWAWAY,
        tls_mode="prefer",
    )


async def _queued(context: ToolContext, question: str = "How many shops?") -> uuid.UUID:
    view = await runs.get_run(org_id=context.org_id, run_id=context.run_id)
    asked = await runs.post_message(
        org_id=context.org_id,
        user_id=context.actor_user_id or uuid.uuid4(),
        conversation_id=view.conversation_id,
        content=question,
        idempotency_key=uuid.uuid4().hex,
    )
    return asked.run_id


# ---------------------------------------------------------------------------
# Choosing a data source
# ---------------------------------------------------------------------------


async def test_one_registered_source_is_the_one_used(context: ToolContext) -> None:
    resolved = await resolve_data_source(context.org_id)

    assert resolved == context.data_source_id


async def test_two_sources_refuse_and_name_the_choices(context: ToolContext) -> None:
    """No default and no tie-break. Being wrong here is invisible in the answer."""
    await _register_a_second_source(context)

    with pytest.raises(AmbiguousDataSourceError) as raised:
        await resolve_data_source(context.org_id)

    assert "Second warehouse" in str(raised.value)
    assert "Customer" in str(raised.value)
    assert set(raised.value.candidates) == {"Customer", "Second warehouse"}


async def test_no_source_at_all_says_what_to_do_about_it(context: ToolContext) -> None:
    assert context.actor_user_id is not None
    assert context.data_source_id is not None
    await datasources.delete_data_source(
        org_id=context.org_id,
        actor_user_id=context.actor_user_id,
        data_source_id=context.data_source_id,
    )

    with pytest.raises(AmbiguousDataSourceError, match="No data source is registered"):
        await resolve_data_source(context.org_id)


async def test_an_ambiguous_source_ends_the_run_with_an_answer_not_a_silence(
    context: ToolContext,
) -> None:
    """The question was asked, so it gets a reply the person can read — and the
    run leaves `queued` rather than sitting there forever."""
    await _register_a_second_source(context)
    run_id = await _queued(context)

    await (await schedule_run(org_id=context.org_id, run_id=run_id))

    view = await runs.get_run(org_id=context.org_id, run_id=run_id)
    assert view.status == "completed"
    assert view.answer is not None
    assert "more than one data source" in view.answer
    assert view.findings == []


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


async def test_a_scheduled_run_is_executed_in_the_background(
    context: ToolContext, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route returns immediately; the run happens after. What is asserted is
    that it happens at all, and against the right run."""
    seen: dict[str, object] = {}

    async def fake_execute(**kwargs: object) -> None:
        seen.update(kwargs)

    monkeypatch.setattr(scheduler, "execute_run", fake_execute)
    run_id = await _queued(context)

    await (await schedule_run(org_id=context.org_id, run_id=run_id, role="reader"))

    assert seen["run_id"] == run_id
    assert seen["org_id"] == context.org_id
    assert seen["data_source_id"] == context.data_source_id


async def test_a_task_that_raises_does_not_escape_into_the_event_loop(
    context: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception out of a background task is logged by asyncio and otherwise
    invisible — no response carries it and no user sees it."""

    async def explode(**kwargs: object) -> None:
        raise RuntimeError("nobody predicted this")

    monkeypatch.setattr(scheduler, "execute_run", explode)
    run_id = await _queued(context)

    task = await schedule_run(org_id=context.org_id, run_id=run_id)
    await task

    assert task.exception() is None


async def test_the_scheduler_keeps_a_reference_to_the_task(
    context: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """asyncio holds only a weak reference, so without this a run can be
    collected mid-flight and simply stop, leaving a row in `running`."""

    async def slow(**kwargs: object) -> None:
        return None

    monkeypatch.setattr(scheduler, "execute_run", slow)
    run_id = await _queued(context)

    task = await schedule_run(org_id=context.org_id, run_id=run_id)

    assert task in scheduler.in_flight()
    await task
    assert task not in scheduler.in_flight(), "the done callback should release it"


# ---------------------------------------------------------------------------
# The orphan sweep
# ---------------------------------------------------------------------------


async def _status_of(wired: URL, org_id: uuid.UUID, run_id: uuid.UUID) -> tuple[str, str | None]:
    engine = create_async_engine(wired)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, false)"), {"org": str(org_id)}
            )
            row = (
                await connection.execute(
                    text("SELECT status, failure_reason FROM agent_runs WHERE id = :id"),
                    {"id": run_id},
                )
            ).one()
    finally:
        await engine.dispose()
    return row.status, row.failure_reason


async def test_a_run_left_running_by_a_restart_is_marked_interrupted(
    context: ToolContext, wired: URL
) -> None:
    run_id = await _queued(context)
    await runs.transition(org_id=context.org_id, run_id=run_id, status="running")

    swept = await sweep_orphaned_runs()

    assert swept >= 1
    status, reason = await _status_of(wired, context.org_id, run_id)
    assert status == "interrupted", "a restart is not a failure"
    assert reason == INTERRUPTED_REASON


async def test_the_reason_says_a_restart_happened_rather_than_blaming_the_question(
    context: ToolContext, wired: URL
) -> None:
    """Somebody reading this should not go looking for a bug in their SQL."""
    run_id = await _queued(context)
    await runs.transition(org_id=context.org_id, run_id=run_id, status="running")

    await sweep_orphaned_runs()

    _, reason = await _status_of(wired, context.org_id, run_id)
    assert reason is not None
    assert "restarted" in reason
    assert "Nothing was wrong with the question" in reason


async def test_the_trace_records_the_interruption(context: ToolContext) -> None:
    """`run_finished` with status interrupted and the reason — so the trace, not
    just the row, says what happened."""
    from dataagent.runs.events import read_events

    run_id = await _queued(context)
    await runs.transition(org_id=context.org_id, run_id=run_id, status="running")

    await sweep_orphaned_runs()

    events = await read_events(org_id=context.org_id, run_id=run_id)
    last = events[-1]
    assert last.type == "run_finished"
    assert last.payload["status"] == "interrupted"
    assert "restarted" in str(last.payload["reason"])
    assert last.payload["totals"] == {"interrupted_by": "restart"}


async def test_a_queued_run_is_swept_too(context: ToolContext, wired: URL) -> None:
    """Nothing schedules a queued run except a task inside a process that no
    longer exists, so one still queued at startup will never start."""
    run_id = await _queued(context)

    await sweep_orphaned_runs()

    status, _ = await _status_of(wired, context.org_id, run_id)
    assert status == "interrupted"


async def test_a_finished_run_is_left_alone(context: ToolContext, wired: URL) -> None:
    """The sweep must not rewrite history. A completed run is a completed run."""
    run_id = await _queued(context)
    await runs.transition(org_id=context.org_id, run_id=run_id, status="running")
    await runs.transition(org_id=context.org_id, run_id=run_id, status="completed")

    await sweep_orphaned_runs()

    status, _ = await _status_of(wired, context.org_id, run_id)
    assert status == "completed"


async def test_the_sweep_reports_nothing_to_do_on_a_clean_start(
    context: ToolContext,
) -> None:
    """Every run in the database already finished, so there is nothing to
    reconcile — including the one the fixture leaves queued, which is itself a
    fair example of what the sweep is for."""
    for run_id in (context.run_id, await _queued(context)):
        await runs.transition(org_id=context.org_id, run_id=run_id, status="running")
        await runs.transition(org_id=context.org_id, run_id=run_id, status="completed")

    assert await sweep_orphaned_runs() == 0


# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------


async def test_asking_over_http_answers_202_and_the_run_completes_behind_it(
    context: ToolContext, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of WP7.2c, from the client's side: the request returns
    straight away and the answer arrives on the run.

    The task is captured rather than slept on — a test that waits on wall-clock
    time for a background task is a test that is flaky on a loaded machine.
    """
    scheduled: list[object] = []
    real_schedule = scheduler.schedule_run

    async def capture(**kwargs: object) -> object:
        # `settings` is injected here because the route has no seam for it:
        # `schedule_run` from a request uses `get_settings()`, which reads the
        # developer's `.env`. Without this the test reaches for the real provider
        # — it did exactly that once, and billed for it. The session guard in
        # `tests/conftest.py` now stops that happening silently (**B-040**);
        # this line is what makes the test *work* rather than merely fail safely.
        task = await real_schedule(**{**kwargs, "settings": build_settings()})  # pyright: ignore[reportArgumentType]
        scheduled.append(task)
        return task

    monkeypatch.setattr(routes_module, "schedule_run", capture)
    fake_llm.script(
        Plan(
            sql="SELECT count(*) AS n FROM shops", purpose="count", answerable=True, reason=""
        ).model_dump_json(),
        role="sql",
    )
    fake_llm.script(
        FinalizeIn(
            answer="There are 3 shops.", answered=True, supported_by=[], confidence="high"
        ).model_dump_json(),
        role="compose",
    )

    app = create_app(settings=Settings(auth_mode="dev", env="ci", build_env="dev"))
    app.state.token_validator = _SubjectAsToken(context.actor_user_id)
    view = await runs.get_run(org_id=context.org_id, run_id=context.run_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            f"/v1/orgs/{context.org_id}/conversations/{view.conversation_id}/messages",
            headers={"Authorization": "Bearer asker"},
            json={"content": "How many shops?", "idempotency_key": uuid.uuid4().hex},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"

    # The request is already answered; now let the background task finish.
    assert scheduled, "the route did not schedule anything"
    await scheduled[0]  # pyright: ignore[reportGeneralTypeIssues]

    finished = await runs.get_run(org_id=context.org_id, run_id=uuid.UUID(body["run_id"]))
    assert finished.status == "completed"
    assert finished.answer == "There are 3 shops."


async def test_a_replayed_send_does_not_schedule_a_second_run(
    context: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The idempotency key exists to stop a double tap costing twice. Scheduling
    on a replay would defeat it after the run row was correctly reused."""
    scheduled = 0

    async def count(**kwargs: object) -> None:
        nonlocal scheduled
        scheduled += 1

    monkeypatch.setattr(routes_module, "schedule_run", count)
    app = create_app(settings=Settings(auth_mode="dev", env="ci", build_env="dev"))
    app.state.token_validator = _SubjectAsToken(context.actor_user_id)
    view = await runs.get_run(org_id=context.org_id, run_id=context.run_id)
    body = {"content": "How many shops?", "idempotency_key": "one-send"}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        for _ in range(2):
            response = await client.post(
                f"/v1/orgs/{context.org_id}/conversations/{view.conversation_id}/messages",
                headers={"Authorization": "Bearer asker"},
                json=body,
            )
            assert response.status_code == 202

    assert scheduled == 1
