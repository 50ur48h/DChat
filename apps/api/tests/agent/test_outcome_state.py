"""The third state, derived — and proof that it travels (B-134, D-044).

**Why this file is shaped the way it is.** `unanswered` is a new field on the
schema the composing model fills, which is **B-109's exact shape**: a colour
channel assembled, carried, accepted by the tool and asserted by a test, with no
field on the schema the model actually fills — built, tested, unreachable. And
B-133 is the same defect one layer further on: `answered` was computed, asserted
on the outcome object, and never reached a column, so the screen contradicted it
for eleven phases with a green test beside it.

So the load-bearing test here is not `run_state`'s truth table. It is
`test_a_partial_answer_survives_every_hop`, which drives a real run and then
serialises it with **the route's own function**, so the assertion can only pass if
the string the model wrote survives:

    FinalizeIn → composer.assemble → _write_ending → transition → the column
      → RunView → RunOut

The last hop, the card, is asserted in `conversation.test.tsx`; HTTP framing of a
`RunOut` is covered by `tests/runs/test_runs_routes.py`.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from dataagent.agent.composer import RUN_STATES, run_state
from dataagent.agent.runner import execute_run
from dataagent.agent.tools.base import ToolContext
from dataagent.agent.tools.finalize import FinalizeIn
from dataagent.db.models import RUN_OUTCOME_STATES
from dataagent.llm.fake import FakeLLM
from dataagent.runs import service as runs
from dataagent.runs.routes import run_out

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "src/dataagent/db/alembic/versions/0030_run_outcome_state.py"
)


def _draft(*, unanswered: str = "") -> FinalizeIn:
    return FinalizeIn(answer="Outlet C, 3.398 kg.", unanswered=unanswered, confidence="high")


# ---------------------------------------------------------------------------
# The derivation. A model is never asked which of these it produced.
# ---------------------------------------------------------------------------


def test_a_named_gap_with_nothing_behind_it_is_a_refusal() -> None:
    """The run named what it could not answer and cited nothing, so it stands
    behind nothing — a refusal however the prose reads."""
    assert run_state(_draft(unanswered="the cost"), ()) == "refused"


def test_naming_no_gap_is_answered_even_without_citations() -> None:
    """**The rule this started with was wrong here, and three tests said so.**

    Making *no citations* mean `refused` outright reclassified every answering run
    whose draft happened to cite nothing. Whether an answer is *backed* is a
    different question from whether it was *given*; the critic and **B-138** are
    where the first one lives, and widening a refusal to cover it would have
    smuggled a behaviour change into a change about vocabulary.
    """
    assert run_state(_draft(), ()) == "answered"


def test_a_citation_and_a_named_gap_is_partial() -> None:
    """**The case that did not exist before D-044.** Three runs of *"which outlet
    wastes the most, and what does it cost?"* recorded `answered=false` while
    returning Outlet C at 3.398 kg with a verified citation behind it."""
    assert run_state(_draft(unanswered="the cost"), ("exec-1",)) == "partly"


def test_a_citation_and_no_named_gap_is_answered() -> None:
    """The control. Without it every assertion above is satisfied by a function
    that never returns `answered`, which would make the product look incapable —
    a worse defect than the one being fixed."""
    assert run_state(_draft(), ("exec-1",)) == "answered"


def test_whitespace_is_not_a_named_gap() -> None:
    """A model that emits `" "` must not put the run into a state whose whole
    promise is that there is something to name on the card."""
    assert run_state(_draft(unanswered="   "), ("exec-1",)) == "answered"


def test_the_states_match_everywhere_they_are_written() -> None:
    """**Three lists in two languages, so something has to count them.**

    `composer.RUN_STATES` is what this module can produce, `models.
    RUN_OUTCOME_STATES` feeds the CHECK the ORM declares, and revision 0030's
    `OUTCOME_STATES` is what the database was actually built with. The third
    copy is not redundancy for its own sake: a constraint that exists only in a
    migration is one `test_models_and_migrations_do_not_drift` reports as drift
    and autogenerate proposes dropping — which is how it was found.

    Same arrangement `TENANT_TABLES` has with revision 0002. A state this module
    can produce and the column will reject is a run that fails at its last write.
    """
    declared = re.search(r"OUTCOME_STATES = \(([^)]*)\)", MIGRATION.read_text(encoding="utf-8"))
    assert declared, "OUTCOME_STATES not found in revision 0030"
    in_migration = tuple(re.findall(r'"([a-z]+)"', declared.group(1)))

    assert in_migration == RUN_STATES
    assert RUN_OUTCOME_STATES == RUN_STATES


# ---------------------------------------------------------------------------
# The hops. This is the test that matters.
# ---------------------------------------------------------------------------


def _plan_shops(sql: str = "SELECT count(*) AS n FROM shops") -> str:
    from dataagent.agent.planner import Plan

    return Plan(
        sql=sql, purpose="answer the question", answerable=True, reason=""
    ).model_dump_json()


def _reflect() -> str:
    from dataagent.agent.loop import Reflection

    return Reflection(
        findings=[], open_questions=[], next_purpose="", done=True, rationale="that answers it"
    ).model_dump_json()


def _passes() -> str:
    from dataagent.agent.critic import CriticOut

    return CriticOut(verdict="pass", reasons=[]).model_dump_json()


async def test_a_partial_answer_survives_every_hop(context: ToolContext, fake_llm: FakeLLM) -> None:
    """**The reachability proof, and the reason this PR is not just a migration.**

    `unanswered` is written by the model into `FinalizeIn` and has to cross five
    boundaries before a reader sees it. Each one has dropped a field before:
    B-100 at the ending, B-133 at the column, B-109 at the schema. The assertion
    below is on the object the route returns, so it fails if any hop loses it.
    """
    from llm_fixture import build_settings

    def _compose_naming_the_gap(request: object) -> str:
        """Cite by reading the execution id out of the prompt, as `test_runner`
        does — the id is minted at run time, so a hard-coded one cannot work, and
        a runner that stopped naming the execution would make this raise rather
        than quietly cite nothing."""
        found = re.search(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            str(getattr(request, "prompt_text", "")),
        )
        assert found is not None, "the composing prompt named no execution"
        return FinalizeIn(
            answer="Outlet C wastes the most, 3.398 kg. Its cost is not recorded.",
            unanswered="the cost",
            supported_by=[found.group(0)],
            confidence="high",
        ).model_dump_json()

    fake_llm.script(_plan_shops(), role="sql")
    fake_llm.script(_reflect(), role="plan")
    fake_llm.script(_compose_naming_the_gap, role="compose")
    fake_llm.script(_passes(), role="critic")

    view = await runs.get_run(org_id=context.org_id, run_id=context.run_id)
    asked = await runs.post_message(
        org_id=context.org_id,
        user_id=context.actor_user_id or uuid.uuid4(),
        conversation_id=view.conversation_id,
        content="which outlet wastes the most, and what does it cost?",
        idempotency_key=uuid.uuid4().hex,
    )
    await execute_run(
        org_id=context.org_id,
        run_id=asked.run_id,
        data_source_id=context.data_source_id or uuid.uuid4(),
        actor_user_id=context.actor_user_id,
        settings=build_settings(),
    )

    stored = await runs.get_run(org_id=context.org_id, run_id=asked.run_id)
    body = run_out(stored).model_dump()

    assert body["unanswered"] == "the cost", (
        "the composer named what it could not answer and the API did not carry it — "
        "which is B-109's shape and the whole reason this test exists"
    )
    assert body["state"] == "partly"
