"""Card search by meaning, not only by wording (**B-018**).

The oldest item in Phase 10, open since WP4.3. Lexical search finds `orders`
from *"revenue"* only because the word "revenue" happens to appear in that
card's prose; it cannot find it from *"how much did we make"* at all. Golden eval
**#14** — *"which day of the week is busiest?"* — fails live for exactly that
reason, and `test_a_card_is_found_by_a_question_that_shares_no_word_with_it` is
that failure in miniature, without a model and without a dollar.

Everything here uses a stub embedder whose vectors the test chooses, so
"semantically near" is a fact the test states rather than a model's opinion. What
that buys is that these tests are about the **merge, the backfill and the
degradation**, which is the code this WP wrote — the quality of the embeddings
themselves is the provider's business and is checked live, once, in the PR.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence

import pytest
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from customer_db import CustomerDatabase
from dataagent.catalog import cards, discovery, profiler, search
from dataagent.datasources import service as datasources
from dataagent.db import engine as engine_module
from dataagent.knowledge.embeddings import EmbeddingBatch
from dataagent.llm.base import Usage
from dataagent.secrets.local import LocalSecretsProvider
from dataagent.tenancy import session as session_module
from llm_fixture import build_settings

DIMENSIONS = 1536


class StubEmbedder:
    """Vectors the test chooses, and a count of what it was asked to embed.

    A copy of the knowledge suite's stub rather than an import of it, for
    **B-074**'s reason: every `conftest.py` is the module `conftest`, so
    importing across suites is what makes `pytest tests/a tests/b` fail to
    collect. Small enough that duplicating it costs less than the collision.
    """

    def __init__(self, vector_for: dict[str, list[float]] | None = None) -> None:
        self.vector_for = vector_for or {}
        self.seen: list[str] = []
        self.calls = 0
        self.fail_with: Exception | None = None
        self.is_stub = True
        self.model = "stub-embedding"

    async def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        self.calls += 1
        self.seen.extend(texts)
        if self.fail_with is not None:
            raise self.fail_with
        vectors: list[tuple[float, ...]] = []
        for item in texts:
            chosen = next(
                (
                    vector
                    for needle, vector in self.vector_for.items()
                    if needle.lower() in item.lower()
                ),
                None,
            )
            vectors.append(tuple(chosen if chosen is not None else [0.0] * DIMENSIONS))
        return EmbeddingBatch(
            vectors=tuple(vectors),
            usage=Usage(input_tokens=len(texts), output_tokens=0),
            model=self.model,
        )

    async def aclose(self) -> None:
        return None


def unit(index: int) -> list[float]:
    """A basis vector: 1.0 in one position and 0.0 elsewhere, so "nearest by
    cosine distance" has an answer a test can state without arithmetic."""
    vector = [0.0] * DIMENSIONS
    vector[index] = 1.0
    return vector


