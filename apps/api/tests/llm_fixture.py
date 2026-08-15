"""What the LLM suite is configured with.

A module rather than fixtures, so a test that needs to name a model id or build
its own settings can import the same values the fixtures use — the shape
``tests/dal/catalog_fixture.py`` established.
"""

from __future__ import annotations

import uuid

from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import create_async_engine

from dataagent.config import Settings

#: One provider, three tiers, obviously fake model ids. Named after their tier so
#: that a test asserting on a model id is really asserting on the tier that was
#: chosen for the role.
FAKE_MODELS = {"fake": {"small": "fake-small", "mid": "fake-mid", "strong": "fake-strong"}}


def build_settings(**overrides: object) -> Settings:
    """Settings with one configured provider and no prices.

    No prices on purpose: the default state of this system is that a model is
    unpriced, so the tests that care about cost add them explicitly and the rest
    prove that an unpriced call records a null rather than a zero.

    **Hermetic in two ways, and both are required.** ``_env_file=None`` switches
    off the repository's ``.env`` for these settings, and every LLM field is then
    passed explicitly. The second alone is not enough: pydantic-settings
    *deep-merges* dict-typed fields across sources, so an explicit
    ``llm_role_map={}`` is merged with whatever ``.env`` holds rather than
    replacing it, and ``llm_models`` would quietly gain every real provider the
    developer had configured. A machine with working local configuration would
    otherwise assert different things from CI — which is precisely what happened
    the first time this file was written without it.
    """
    values: dict[str, object] = {
        "env": "ci",
        "build_env": "dev",
        "llm_providers": ("fake",),
        "llm_models": FAKE_MODELS,
        "llm_role_map": {},
        "llm_prices": {},
        "llm_run_cost_limit_usd": None,
        "llm_refuse_unpriced_when_capped": True,
        "openai_api_key": None,
        "anthropic_api_key": None,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # pyright: ignore[reportArgumentType, reportCallIssue]


async def seed_run(url: URL, org_id: uuid.UUID) -> uuid.UUID:
    """A real conversation and a real run, and the run's id.

    From revision 0012 ``usage_ledger.run_id`` is a foreign key to ``agent_runs``
    (WP7.1), so a ledger row can no longer name a run that was never created. The
    invented uuids these tests used before were exactly the thing the constraint
    was added to stop: a cost attributed to nothing, which no screen could ever
    resolve and no per-run ceiling could bound.
    """
    run_id = uuid.uuid4()
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            conversation_id = (
                await connection.execute(
                    text(
                        "INSERT INTO conversations (org_id, title) VALUES (:org, 'Metered') "
                        "RETURNING id"
                    ),
                    {"org": org_id},
                )
            ).scalar_one()
            await connection.execute(
                text(
                    "INSERT INTO agent_runs (id, org_id, conversation_id, status, question) "
                    "VALUES (:id, :org, :conversation, 'running', 'How much did that cost?')"
                ),
                {"id": run_id, "org": org_id, "conversation": conversation_id},
            )
    finally:
        await engine.dispose()
    return run_id
