"""The whole product, over HTTP, with nothing stubbed but the model.

Architecture M7 and plan WP7.3. Every other test in this repository holds one
seam still and checks the thing beside it; this one holds nothing still except
the provider, and walks the path a person walks: **a question goes in over HTTP
and a cited answer comes back, and the citation opens.**

What is real here is the point. A real conversation naming a real data source, a
real catalog discovered from a real database, the real validator, the real
executor against real rows, real ``query_executions`` and ``audit_log`` writes,
and the real routes in front of all of it. The FakeLLM is the only substitution,
and it is not a convenience: **no test may call a real model** (B-040), so the
model is scripted and CI needs no key at all.

The two scripts are written the way the real calls behave. The planner's is
static, because a planner sees the question and the catalog and nothing else. The
composer's is a **callable** that reads the execution id out of its own prompt
and cites it — because that is exactly what a real composing call does, and a
static script could not do it at all. Scripting the citation as a constant would
prove the plumbing while quietly assuming away the one property architecture 4.2
rests on: that the id a model cites is the id of a row that exists.

The question is a date-ranged count — the shape of the gate's "how many orders
were placed in July 2026?" against a database CI actually has. The gate demo
itself runs against the seeded pizza database in a browser; this proves the same
path on every commit.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from dataagent.agent import scheduler
from dataagent.agent.critic import CriticOut
from dataagent.agent.loop import Reflection
from dataagent.agent.planner import Plan
from dataagent.agent.tools.base import ToolContext
from dataagent.agent.tools.finalize import ChartAsk, FinalizeIn
from dataagent.auth.jwt_validator import TokenValidator
from dataagent.auth.principal import Principal
from dataagent.config import Settings
from dataagent.llm.base import LLMRequest
from dataagent.llm.fake import FakeLLM
from dataagent.main import create_app
from dataagent.runs import routes as routes_module
from dataagent.tenancy.session import org_session
from llm_fixture import build_settings

#: One shop opened in 2021 — Northgate, on 2021-02-02 (``tests/customer_db.py``).
#: Written here as the answer a person would check by hand, because a test that
#: computed it the same way the query does would agree with a broken query.
SHOPS_OPENED_IN_2021 = 1

QUESTION = "How many shops opened in 2021?"

#: Never connected to — the second source exists only to make the count two,
#: which is what an unnamed conversation refuses on. Named away from "password"
#: so the hardcoded-credential lint does not flag a value that reaches no
#: database, exactly as `test_scheduler.py` does.
_THROWAWAY = "not-a-real-credential"

KNOWN_GOOD_SQL = (
    "SELECT count(*) AS shops_opened FROM shops "
    "WHERE opened_on >= '2021-01-01' AND opened_on < '2022-01-01'"
)


class _SubjectAsToken(TokenValidator):
    def __init__(self, user_id: uuid.UUID | None) -> None:
        self._user_id = user_id

    async def validate(self, token: str) -> Principal:
        return Principal(subject=f"sub-{self._user_id}", email="asker@example.com")


def _cite_what_actually_ran(request: LLMRequest) -> str:
    """Compose an answer citing the execution id in this very prompt.

    A real composing call is shown the tool result — which carries
    ``execution_id`` — and is asked to cite it. This does the same, so what the
    test proves is that a cited id resolves, rather than that a constant matches
    a constant.
    """
    prompt = "\n".join(message.content for message in request.messages)
    found = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", prompt)
    assert found is not None, f"the composing prompt carried no execution id:\n{prompt}"
    return FinalizeIn(
        answer=f"{SHOPS_OPENED_IN_2021} shop opened in 2021.",
        answered=True,
        supported_by=[found.group(1)],
        confidence="high",
    ).model_dump_json()


def _cite_and_chart(request: LLMRequest) -> str:
    """Compose, and ask for a chart of the result just cited (WP11.1).

    Written against the same prompt the real composer sees, for the reason
    `_cite_what_actually_ran` gives: what is proved is that an id the model found
    in its own context reaches the run's stored chart, not that two constants
    match.
    """
    prompt = "\n".join(message.content for message in request.messages)
    found = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", prompt)
    assert found is not None
    return FinalizeIn(
        answer=f"{SHOPS_OPENED_IN_2021} shop opened in 2021.",
        answered=True,
        supported_by=[found.group(1)],
        confidence="high",
        chart=ChartAsk(of=found.group(1), mark="bar", x="opened_year", y="shops"),
    ).model_dump_json()


@pytest.fixture
def scripted(fake_llm: FakeLLM) -> FakeLLM:
    fake_llm.script(
        Plan(
            sql=KNOWN_GOOD_SQL,
            purpose="Count shops whose opening date falls in 2021",
            answerable=True,
            reason="",
        ).model_dump_json(),
        role="sql",
    )
    # One reflection that ends the investigation: this question needs one query,
    # and the loop is not the thing under test here — the path from HTTP to a
    # cited answer is.
    fake_llm.script(
        Reflection(
            findings=[],
            open_questions=[],
            next_purpose="",
            done=True,
            rationale="the count answers it",
        ).model_dump_json(),
        role="plan",
    )
    fake_llm.script(_cite_what_actually_ran, role="compose")
    # A run now ends with a critic pass (WP9.1). Scripted explicitly rather
    # than defaulted, so a critic that stopped running would fail here.
    fake_llm.script(CriticOut(verdict="pass", reasons=[]).model_dump_json(), role="critic")
    return fake_llm


async def _ask_over_http(
    context: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, Any], list[object]]:
    """Create a conversation naming the source, ask, and hand back the task.

    ``schedule_run`` is wrapped rather than replaced, so the real scheduler runs
    with hermetic settings. The route calls ``get_settings()``, which reads the
    developer's own ``.env`` — without this seam the test reaches for a real
    provider, which it once did and was billed for (B-040).
    """
    scheduled: list[object] = []
    real_schedule = scheduler.schedule_run

    async def capture(**kwargs: object) -> object:
        task = await real_schedule(**{**kwargs, "settings": build_settings()})  # pyright: ignore[reportArgumentType]
        scheduled.append(task)
        return task

    monkeypatch.setattr(routes_module, "schedule_run", capture)
    app = create_app(settings=Settings(auth_mode="dev", env="ci", build_env="dev"))
    app.state.token_validator = _SubjectAsToken(context.actor_user_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        headers = {"Authorization": "Bearer asker"}
        created = await client.post(
            f"/v1/orgs/{context.org_id}/conversations",
            headers=headers,
            json={"data_source_id": str(context.data_source_id)},
        )
        assert created.status_code == 201, created.text
        conversation = created.json()
        assert conversation["data_source_id"] == str(context.data_source_id)

        accepted = await client.post(
            f"/v1/orgs/{context.org_id}/conversations/{conversation['id']}/messages",
            headers=headers,
            json={"content": QUESTION, "idempotency_key": uuid.uuid4().hex},
        )
        assert accepted.status_code == 202, accepted.text

    assert scheduled, "the route did not schedule a run"
    return accepted.json(), scheduled


async def _get(context: ToolContext, path: str) -> tuple[int, Any]:
    app = create_app(settings=Settings(auth_mode="dev", env="ci", build_env="dev"))
    app.state.token_validator = _SubjectAsToken(context.actor_user_id)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(path, headers={"Authorization": "Bearer asker"})
    return response.status_code, response.json()


async def test_a_question_is_answered_over_http_and_the_citation_opens(
    context: ToolContext, scripted: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The M7 criterion, end to end, in the order a user experiences it."""
    accepted, scheduled = await _ask_over_http(context, monkeypatch)
    await scheduled[0]  # pyright: ignore[reportGeneralTypeIssues]
    run_id = accepted["run_id"]

    status, run = await _get(context, f"/v1/orgs/{context.org_id}/runs/{run_id}")
    assert status == 200
    assert run["status"] == "completed"
    assert run["question"] == QUESTION
    assert run["answer"] == f"{SHOPS_OPENED_IN_2021} shop opened in 2021."

    # A finding, carrying the citation. Not decoration: architecture 4.2 makes
    # "a claim points at the row that supports it" the spine of the whole trust
    # model, and an answer with no finding has claimed something unsupported.
    assert len(run["findings"]) == 1
    support = run["findings"][0]["support"]
    assert len(support) == 1, "the answer cited nothing, or cited more than it ran"

    # And the citation opens — which is the half B-034 existed to build. Before
    # this route, `support` was a list of ids nobody could resolve.
    status, evidence = await _get(
        context, f"/v1/orgs/{context.org_id}/runs/{run_id}/executions/{support[0]}"
    )
    assert status == 200, evidence
    assert evidence["status"] == "ok"
    assert evidence["tables"] == ["public.shops"]
    assert "shops" in evidence["sql"]