@pytest.fixture
async def platform(
    app_database: URL,
    migrated_database: URL,
    secrets_provider: LocalSecretsProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[URL]:
    owner = create_async_engine(migrated_database)
    app_engine = create_async_engine(app_database)
    monkeypatch.setattr(engine_module, "get_engine", lambda: owner)
    monkeypatch.setattr(
        session_module,
        "_session_factory",
        lambda: async_sessionmaker(app_engine, expire_on_commit=False, autoflush=False),
    )
    try:
        yield app_database
    finally:
        await owner.dispose()
        await app_engine.dispose()


async def _catalogued(
    migrated_database: URL,
    customer: CustomerDatabase,
    *,
    embedder: StubEmbedder | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """An organization with one discovered, carded data source."""
    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    engine = create_async_engine(migrated_database)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            await connection.execute(
                text("INSERT INTO organizations (id, name) VALUES (:id, 'Vectors')"), {"id": org_id}
            )
            await connection.execute(
                text("INSERT INTO users (id, external_subject, email) VALUES (:i, :s, :e)"),
                {"i": user_id, "s": f"sub-{user_id}", "e": "owner@example.com"},
            )
            await connection.execute(
                text(
                    "INSERT INTO org_memberships (org_id, user_id, role) VALUES (:o, :u, 'admin')"
                ),
                {"o": org_id, "u": user_id},
            )
    finally:
        await engine.dispose()

    view = await datasources.create_data_source(
        org_id=org_id,
        actor_user_id=user_id,
        name="Customer",
        engine="pg",
        host=customer.host,
        port=customer.port,
        database=customer.database,
        username=customer.reader_username,
        password=customer.reader_password,
        tls_mode="prefer",
    )
    await datasources.test_data_source(org_id=org_id, actor_user_id=user_id, data_source_id=view.id)
    await discovery.discover(
        org_id=org_id, actor_user_id=user_id, data_source_id=view.id, embedder=embedder
    )
    # Profiled, like `test_cards_and_search.py`'s fixture, because a profile
    # changes what a card says and therefore how it ranks — an unprofiled
    # catalog would make these tests measure a shape the product never has. It
    # also exercises the path that matters most for the backfill: a re-carded
    # table loses the vector describing words it no longer contains, and
    # `profile` fills it back in.
    await profiler.profile(
        org_id=org_id, actor_user_id=user_id, data_source_id=view.id, embedder=embedder
    )
    return org_id, view.id


async def _embedded(url: URL, org_id: uuid.UUID) -> dict[str, bool]:
    """Which cards carry a vector, by table name, read as the app role would."""
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, false)"), {"org": str(org_id)}
            )
            rows = (
                await connection.execute(
                    text(
                        "SELECT t.table_name, t.embedding IS NOT NULL AS embedded, t.flags "
                        "FROM catalog_tables t "
                        "JOIN catalog_snapshots s ON s.id = t.snapshot_id "
                        "WHERE s.status = 'active'"
                    )
                )
            ).mappings()
            return {row["table_name"]: bool(row["embedded"]) for row in rows}
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# The backfill
# ---------------------------------------------------------------------------


