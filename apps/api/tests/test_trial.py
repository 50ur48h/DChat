"""The engine trial's comparison, and the floor under it.

**What has to be true for this tool to be worth running.** That it *notices*
disagreement — the whole product of `trial.py` is `divergences`, and a comparison
that quietly agrees with itself is worse than no comparison, because somebody
will read the green and conclude the model is stable. And that it refuses to be
asked once: `MINIMUM_REPEATS` is the lesson of 2026-08-25 written as code.

The control matters as much as the cases. A `divergences` that flagged everything
would also "catch" B-060 and B-119, and would be useless.
"""

from __future__ import annotations

import uuid

import pytest

from dataagent.agent.state import ResearchState
from dataagent.ops import trial


def _run(
    *,
    status: str = "completed",
    answered: bool | None = True,
    answer: str = "There are 3 shops.",
    tables: tuple[str, ...] = ("public.shops",),
    statements: tuple[str, ...] = ("SELECT count(*) FROM shops",),
) -> trial.ProbeRun:
    return trial.ProbeRun(
        run_id=uuid.uuid4(),
        status=status,
        answered=answered,
        answer=answer,
        sources_offered=(),
        tables_read=tables,
        statements=statements,
        limitations=(),
        method="1 query over one step",
        findings=1,
    )


# ---------------------------------------------------------------------------
# The control comes first, because everything below is worthless without it
# ---------------------------------------------------------------------------


def test_three_identical_runs_disagree_about_nothing() -> None:
    """A comparison that flags agreement flags everything, and would be dropped
    within a day of somebody reading its output."""
    assert trial.divergences(tuple(_run() for _ in range(3))) == ()


# ---------------------------------------------------------------------------
# The two shapes this tool exists to catch
# ---------------------------------------------------------------------------


def test_a_question_that_refuses_once_and_answers_twice_is_flagged() -> None:
    """**B-119's shape, and the reason for the repeat floor.**

    Asked once, this run set is a clean answer or a clean refusal depending on
    which of the three you happened to get.
    """
    found = trial.divergences(
        (_run(), _run(), _run(answered=False, answer="I could not answer that."))
    )

    assert any("ended differently" in note for note in found)
    assert any("B-119" in note for note in found)


def test_runs_that_read_different_tables_are_flagged() -> None:
    """**B-060's shape**: two defensible sources for one question, and answers two
    orders of magnitude apart. The tables are the signal that precedes the
    numbers — and they come from what the validator resolved, not from what the
    answer says it read."""
    found = trial.divergences(
        (
            _run(tables=("public.fact_sale",)),
            _run(tables=("public.fact_sale",)),
            _run(tables=("public.fact_purchase",)),
        )
    )

    assert any("different tables" in note for note in found)
    assert any("B-060" in note for note in found)


def test_runs_that_state_different_numbers_are_flagged() -> None:
    found = trial.divergences(
        (_run(answer="Revenue was 1,735,835.05."), _run(answer="Revenue was 3,625,180.34."))
    )

    assert any("different numbers" in note for note in found)


def test_the_same_number_written_differently_is_not_a_disagreement() -> None:
    """Thousands separators are formatting, not a different answer. Without this
    the tool cries wolf on every currency figure it sees."""
    assert trial.divergences((_run(answer="1,735,835.05"), _run(answer="1735835.05"))) == ()


# ---------------------------------------------------------------------------
# The floor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_single_run_is_refused_with_the_reason() -> None:
    """`MINIMUM_REPEATS` is enforced, not defaulted.

    A default is something a caller overrides at the moment it is inconvenient,
    which is exactly the moment it matters. The message names the incident rather
    than the rule, because "asking once measures the model's luck" is an argument
    and "3" is not.
    """
    with pytest.raises(ValueError, match="B-119"):
        await trial.run_probe(
            org_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            data_source_id=uuid.uuid4(),
            question="anything",
            repeats=1,
        )


# ---------------------------------------------------------------------------
# The correspondence check — the one that catches this module's own defect class
# ---------------------------------------------------------------------------


def test_the_state_key_this_reads_is_the_key_the_agent_writes() -> None:
    """**Written after getting it wrong.** The first version of `offered_sources`
    read `view.grounding.candidate_sources`; `RunView` has no `grounding`
    attribute — `_grounding` is a function returning a tuple — so the field would
    have been empty on every run, forever, with nothing to notice it.

    That is the defect class this whole module was built to find, reproduced
    inside the module on its first draft. A test asserting the function returns
    `[]` for a junk input would have passed against it, which is why this asserts
    against the **agent's own serialisation** instead: `ResearchState.as_json()`
    is what actually lands in the column, so if the field is renamed this fails
    rather than quietly reporting nothing.
    """
    state = ResearchState(
        run_id=uuid.uuid4(), org_id=uuid.uuid4(), question="which outlet wastes the most?"
    )
    state.candidate_sources = ["public.fact_waste", "public.fact_sale"]

    stored = state.as_json()

    assert "candidate_sources" in stored, (
        "the agent no longer records candidate_sources under that key, so the trial "
        "report's 'sources offered' column is silently empty"
    )
    assert trial.offered_sources(stored) == ["public.fact_sale", "public.fact_waste"]


def test_offered_sources_survives_a_run_that_recorded_nothing() -> None:
    """A run that refused before context was built has no candidates, and those
    are among the runs most worth looking at. Empty, never an exception."""
    assert trial.offered_sources(None) == []
    assert trial.offered_sources({}) == []
    assert trial.offered_sources({"candidate_sources": "not a list"}) == []
