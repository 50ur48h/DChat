"""The check a model cannot talk its way past (architecture 4.3).

The flagship is the M8 gate's own demo: **"which menu items sell best?" against a
schema with no link between the two tables must refuse, name the missing link,
and run no query at all**. It matters more than it looks. A join between
unrelated tables does not error — it returns a cartesian product — so the
alternative to refusing is a confident, correctly-cited answer computed from
nonsense, which is indistinguishable from a real one.

The graph tests are pure and need nothing; the loop tests go through
`execute_run` against the real fixture, because "no query was sent" is a claim
about `query_executions` and only the database can settle it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import create_async_engine

from dataagent.agent.capability import CapabilityGap, JoinGraph, load_join_graph
from dataagent.agent.loop import Reflection
from dataagent.agent.planner import Plan
from dataagent.agent.tools.base import ToolContext
from dataagent.agent.tools.finalize import FinalizeIn
from dataagent.llm.fake import FakeLLM
from dataagent.runs import service as runs
from dataagent.runs.events import read_events
from llm_fixture import build_settings


def _graph(**edges: list[str]) -> JoinGraph:
    return JoinGraph(edges={name: frozenset(links) for name, links in edges.items()})


# ---------------------------------------------------------------------------
# Reachability
# ---------------------------------------------------------------------------


def test_a_declared_foreign_key_joins_both_ways() -> None:
    """A foreign key points one way; a join works either way. Treating the graph
    as directed would refuse half the questions it should allow."""
    graph = _graph(orders=["customers"], customers=["orders"])

    assert graph.path("orders", "customers") == ("orders", "customers")
    assert graph.path("customers", "orders") == ("customers", "orders")


def test_a_multi_hop_path_is_found_and_reported_at_its_shortest() -> None:
    """`payments → orders → customers` is a real answer to "can these be
    combined", and the hop count is what tells a person whether the join is
    sensible."""
    graph = _graph(payments=["orders"], orders=["payments", "customers"], customers=["orders"])

    assert graph.path("payments", "customers") == ("payments", "orders", "customers")


def test_an_isolated_table_reaches_nothing() -> None:
    graph = _graph(orders=["customers"], customers=["orders"], menu_items=[])

    assert graph.path("menu_items", "orders") is None
    assert graph.check(["menu_items", "orders"]).answerable is False


def test_one_table_is_always_answerable() -> None:
    """There is nothing to join, so there is nothing to refuse."""
    graph = _graph(menu_items=[])

    assert graph.check(["menu_items"]).answerable is True
    assert graph.check([]).answerable is True


def test_a_schema_prefix_is_the_same_table() -> None:
    """A statement says `FROM orders` as often as `FROM public.orders`, and a
    check that missed a gap over a prefix would be worse than no check."""
    graph = _graph(orders=["customers"], customers=["orders"], menu_items=[])

    assert graph.check(["public.orders", '"public"."customers"']).answerable is True
    assert graph.check(["public.menu_items", "public.orders"]).answerable is False


def test_the_refusal_names_both_tables_and_what_would_unlock_it() -> None:
    """ "I cannot answer that" without a reason is indistinguishable from the
    product being broken (4.3)."""
    sentence = CapabilityGap(left="menu_items", right="orders").sentence()

    assert "menu_items" in sentence
    assert "orders" in sentence
    assert "linking" in sentence


def test_the_gap_that_fails_is_the_one_named() -> None:
    """Pairwise rather than "is the set connected", so a question over three
    tables says *which* one is stranded."""
    graph = _graph(orders=["customers"], customers=["orders"], menu_items=[])

    verdict = graph.check(["orders", "customers", "menu_items"])

    assert {(gap.left, gap.right) for gap in verdict.gaps} == {
        ("customers", "menu_items"),
        ("menu_items", "orders"),
    }


async def test_the_graph_includes_tables_that_join_to_nothing(
    context: ToolContext, wired: URL
) -> None:
    """Built from the *tables*, not only from the edges.

    A graph assembled from relationships alone would not contain `menu_items` at
    all, and a question about it would fall through this check instead of being
    refused by it — the exact failure the M8 gate is built to demonstrate.
    """
    assert context.data_source_id is not None
    graph = await load_join_graph(context.org_id, context.data_source_id)

    assert "products" in graph.edges, "the fixture's unrelated table is present"
    assert graph.edges["products"] == frozenset(), "and it joins to nothing"
    assert graph.path("products", "shops") is None


# ---------------------------------------------------------------------------
# The flagship
# ---------------------------------------------------------------------------


async def _ask(context: ToolContext, question: str) -> uuid.UUID:
    view = await runs.get_run(org_id=context.org_id, run_id=context.run_id)
    asked = await runs.post_message(
        org_id=context.org_id,
        user_id=context.actor_user_id or uuid.uuid4(),
        conversation_id=view.conversation_id,
        content=question,
        idempotency_key=uuid.uuid4().hex,
    )
    return asked.run_id


async def _execute(context: ToolContext, run_id: uuid.UUID):
    from dataagent.agent.runner import execute_run

    return await execute_run(
        org_id=context.org_id,
        run_id=run_id,
        data_source_id=context.data_source_id or uuid.uuid4(),
        actor_user_id=context.actor_user_id,
        settings=build_settings(),
    )


async def _queries_run(context: ToolContext, run_id: uuid.UUID, wired: URL) -> int:
    engine = create_async_engine(wired)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, false)"), {"org": str(context.org_id)}
            )
            return (
                await connection.execute(
                    text("SELECT count(*) FROM query_executions WHERE run_id = :run"),
                    {"run": run_id},
                )
            ).scalar_one()
    finally:
        await engine.dispose()


async def test_a_question_needing_an_absent_join_refuses_and_runs_nothing(
    context: ToolContext, fake_llm: FakeLLM, wired: URL
) -> None:
    """**The M8 gate's own demo**, in the fixture's terms.

    `products` and `shops` are unrelated in `customer_db.py` exactly as
    `menu_items` and `orders` are in the pizza database. The model is scripted to
    do the plausible wrong thing — join them anyway — and the check must stop it
    **before the statement is sent**, because the join would not error: it would
    return a cartesian product and an answer computed from one looks exactly like
    a real answer.
    """
    fake_llm.script(
        Plan(
            sql=(
                "SELECT p.name, count(*) AS sold FROM products p "
                "JOIN shops s ON 1 = 1 GROUP BY p.name"
            ),
            purpose="best selling products per shop",
            answerable=True,
            reason="",
        ).model_dump_json(),
        role="sql",
    )
    run_id = await _ask(context, "Which products sell best in each shop?")

    outcome = await _execute(context, run_id)

    assert await _queries_run(context, run_id, wired) == 0, "nothing may be sent"
    assert outcome.answered is False
    assert outcome.status == "completed", "an honest refusal is an ending, not a failure"

    view = await runs.get_run(org_id=context.org_id, run_id=run_id)
    answer = view.answer or ""
    assert "products" in answer and "shops" in answer, "the refusal names both tables"
    assert "linking" in answer, "and says what would unlock it"
    assert view.findings == [], "nothing was concluded about the data"


async def test_the_verdict_is_in_the_trace(context: ToolContext, fake_llm: FakeLLM) -> None:
    """10.3 gives `capability_checked` a place in the vocabulary; a refusal the
    trace cannot account for is one nobody can check."""
    fake_llm.script(
        Plan(
            sql="SELECT * FROM products p JOIN shops s ON 1 = 1",
            purpose="join the unrelated",
            answerable=True,
            reason="",
        ).model_dump_json(),
        role="sql",
    )
    run_id = await _ask(context, "Which products sell best in each shop?")

    await _execute(context, run_id)

    types = [event.type for event in await read_events(org_id=context.org_id, run_id=run_id)]
    assert types.count("capability_checked") == 2, "once up front, once on the refusal"
    assert "query_executed" not in types


async def test_a_question_over_joinable_tables_is_not_refused(
    context: ToolContext, fake_llm: FakeLLM, wired: URL
) -> None:
    """The control. A check that refused everything would pass the flagship and
    be worthless — `shops` and `regions` have a declared foreign key, so this
    must go all the way through to an answer.
    """
    fake_llm.script(
        Plan(
            sql=(
                "SELECT r.name, count(*) AS n FROM shops s "
                "JOIN regions r ON r.id = s.region_id GROUP BY r.name"
            ),
            purpose="shops per region",
            answerable=True,
            reason="",
        ).model_dump_json(),
        role="sql",
    )
    fake_llm.script(
        Reflection(
            findings=[], open_questions=[], next_purpose="", done=True, rationale="that answers it"
        ).model_dump_json(),
        role="plan",
    )
    fake_llm.script(
        FinalizeIn(
            answer="Two regions have shops.", answered=True, supported_by=[], confidence="high"
        ).model_dump_json(),
        role="compose",
    )
    run_id = await _ask(context, "How many shops are in each region?")

    outcome = await _execute(context, run_id)

    assert await _queries_run(context, run_id, wired) == 1, "the joinable question ran"
    assert outcome.answered is True


async def test_the_planner_is_told_which_pairs_cannot_be_joined(
    context: ToolContext, fake_llm: FakeLLM
) -> None:
    """4.3: the planner is *told so as fact*. Being told is a courtesy — the
    check above is the control — but a model that is never told will keep
    proposing the dead end and spending an iteration on it each time."""
    fake_llm.script(
        Plan(sql="SELECT 1", purpose="anything", answerable=False, reason="nope").model_dump_json(),
        role="sql",
    )
    run_id = await _ask(context, "Which products sell best in each shop?")

    await _execute(context, run_id)

    prompt = fake_llm.calls_for("sql")[0].prompt
    assert "cannot be combined" in prompt
    assert "products" in prompt