async def test_discovering_a_catalog_embeds_its_cards(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """One pass, at the moment the cards are written. A catalog whose cards are
    searchable by wording and not by meaning is half a catalog, and nothing
    later would tell anyone which half they had."""
    embedder = StubEmbedder()

    org_id, _ = await _catalogued(migrated_database, isolated_customer_database, embedder=embedder)

    embedded = await _embedded(platform, org_id)
    assert embedded, "the catalog has no cards at all"
    assert all(embedded.values()), f"some cards were left unembedded: {embedded}"


async def test_a_deployment_with_no_embedder_still_gets_its_cards(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """The absence of an embedding model is a supported state. The cards are
    written, they stay lexically searchable, and nothing raises — because catalog
    refresh is the one thing a new organization cannot skip."""
    org_id, _ = await _catalogued(migrated_database, isolated_customer_database)

    embedded = await _embedded(platform, org_id)
    assert embedded and not any(embedded.values())
    assert await search.search_cards(org_id, "trading name"), "lexical search stopped working"


async def test_the_backfill_is_idempotent(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """What makes it a backfill rather than a rebuild. Running it twice must cost
    one provider call, not two — otherwise every catalog refresh re-buys every
    vector it already has."""
    embedder = StubEmbedder()
    org_id, source_id = await _catalogued(
        migrated_database, isolated_customer_database, embedder=embedder
    )
    after_first = embedder.calls

    written = await cards.embed_cards(
        org_id, source_id, embedder=embedder, settings=build_settings()
    )

    assert written == 0, "the backfill re-embedded cards that already had vectors"
    assert embedder.calls == after_first, "the provider was called with nothing to do"


async def test_a_provider_failure_leaves_the_cards_lexically_searchable(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """The cards were already written. Losing a catalog to a rate limit would
    trade working search for tidy bookkeeping — and the rows stay queued, so the
    next refresh finishes the job."""
    from dataagent.knowledge.embeddings import EmbeddingError

    broken = StubEmbedder()
    broken.fail_with = EmbeddingError("rate limited", retryable=True)

    org_id, _ = await _catalogued(migrated_database, isolated_customer_database, embedder=broken)

    embedded = await _embedded(platform, org_id)
    assert embedded, "the refresh failed outright instead of degrading"
    assert not any(embedded.values())
    assert await search.search_cards(org_id, "trading name")


async def test_an_unchanged_card_keeps_the_vector_it_already_paid_for(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """D-012's rule — a refresh that finds no change writes nothing — applied to
    the half that costs money. Re-carding a table whose text is identical must
    not invalidate a vector we would then buy again."""
    embedder = StubEmbedder()
    org_id, source_id = await _catalogued(
        migrated_database, isolated_customer_database, embedder=embedder
    )
    after_first = embedder.calls

    await cards.refresh_cards(org_id, source_id)
    await cards.embed_cards(org_id, source_id, embedder=embedder, settings=build_settings())

    assert embedder.calls == after_first, "an unchanged card was re-embedded"
    assert all((await _embedded(platform, org_id)).values())


# ---------------------------------------------------------------------------
# What the vector arm buys
# ---------------------------------------------------------------------------


async def test_a_card_is_found_by_a_question_that_shares_no_word_with_it(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """**B-018 in one test, and golden eval #14 in miniature.**

    The question contains no word from the card, so the lexical arm returns
    nothing at all and the vector arm has to be what finds it. Before this WP the
    planner was handed an empty catalog for exactly this shape of question.
    """
    embedder = StubEmbedder(vector_for={"shops (public.shops)": unit(3), "outlets we run": unit(3)})
    org_id, _ = await _catalogued(migrated_database, isolated_customer_database, embedder=embedder)
    question = "outlets we run"

    lexical = await search.search_cards(org_id, question)
    hybrid = await search.search_cards(
        org_id, question, embedder=embedder, settings=build_settings()
    )

    assert lexical == [], "the question was findable by wording, so this proves nothing"
    assert hybrid, "the vector arm found nothing the lexical arm had missed"
    assert hybrid[0].table_name == "shops"
    assert hybrid[0].found_by == "vector"


async def test_a_card_both_arms_agree_on_outranks_one_either_found_alone(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """What Reciprocal Rank Fusion is for. Agreement between two independent
    signals is the strongest thing this retrieval can say, and it must survive
    the merge rather than being averaged away."""
    embedder = StubEmbedder(vector_for={"shops (public.shops)": unit(3), "trading name": unit(3)})
    org_id, _ = await _catalogued(migrated_database, isolated_customer_database, embedder=embedder)

    hits = await search.search_cards(
        org_id, "trading name", embedder=embedder, settings=build_settings()
    )

    assert hits
    assert hits[0].table_name == "shops"
    assert hits[0].found_by == "both"
    assert all(hit.rank <= hits[0].rank for hit in hits)


async def test_without_an_embedder_the_ranking_is_the_one_it_always_was(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """A regression guard rather than a feature. With one arm, RRF is a monotone
    transformation of that arm's own order — so a deployment with no embedding
    key must get exactly the results it got before this WP, in the same order."""
    embedder = StubEmbedder()
    org_id, _ = await _catalogued(migrated_database, isolated_customer_database, embedder=embedder)

    hits = await search.search_cards(org_id, "How many shops opened in 2021?")

    assert [hit.table_name for hit in hits][:1] == ["shops"]
    assert all(hit.found_by == "lexical" for hit in hits)


async def test_the_search_says_which_arm_found_each_card(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """`found_by` reaches `context_selected`, and a run whose tables all came
    from the lexical arm on a deployment that has an embedder is a retrieval
    regression with no other symptom (B-060's family)."""
    embedder = StubEmbedder(
        vector_for={"regions (public.regions)": unit(7), "areas of the country": unit(7)}
    )
    org_id, _ = await _catalogued(migrated_database, isolated_customer_database, embedder=embedder)

    hits = await search.search_cards(
        org_id, "areas of the country", embedder=embedder, settings=build_settings()
    )

    assert hits
    assert {hit.found_by for hit in hits} <= {"vector", "lexical", "both"}
    assert any(hit.found_by == "vector" for hit in hits)


async def test_a_failed_query_embedding_falls_back_to_wording_rather_than_failing(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """This is the **context** stage of every run, so raising here would turn a
    busy provider into a question that cannot be answered at all. The lexical arm
    has already been paid for and still answers."""
    from dataagent.knowledge.embeddings import EmbeddingError

    working = StubEmbedder()
    org_id, _ = await _catalogued(migrated_database, isolated_customer_database, embedder=working)
    broken = StubEmbedder()
    broken.fail_with = EmbeddingError("rate limited", retryable=True)

    hits = await search.search_cards(
        org_id, "trading name", embedder=broken, settings=build_settings()
    )

    assert hits, "a failed query embedding took the whole search down with it"
    assert all(hit.found_by == "lexical" for hit in hits)
