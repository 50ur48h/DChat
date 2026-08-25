"""What actually runs a queued run (architecture 0.2.4 and Part 8.2).

An **in-process async task**, state checkpointed to Postgres, no queue —
0.2.4 verbatim, and Part 8.2's table lists Service Bus as ✗ with "in-process
tasks + checkpoints" as the V1 answer. The promotion path to a worker in V1.5 is
free because `runner.execute_run` takes ids and knows nothing about requests;
this module is the only place that assumes a process.

Four things it is responsible for, and each has a failure it exists to prevent.

**Choosing the data source, or refusing to.** The conversation's own choice wins
(D-022): a thread names the database it is about, and every question in it goes
there. When it named none, exactly one registered source is used, and anything
else refuses and names the choices. A silently wrong database is the worst
failure this product can produce — the answer looks right, cites a real
execution, and is about someone else's data. So the fallback stayed a refusal
rather than becoming a tie-break: what closed the gap is a caller *saying* which
database it means, which is the only thing that could.

**Bounding concurrency.** A per-organization semaphore, default 2 per
architecture 8.4. Without it, sending questions is a way to spawn unbounded tasks
inside the API process, which is a denial of service with a friendly UI.

**Keeping a reference to the task.** `asyncio` holds only a weak reference to a
bare task, so a run can be garbage-collected mid-flight and simply stop, leaving
a row in `running` and no explanation. The set here is not tidiness.

**Reconciling orphans at startup.** A redeploy kills in-flight runs; 0.2.4 names
this as the honest weakness of the in-process model and asks for the mitigation.
Anything left `queued`, `running` or `validating` becomes **`interrupted`** — a
status that already exists for exactly this — and the trace says a restart
happened rather than implying the question was at fault.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Sequence

from sqlalchemy import text

from dataagent.agent.runner import execute_run
from dataagent.config import Settings
from dataagent.datasources import service as datasources
from dataagent.orgs import service as orgs_service
from dataagent.runs import service as runs
from dataagent.tenancy.session import app_session

__all__ = [
    "AmbiguousDataSourceError",
    "in_flight",
    "resolve_data_source",
    "schedule_run",
    "sweep_orphaned_runs",
]

logger = logging.getLogger(__name__)

#: Concurrent runs per organization (architecture 8.4). One tenant asking a lot
#: of questions must not starve another; this is the crudest thing that holds
#: that line, and Phase 8's budgets refine it.
MAX_CONCURRENT_RUNS_PER_ORG = 2

#: What a run is told when the service restarted underneath it. Worded so the
#: person reading it learns two things: nothing was wrong with their question,
#: and asking again is the fix.
INTERRUPTED_REASON = (
    "The service restarted while this run was in progress, so it was stopped "
    "part-way. Nothing was wrong with the question — ask it again to retry."
)

#: Statuses a run cannot still be in after a restart, because the only thing
#: that could advance them was a task inside a process that no longer exists.
ORPHANABLE = ("queued", "running", "validating")

_semaphores: dict[uuid.UUID, asyncio.Semaphore] = {}
#: Strong references to in-flight tasks. asyncio keeps only a weak one, so
#: without this a run can vanish mid-flight with no error and no ending.
_running: set[asyncio.Task[None]] = set()


class AmbiguousDataSourceError(Exception):
    """There is no single obvious data source to answer against.

    Carries the candidates so the refusal can name them: "pick one" is only
    useful advice if the reader is told what there is to pick from.
    """

    def __init__(self, message: str, *, candidates: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.candidates = tuple(candidates)


async def resolve_data_source(org_id: uuid.UUID) -> uuid.UUID:
    """The one data source to answer from, or a refusal that says why not.

    Reached only when the conversation named none (D-022). Deliberately no
    default and no ranking even here: with two databases registered, the
    "obvious" choice is obvious only to whoever wrote the tie-break — and being
    wrong produces a confident, well-cited answer about the wrong company's
    data. The refusal names the choices, because "pick one" is useful advice
    only to a reader told what there is to pick from.

    **The organization's own choice comes first (D-045), and it is not a
    tie-break.** An Admin naming the database this organization asks questions of
    is a person deciding, which is exactly what the refusals below exist to
    demand — the platform still never guesses. When no Admin has chosen, both
    refusals stand word for word, and every organization from before revision
    0031 reaches them exactly as it did.

    Conversations created after D-045 are stamped at creation, so this path is
    the one for threads that predate the choice — and for any caller that reaches
    a run without one.
    """
    chosen = await orgs_service.active_data_source(org_id)
    if chosen.data_source_id is not None:
        return chosen.data_source_id

    sources = await datasources.list_data_sources(org_id)
    if not sources:
        raise AmbiguousDataSourceError(
            "No data source is registered for this organization, so there is "
            "nothing to query. An Admin can add one on the data sources screen."
        )
    if len(sources) > 1:
        names = [source.name for source in sources]
        raise AmbiguousDataSourceError(
            "This organization has more than one data source and this conversation "
            f"is not tied to one of them: {', '.join(names)}. Start a conversation "
            "that names the database you mean.",
            candidates=names,
        )
    return sources[0].id


async def schedule_run(
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    actor_user_id: uuid.UUID | None = None,
    role: str = "reader",
    data_source_id: uuid.UUID | None = None,
    settings: Settings | None = None,
) -> asyncio.Task[None]:
    """Start a queued run in the background and return immediately.

    The caller — a route — gets its task back so a test can await it. Production
    callers ignore it: the run reports itself through `agent_runs` and
    `agent_events`, which is the only channel that survives the request.
    """
    task = asyncio.create_task(
        _guarded(
            org_id=org_id,
            run_id=run_id,
            actor_user_id=actor_user_id,
            role=role,
            data_source_id=data_source_id,
            settings=settings,
        )
    )
    _running.add(task)
    task.add_done_callback(_running.discard)
    return task


def in_flight() -> frozenset[asyncio.Task[None]]:
    """The runs this process is currently executing.

    Public so the reference-keeping can be asserted without reaching into the
    module — and useful in its own right for a later health endpoint that wants
    to say how busy this container is.
    """
    return frozenset(_running)


async def _guarded(
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    role: str,
    data_source_id: uuid.UUID | None,
    settings: Settings | None,
) -> None:
    """Resolve the source, wait for a slot, run — and never raise into the loop.

    An exception escaping a background task is logged by asyncio and otherwise
    invisible: no response carries it and no user sees it. So every ending is
    written to the run before this returns.
    """
    try:
        resolved = data_source_id or await resolve_data_source(org_id)
    except AmbiguousDataSourceError as choice:
        await _refuse(org_id=org_id, run_id=run_id, reason=str(choice))
        return
    except Exception:
        logger.exception("could not resolve a data source for run %s", run_id)
        await _refuse(
            org_id=org_id,
            run_id=run_id,
            reason="I could not work out which database to query.",
        )
        return

    async with _semaphore_for(org_id):
        try:
            await execute_run(
                org_id=org_id,
                run_id=run_id,
                data_source_id=resolved,
                actor_user_id=actor_user_id,
                role=role,
                settings=settings,
            )
        except Exception:
            # `execute_run` already ends the run on every path it knows about.
            # This is the belt for the paths it does not — a failure to write
            # the ending itself — and it is logged rather than swallowed.
            logger.exception("run %s ended abnormally", run_id)


async def _refuse(*, org_id: uuid.UUID, run_id: uuid.UUID, reason: str) -> None:
    """End a run that never got as far as the agent.

    Still an ending with an answer, not a silent disappearance: the question was
    asked, so it gets a reply the person can read, and the run leaves `queued`.
    """
    await runs.transition(org_id=org_id, run_id=run_id, status="running")
    await runs.record_answer(org_id=org_id, run_id=run_id, content=reason)
    await runs.transition(
        org_id=org_id,
        run_id=run_id,
        status="completed",
        # **The run never reached the agent**, so it stands behind nothing and the
        # whole question is what went unanswered — `refused`, in D-044's
        # vocabulary. Written on the column as well as in the trace, because a
        # screen cannot read the trace per run (B-133).
        state="refused",
        unanswered="this question",
        totals={"llm_calls": 0, "queries": 0, "state": "refused"},
    )


def _semaphore_for(org_id: uuid.UUID) -> asyncio.Semaphore:
    semaphore = _semaphores.get(org_id)
    if semaphore is None:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_RUNS_PER_ORG)
        _semaphores[org_id] = semaphore
    return semaphore


async def sweep_orphaned_runs() -> int:
    """Mark runs that a restart stopped, and return how many.

    Runs at startup, across every organization, so it reads through the system
    session — there is no single ``app.org_id`` that could scope "every run left
    hanging". Each transition is then made inside its own organization's session,
    so the event and the row are written under the same policies as any other.

    ``interrupted``, never ``failed``: nothing was wrong with the question, and a
    trace that said otherwise would send somebody looking for a bug in their SQL.
    """
    # **Spans every organization by definition** — "every run left hanging" is
    # not a question any single `app.org_id` can answer. One audited function
    # instead of the owner connection this used to take (B-123). The statuses are
    # fixed inside the function; `tests/db/test_security_definer.py` asserts its
    # list and `ORPHANABLE` still agree.
    async with app_session() as session:
        rows = (await session.execute(text("SELECT run_id, org_id FROM ops_orphaned_runs()"))).all()

    swept = 0
    for run_id, org_id in rows:
        try:
            await runs.transition(
                org_id=org_id,
                run_id=run_id,
                status="interrupted",
                failure_reason=INTERRUPTED_REASON,
                totals={"interrupted_by": "restart"},
            )
        except Exception:
            # One unreconcilable run must not stop the rest being reconciled.
            logger.exception("could not mark run %s as interrupted", run_id)
            continue
        swept += 1

    if swept:
        logger.warning("marked %d run(s) interrupted after a restart", swept)
    return swept
