"""Embedding spend inside a run, and the ceiling that bounds it (**B-073**).

This is the file the backlog entry asks for. An embedder on `ToolContext` is a
spending capability reaching the agent loop, and until this existed the two
guards around spending could not see it: D-019's per-run ceiling reads
`usage_ledger` rows *for the run*, and the query embedding was charged to no run
at all — so an agent searching its documents on every iteration spent money
somewhere the cap was not looking.

Three properties, and the order matters. **The spend is on the run's own ledger
line**, which is what makes it countable at all. **The ceiling refuses the next
embedding**, before it is made rather than after. And **a refusal degrades the
search rather than breaking it** — 8.5 calls budget exhaustion not a failure, the
lexical arm has already been paid for, and taking a working half of retrieval
away for the sake of consistency would be the wrong trade. What must not happen
is that any of it goes unsaid, which is what `Retrieval.degraded` is for.

Every embedding here is a stub. No test in this file can reach a provider, which
is B-040's rule and the reason `StubEmbedder` declares `is_stub`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import create_async_engine

from conftest import StubEmbedder, TwoOrgs, unit
from dataagent.agent.tools.base import ToolContext
from dataagent.agent.tools.knowledge import SEARCH_KNOWLEDGE, SearchKnowledgeIn, SearchKnowledgeOut
from dataagent.knowledge.ingest import ingest_document
from dataagent.knowledge.retrieve import search_knowledge
from dataagent.knowledge.store import LocalDocumentStore
from dataagent.llm import meter
from dataagent.llm.base import Usage
from llm_fixture import build_settings, seed_run

POLICY = b"""\
# Revenue policy

## Net revenue

