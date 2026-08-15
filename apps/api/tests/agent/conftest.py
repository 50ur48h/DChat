"""Fixtures for the agent suite.

One organization with a registered, verified, discovered and carded data source,
and a real run to attribute work to. Built through the real services rather than
by inserting rows: what these tests are about is the agent meeting the rest of
the system, and a hand-built catalog would let it meet a catalog that discovery
would never produce.

The run is real because it has to be — ``query_executions.run_id`` became a
foreign key in revision 0012, so an invented uuid is precisely what the
constraint refuses.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from customer_db import CustomerDatabase
from dataagent.agent.tools.base import ToolContext
from dataagent.catalog import cards, discovery
from dataagent.dal import policy as dal_policy
from dataagent.datasources import service as datasource_service
from dataagent.db import engine as engine_module
from dataagent.runs import service as runs
from dataagent.secrets.local import LocalSecretsProvider
from dataagent.tenancy import session as session_module


@pytest.fixture
async def wired(
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
    # The policy cache is process-wide and keyed by (org, source); another test's
    # entry would be a different catalog answering this one's questions.
    dal_policy.invalidate_all()
    try:
        yield app_database
    finally:
        dal_policy.invalidate_all()
        await owner.dispose()
        await app_engine.dispose()


@pytest.fixture
async def context(
    wired: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> ToolContext:
    """One org, one discovered source, one queued run — as the runner will see it."""
    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    engine = create_async_engine(migrated_database)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            await connection.execute(
                text("INSERT INTO organizations (id, name) VALUES (:id, 'Tools')"), {"id": org_id}
            )
            await connection.execute(
                text("INSERT INTO users (id, external_subject, email) VALUES (:i, :s, :e)"),
                {"i": user_id, "s": f"sub-{user_id}", "e": "asker@example.com"},
            )
    finally:
        await engine.dispose()

    source = await datasource_service.create_data_source(
        org_id=org_id,
        actor_user_id=user_id,
        name="Customer",
        engine="pg",
        host=isolated_customer_database.host,
        port=isolated_customer_database.port,
        database=isolated_customer_database.database,
        username=isolated_customer_database.reader_username,
        password=isolated_customer_database.reader_password,
        tls_mode="prefer",
    )
    # Verified first: discovery refuses a source whose credentials were never
    # proven read-only, which is the rule doing its job rather than an obstacle.
    await datasource_service.test_data_source(
        org_id=org_id, actor_user_id=user_id, data_source_id=source.id
    )
    await discovery.discover(org_id=org_id, actor_user_id=user_id, data_source_id=source.id)
    # Cards are what `search_tables` searches; discovery writes the rows, this
    # writes the prose over them.
    await cards.refresh_cards(org_id, source.id)

    conversation = await runs.create_conversation(org_id=org_id, user_id=user_id, title="Tools")
    asked = await runs.post_message(
        org_id=org_id,
        user_id=user_id,
        conversation_id=conversation.id,
        content="What is in this database?",
        idempotency_key=uuid.uuid4().hex,
    )
    return ToolContext(
        org_id=org_id,
        run_id=asked.run_id,
        role="reader",
        actor_user_id=user_id,
        data_source_id=source.id,
    )
