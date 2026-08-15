"""Choosing what goes into a prompt, against a real catalog.

``test_context.py`` proves the assembler is a correct function of a bundle. This
file proves the bundle is built from the right things — which is the half that
can leak, because everything selected here ends up in front of a model.

The claim worth holding: **nothing selected can carry a value the DAL would have
hidden.** Card examples were masked before they were stored (D-013), so a card is
safe by construction rather than by a filter here that somebody could forget.
"""

from __future__ import annotations

import uuid

from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import create_async_engine

from dataagent.agent.context import build_context, render
from dataagent.agent.tools.base import ToolContext
from dataagent.catalog import policies
from dataagent.dal import policy as dal_policy


async def _any_column_of(wired: URL, org_id: uuid.UUID, table: str) -> uuid.UUID:
    """One column id of a named table in the active catalog."""
    engine = create_async_engine(wired)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, false)"), {"org": str(org_id)}
            )
            return (
                await connection.execute(
                    text(
                        "SELECT c.id FROM catalog_columns c "
                        "JOIN catalog_tables t ON t.id = c.table_id "
                        "JOIN catalog_snapshots s ON s.id = t.snapshot_id "
                        "WHERE s.status = 'active' AND t.table_name = :table "
                        "ORDER BY c.ordinal LIMIT 1"
                    ),
                    {"table": table},
                )
            ).scalar_one()
    finally:
        await engine.dispose()


async def _restrict(context: ToolContext, wired: URL, policy: str) -> tuple[str, str]:
    """Put a policy on a column of a table this question really selects.

    Chosen from what the search returns rather than named outright, because a
    table is not reliably findable by its own name yet (**B-039**) — so a test
    that named one would be testing the defect instead of the behaviour.
    """
    question = "shops and regions"
    bundle = await build_context(
        org_id=context.org_id, question=question, data_source_id=context.data_source_id
    )
    assert bundle.cards, "the fixture catalog matched nothing, so there is nothing to restrict"
    card = bundle.cards[0]

    column_id = await _any_column_of(wired, context.org_id, card.table_name)
    assert context.actor_user_id is not None
    assert context.data_source_id is not None
    await policies.set_policy(
        org_id=context.org_id,
        actor_user_id=context.actor_user_id,
        data_source_id=context.data_source_id,
        column_id=column_id,
        policy=policy,
        reason="test",
    )
    dal_policy.invalidate_all()
    return question, card.qualified


async def test_a_question_selects_the_tables_its_words_point_at(context: ToolContext) -> None:
    bundle = await build_context(
        org_id=context.org_id,
        question="which shops are in which region?",
        data_source_id=context.data_source_id,
    )

    assert bundle.cards, "nothing was selected for a question about the seeded tables"
    assert all(card.card_text for card in bundle.cards)
    assert bundle.question == "which shops are in which region?"


async def test_a_question_that_matches_nothing_still_produces_a_usable_prompt(
    context: ToolContext,
) -> None:
    """A prompt with no cards is thin, not broken. The model still has the rules
    and the tools, and ``search_tables`` is how it recovers."""
    bundle = await build_context(
        org_id=context.org_id,
        question="what is our cryptocurrency exposure?",
        data_source_id=context.data_source_id,
    )

    assert bundle.cards == ()
    messages = render(bundle)
    assert "[L4]" not in messages[0].content
    assert messages[1].content == "what is our cryptocurrency exposure?"


async def test_a_masked_column_is_summarised_into_the_prompt(
    context: ToolContext, wired: URL
) -> None:
    """So the model does not spend a round trip writing SQL the DAL will refuse.

    A courtesy rather than a control: the refusal happens whether or not this
    summary was right, which is why a stale one is a performance bug.
    """
    question, table = await _restrict(context, wired, "mask")

    bundle = await build_context(
        org_id=context.org_id, question=question, data_source_id=context.data_source_id
    )

    masked = {r.qualified for r in bundle.restrictions if r.policy == "mask"}
    assert masked, "a masked column did not reach the bundle"
    assert all(name.startswith(f"{table}.") for name in masked)
    assert "Masked" in render(bundle)[0].content


async def test_a_denied_column_is_named_as_unusable_anywhere(
    context: ToolContext, wired: URL
) -> None:
    question, _ = await _restrict(context, wired, "deny")

    bundle = await build_context(
        org_id=context.org_id, question=question, data_source_id=context.data_source_id
    )
    system = render(bundle)[0].content

    assert any(r.policy == "deny" for r in bundle.restrictions)
    # The distinction is the whole point: one may be counted, the other may not
    # appear at all.
    assert "Denied" in system
    assert "including in filters" in system


async def test_restrictions_cover_only_the_tables_that_were_selected(
    context: ToolContext, wired: URL
) -> None:
    """A list of every restricted column in the organization would be longer than
    the cards and would tell the model about tables it was not given."""
    question, _ = await _restrict(context, wired, "deny")

    bundle = await build_context(
        org_id=context.org_id, question=question, data_source_id=context.data_source_id
    )

    selected = {card.qualified for card in bundle.cards}
    assert bundle.restrictions
    for restriction in bundle.restrictions:
        assert f"{restriction.schema_name}.{restriction.table_name}" in selected
