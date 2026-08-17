"""Fixtures for the knowledge suite.

Two organizations against a real platform database, because the property this
suite exists to prove is a *database* property: row-level security over
`knowledge_documents` and `knowledge_chunks`. A fake store would prove the shape
of the code and none of what it is for.

The embedder is a stub and always will be — B-040's rule, with the sharpest edge
in this package: embedding is the one path in `knowledge/` that spends money, and
a suite that can reach a real provider is a suite that can spend it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dataagent.db import engine as engine_module
from dataagent.knowledge.embeddings import EmbeddingBatch
from dataagent.knowledge.store import LocalDocumentStore
from dataagent.llm.base import Usage
from dataagent.tenancy import session as session_module

DIMENSIONS = 1536


@dataclass
class StubEmbedder:
    """Vectors chosen by the test, not by a model.

    Deterministic and inspectable: `seen` is every text this was asked to
    embed, which is how a test asserts *what* was sent as well as what came
    back. `vector_for` maps a substring to the vector a text containing it gets,
    so a test can say "these two passages are near each other" without a model
    and without pretending to do semantics.
    """

    vector_for: dict[str, list[float]] = field(default_factory=dict[str, list[float]])
    default: float = 0.0
    seen: list[str] = field(default_factory=list[str])
    calls: int = 0
    fail_with: Exception | None = None

    async def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        self.calls += 1
        self.seen.extend(texts)
        if self.fail_with is not None:
            raise self.fail_with
        vectors: list[tuple[float, ...]] = []
        for item in texts:
            chosen = None
            for needle, vector in self.vector_for.items():
                if needle.lower() in item.lower():
                    chosen = vector
                    break
            vectors.append(tuple(chosen if chosen is not None else [self.default] * DIMENSIONS))
        return EmbeddingBatch(
            vectors=tuple(vectors),
            usage=Usage(input_tokens=len(texts), output_tokens=0),
            model="stub-embedding",
        )

    async def aclose(self) -> None:
        return None


def unit(index: int) -> list[float]:
    """A basis vector: 1.0 in one position, 0.0 everywhere else.

    Two of these are exactly orthogonal and each is exactly itself, so "nearest
    by cosine distance" has an answer a test can state without arithmetic.
    """
    vector = [0.0] * DIMENSIONS
    vector[index] = 1.0
    return vector


@dataclass(frozen=True, slots=True)
class TwoOrgs:
    """Two tenants and a member of each."""

    a: uuid.UUID
    b: uuid.UUID
    a_user: uuid.UUID
    b_user: uuid.UUID


@pytest.fixture
async def wired(
    app_database: URL, migrated_database: URL, monkeypatch: pytest.MonkeyPatch
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


@pytest.fixture
async def orgs(wired: URL, migrated_database: URL) -> TwoOrgs:
    """Two organizations, each with one member.

    Separate users on purpose. The RLS proof suite deliberately uses the *same*
    person in both, to show isolation comes from the organization; here the
    interesting case is the ordinary one, and using one user would leave "would
    it also work for two?" unasked.
    """
    a, b, a_user, b_user = (uuid.uuid4() for _ in range(4))
    engine = create_async_engine(migrated_database)
    try:
        async with engine.begin() as connection:
            for org_id, user_id, name in ((a, a_user, "Alpha"), (b, b_user, "Beta")):
                await connection.execute(
                    text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
                )
                await connection.execute(
                    text("INSERT INTO organizations (id, name) VALUES (:id, :name)"),
                    {"id": org_id, "name": name},
                )
                await connection.execute(
                    text("INSERT INTO users (id, external_subject, email) VALUES (:i, :s, :e)"),
                    {"i": user_id, "s": f"sub-{user_id}", "e": f"{name.lower()}@example.com"},
                )
                await connection.execute(
                    text(
                        "INSERT INTO org_memberships (org_id, user_id, role) "
                        "VALUES (:org, :user, 'admin')"
                    ),
                    {"org": org_id, "user": user_id},
                )
    finally:
        await engine.dispose()
    return TwoOrgs(a=a, b=b, a_user=a_user, b_user=b_user)


@pytest.fixture
def store(tmp_path: Path) -> LocalDocumentStore:
    return LocalDocumentStore(tmp_path / "docs")