Net revenue excludes cancelled and refunded orders.
"""

#: The stub's model id, priced so a ceiling can count it. The number is not the
#: real one and does not need to be: what is under test is that *a* price makes
#: the spend countable, and a fake price keeps the test from going stale the day
#: the provider changes theirs.
PRICES = {
    "stub-embedding": {"input": 1.0, "output": 0.0},
    "fake-strong": {"input": 10.0, "output": 30.0},
}


async def _ledger(url: URL, org_id: uuid.UUID, run_id: uuid.UUID | None) -> list[dict[str, object]]:
    """The organization's embedding rows, read as the application role would.

    Filtered to ``role = 'embed'`` so an unrelated chat call cannot make this
    pass, and ordered so a multi-batch spend reads in the order it happened.
    """
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, false)"), {"org": str(org_id)}
            )
            rows = (
                await connection.execute(
                    text(
                        "SELECT run_id, role, tier, model, input_tokens, cost_usd, status "
                        "FROM usage_ledger WHERE role = 'embed' ORDER BY created_at"
                    )
                )
            ).mappings()
            return [dict(row) for row in rows]
    finally:
        await engine.dispose()


async def _upload(
    org_id: uuid.UUID, store: LocalDocumentStore, embedder: StubEmbedder | None
) -> None:
    await ingest_document(
        org_id=org_id,
        title="Revenue policy",
        payload=POLICY,
        mime="text/markdown",
        store=store,
        embedder=embedder,
        settings=build_settings(llm_prices=PRICES),
    )


# ---------------------------------------------------------------------------
# The spend lands on the run
# ---------------------------------------------------------------------------


async def test_a_query_embedding_is_charged_to_the_run_that_asked(
    orgs: TwoOrgs, store: LocalDocumentStore, wired: URL, migrated_database: URL
) -> None:
    """The property the ceiling is built on. A row with no ``run_id`` is spend
    nothing can attribute, and a per-run cap over unattributed rows is a cap over
    zero."""
    run_id = await seed_run(migrated_database, orgs.a)
    embedder = StubEmbedder(vector_for={"revenue": unit(0)})
    await _upload(orgs.a, store, None)

    await search_knowledge(
        org_id=orgs.a,
        query="revenue",
        embedder=embedder,
        run_id=run_id,
        actor_user_id=orgs.a_user,
        settings=build_settings(llm_prices=PRICES),
    )

    rows = await _ledger(migrated_database, orgs.a, run_id)
    assert len(rows) == 1, "the query embedding did not leave exactly one ledger row"
    charged = rows[0]
    assert charged["run_id"] == run_id
    # Its own role and tier (revision 0017), never `small`: filing embedding
    # tokens beside intake calls would make every spend-by-tier query wrong.
    assert charged["role"] == "embed"
    assert charged["tier"] == "embed"
    assert charged["model"] == "stub-embedding"
    assert charged["status"] == "ok"
    assert charged["cost_usd"] == Decimal("0.000001"), "a priced embedding recorded no cost"


async def test_ingest_spend_is_recorded_even_though_no_run_asked_for_it(
    orgs: TwoOrgs, store: LocalDocumentStore, wired: URL, migrated_database: URL
) -> None:
    """An upload is not a run and has nothing to accumulate against, which is the
    hole `llm/budget.py` states plainly. The spend is still *recorded*, because
    "what did this organization spend" must be answerable from this table — it is
    only the ceiling that cannot apply."""
    embedder = StubEmbedder(vector_for={"revenue": unit(0)})

    await _upload(orgs.a, store, embedder)

    rows = await _ledger(migrated_database, orgs.a, None)
    assert rows, "embedding a document left no ledger row at all"
    assert all(row["run_id"] is None for row in rows)


# ---------------------------------------------------------------------------
# The ceiling, and what it does to a search
# ---------------------------------------------------------------------------


async def test_a_run_at_its_ceiling_still_gets_a_lexical_answer_and_is_told_why(
    orgs: TwoOrgs, store: LocalDocumentStore, wired: URL, migrated_database: URL
) -> None:
    """The trade this design makes, asserted rather than described. The vector arm
    is refused because the run has spent its allowance; the lexical arm was paid
    for by the upload and still answers; and the caller is told which of those
    happened."""
    run_id = await seed_run(migrated_database, orgs.a)
    embedder = StubEmbedder(vector_for={"revenue": unit(0)})
    await _upload(orgs.a, store, embedder)
    # A prior, priced chat call that used the whole ceiling.
    await meter.record(
        org_id=orgs.a,
        role="plan",
        tier="strong",
        provider="fake",
        model="fake-strong",
        usage=Usage(input_tokens=1_000_000, output_tokens=0),  # $10.00
        latency_ms=1,
        run_id=run_id,
        settings=build_settings(llm_prices=PRICES),
    )
    before = embedder.calls

    found = await search_knowledge(
        org_id=orgs.a,
        query="cancelled refunded orders",
        embedder=embedder,
        run_id=run_id,
        settings=build_settings(llm_prices=PRICES, llm_run_cost_limit_usd=1.0),
    )

    assert embedder.calls == before, "the ceiling was checked after the spend, not before"
    assert found.arms == ("lexical",)
    assert found.degraded is not None
    assert "spending ceiling" in found.degraded
    assert found.passages, "the lexical arm was taken away along with the vector arm"


async def test_an_unpriced_embedding_model_under_a_ceiling_is_refused_the_same_way(
    orgs: TwoOrgs, store: LocalDocumentStore, wired: URL, migrated_database: URL
) -> None:
    """D-019's rule, arriving through the new door. An unpriced model records a
    NULL cost, which no ceiling can count — so under a cap it is refused rather
    than waved through, and the search degrades instead of spending blind."""
    run_id = await seed_run(migrated_database, orgs.a)
    embedder = StubEmbedder(vector_for={"revenue": unit(0)})
    await _upload(orgs.a, store, embedder)

    found = await search_knowledge(
        org_id=orgs.a,
        query="cancelled refunded orders",
        embedder=embedder,
        run_id=run_id,
        settings=build_settings(llm_prices={}, llm_run_cost_limit_usd=1.0),
    )

    assert found.arms == ("lexical",)
    assert found.degraded is not None
    assert found.passages


async def test_a_provider_failure_degrades_the_search_rather_than_failing_it(
    orgs: TwoOrgs, store: LocalDocumentStore, wired: URL, migrated_database: URL
) -> None:
    """A rate limit is not a reason to answer nothing. It is a reason to answer
    from the half that still works and say the other half did not run."""
    from dataagent.knowledge.embeddings import EmbeddingError

    run_id = await seed_run(migrated_database, orgs.a)
    await _upload(orgs.a, store, None)
    broken = StubEmbedder(fail_with=EmbeddingError("rate limited", retryable=True))

    found = await search_knowledge(
        org_id=orgs.a,
        query="cancelled refunded orders",
        embedder=broken,
        run_id=run_id,
        settings=build_settings(llm_prices=PRICES),
    )

    assert found.arms == ("lexical",)
    assert found.degraded is not None
    assert "could not be run" in found.degraded
    assert found.passages
    # The failed attempt is on the ledger, as `llm/service.py` records a failed
    # completion: a provider that died has still been asked, and a ledger that
    # only holds successes understates the bill exactly when someone is trying
    # to find out why it is high.
    rows = await _ledger(migrated_database, orgs.a, run_id)
    assert [row["status"] for row in rows] == ["error"]
    assert rows[0]["run_id"] == run_id


# ---------------------------------------------------------------------------
# What the model is told
# ---------------------------------------------------------------------------


async def _call(context: ToolContext, query: str) -> SearchKnowledgeOut:
    result = await SEARCH_KNOWLEDGE.handler(context, SearchKnowledgeIn(query=query))
    assert isinstance(result, SearchKnowledgeOut)
    return result


async def test_the_tool_searches_by_meaning_when_the_run_has_an_embedder(
    orgs: TwoOrgs, store: LocalDocumentStore, wired: URL, migrated_database: URL
) -> None:
    """B-073's practical cost, closed: a question worded unlike the document used
    to find nothing through this tool, because the tool called retrieval without
    an embedder however well configured the deployment was."""
    run_id = await seed_run(migrated_database, orgs.a)
    embedder = StubEmbedder(vector_for={"cancelled": unit(0), "takings": unit(0)})
    await _upload(orgs.a, store, embedder)
    context = ToolContext(
        org_id=orgs.a,
        run_id=run_id,
        role="reader",
        embedder=embedder,
        settings=build_settings(llm_prices=PRICES),
    )

    result = await _call(context, "takings")

    assert result.passages, "a query sharing no word with the document found nothing"
    assert any(passage.found_by in {"vector", "both"} for passage in result.passages)
    assert result.note == "", "a search that ran in full should not report a degradation"


async def test_a_degraded_search_that_finds_nothing_does_not_say_nothing_is_written_down(
    orgs: TwoOrgs, store: LocalDocumentStore, wired: URL, migrated_database: URL
) -> None:
    """The distinction this whole entry turns on. *"Nothing is written down about
    that"* invites a model to stop looking and answer from its own knowledge; it
    is only honest when the whole search actually ran. A run with no embedder is
    told both facts, in that order."""
    run_id = await seed_run(migrated_database, orgs.a)
    await _upload(orgs.a, store, None)
    context = ToolContext(
        org_id=orgs.a,
        run_id=run_id,
        role="reader",
        embedder=None,
        settings=build_settings(llm_prices=PRICES),
    )

    result = await _call(context, "zzzz nonexistent term")

    assert result.passages == []
    assert "no embedding model configured" in result.note
    assert "do not infer a definition" in result.note
    assert result.note.index("embedding model") < result.note.index("do not infer")