async def test_a_chart_that_cannot_be_drawn_says_so_on_the_run(
    context: ToolContext, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The property the whole chart design turns on** (WP11.1).

    This question's result is a single count, so a bar chart of it has no
    horizontal axis to use — and the model asked for one anyway, which is exactly
    what a real one does. What must not happen is silence: a picture that fails
    to appear with no reason given is indistinguishable from a broken page, which
    is B-087's lesson carried into charts.

    Asserted on the **run** rather than on the tool, because that is where the
    answer card reads it from, and because the refusal has to survive the whole
    path — tool, runner, database — to be worth anything.
    """
    fake_llm.script(
        Plan(
            sql=KNOWN_GOOD_SQL,
            purpose="Count shops whose opening date falls in 2021",
            answerable=True,
            reason="",
        ).model_dump_json(),
        role="sql",
    )
    fake_llm.script(
        Reflection(
            findings=[],
            open_questions=[],
            next_purpose="",
            done=True,
            rationale="the count answers it",
        ).model_dump_json(),
        role="plan",
    )
    fake_llm.script(_cite_and_chart, role="compose")
    fake_llm.script(CriticOut(verdict="pass", reasons=[]).model_dump_json(), role="critic")

    accepted, scheduled = await _ask_over_http(context, monkeypatch)
    await scheduled[0]  # pyright: ignore[reportGeneralTypeIssues]

    status, run = await _get(context, f"/v1/orgs/{context.org_id}/runs/{accepted['run_id']}")

    assert status == 200
    chart = run["chart"]
    assert chart is not None, "a chart was asked for and the run recorded nothing about it"
    assert chart.get("spec") is None
    # Names the column that is missing, because the reader's next question is
    # which one — the same reason B-092 made a card's values carry their share.
    assert "opened_year" in chart["declined"]
    assert chart["code"] == "no_such_column"

    # And the limitations are untouched: a missing picture says nothing about
    # whether the answer is true, so it does not go in the list that is about
    # exactly that (the owner's call, and B-079's argument from the other side).
    assert not any("chart" in note.lower() for note in run["limitations"])


async def test_the_answer_is_the_database_s_own_number(
    context: ToolContext, scripted: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The DAL really ran, against real rows, and the evidence says so.

    The model was scripted to *say* one shop. This asserts the database agreed —
    so a run whose SQL never reached an engine, or reached it and matched
    nothing, fails here rather than passing on the strength of a scripted
    sentence. That distinction is the whole difference between an e2e and a
    mock.
    """
    accepted, scheduled = await _ask_over_http(context, monkeypatch)
    await scheduled[0]  # pyright: ignore[reportGeneralTypeIssues]

    _, run = await _get(context, f"/v1/orgs/{context.org_id}/runs/{accepted['run_id']}")
    citation = run["findings"][0]["support"][0]
    _, evidence = await _get(
        context, f"/v1/orgs/{context.org_id}/runs/{accepted['run_id']}/executions/{citation}"
    )

    assert evidence["row_count"] == 1, "a count returns one row"
    assert evidence["sample_rows"] == [[SHOPS_OPENED_IN_2021]]
    assert evidence["columns"] == ["shops_opened"]
    assert evidence["duration_ms"] is not None, "nothing timed it, so nothing ran it"


async def test_the_whole_path_left_a_record_at_every_step(
    context: ToolContext, scripted: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    """message → run → execution → audit, joined up, in one test (plan WP7.3).

    Each of these is written by a different part of the system, and each was
    tested in isolation when it was built. What is proved here is that they
    point at *each other*: a support engineer handed only the question can reach
    the audit row, and an auditor handed only the audit row can reach the
    question.
    """
    accepted, scheduled = await _ask_over_http(context, monkeypatch)
    await scheduled[0]  # pyright: ignore[reportGeneralTypeIssues]
    run_id = uuid.UUID(accepted["run_id"])

    async with org_session(context.org_id) as session:
        message = (
            await session.execute(
                text(
                    "SELECT run_id FROM messages WHERE id = :id AND role = 'user'",
                ),
                {"id": uuid.UUID(accepted["message_id"])},
            )
        ).scalar_one()
        assert message == run_id, "the question does not name the run that answered it"

        answered = (
            await session.execute(
                text("SELECT count(*) FROM messages WHERE run_id = :r AND role = 'assistant'"),
                {"r": run_id},
            )
        ).scalar_one()
        assert answered == 1, "the reply is a message in the conversation, like any other"

        execution_id, sql_hash = (
            await session.execute(
                text(
                    "SELECT id, sql_hash FROM query_executions WHERE run_id = :r AND status = 'ok'"
                ),
                {"r": run_id},
            )
        ).one()

        # The audit row is written in the same transaction as the execution and
        # cannot be rewritten afterwards — the grant lock `audit_log` has carried
        # since revision 0002. It is what makes the chain evidence rather than a
        # convention.
        action, object_id, details = (
            await session.execute(
                text(
                    "SELECT action, object_id, details FROM audit_log "
                    "WHERE object_id = :o AND action = 'dal.query_executed'"
                ),
                {"o": str(execution_id)},
            )
        ).one()
        assert action == "dal.query_executed"
        assert object_id == str(execution_id)
        assert details["sql_hash"] == sql_hash, "the audit row describes a different statement"

        # And the trace, which is what the user is shown as proof of how the
        # answer was reached (architecture 10.3).
        traced = {
            row[0]
            for row in (
                await session.execute(
                    text("SELECT type FROM agent_events WHERE run_id = :r"), {"r": run_id}
                )
            ).all()
        }
    assert {"run_started", "query_executed", "answer_composed", "run_finished"} <= traced


async def test_a_conversation_that_names_no_source_refuses_when_there_are_two(
    context: ToolContext, scripted: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal D-022 kept, rather than replaced with a tie-break.

    Naming a source on the conversation is what *closed* the ambiguity; it did
    not make guessing safe. A thread that names none, in an organization with a
    choice, still refuses and still says what the choices are — and it refuses
    as a completed run with a readable reply, not as a failure.
    """
    assert context.actor_user_id is not None
    from dataagent.datasources import service as datasources

    await datasources.create_data_source(
        org_id=context.org_id,
        actor_user_id=context.actor_user_id,
        name="Second warehouse",
        engine="pg",
        host="127.0.0.1",
        port=1,
        database="unused",
        username="unused",
        password=_THROWAWAY,
        tls_mode="prefer",
    )

    scheduled: list[object] = []
    real_schedule = scheduler.schedule_run

    async def capture(**kwargs: object) -> object:
        task = await real_schedule(**{**kwargs, "settings": build_settings()})  # pyright: ignore[reportArgumentType]
        scheduled.append(task)
        return task

    monkeypatch.setattr(routes_module, "schedule_run", capture)
    app = create_app(settings=Settings(auth_mode="dev", env="ci", build_env="dev"))
    app.state.token_validator = _SubjectAsToken(context.actor_user_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        headers = {"Authorization": "Bearer asker"}
        created = await client.post(
            f"/v1/orgs/{context.org_id}/conversations", headers=headers, json={}
        )
        accepted = await client.post(
            f"/v1/orgs/{context.org_id}/conversations/{created.json()['id']}/messages",
            headers=headers,
            json={"content": QUESTION, "idempotency_key": uuid.uuid4().hex},
        )

    assert accepted.status_code == 202
    await scheduled[0]  # pyright: ignore[reportGeneralTypeIssues]

    _, run = await _get(context, f"/v1/orgs/{context.org_id}/runs/{accepted.json()['run_id']}")
    assert run["status"] == "completed", "a refusal is an ending, not a failure"
    assert "more than one data source" in run["answer"]
    assert "Second warehouse" in run["answer"], "the refusal must name the choices"
    assert run["findings"] == [], "nothing was concluded, so nothing is claimed"
