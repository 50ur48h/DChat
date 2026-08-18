"""What an answer says about itself (architecture 4.2, M9).

Limitations are the half of an answer that is easy to skip and expensive to
omit. The property these hold: they are **assembled from what the run knows**,
never asked of the model — so they cannot be talked out of, and cannot be
invented either.

Two failures are worth naming because they pull in opposite directions. A partial
answer presented as complete is what budgets were added to make visible. And a
clean answer dressed in hedging teaches people to skip the caveats, which costs
the real ones their meaning. So an empty list is a result, not a gap.
"""

from __future__ import annotations

import uuid

from dataagent.agent.composer import assemble, limitations_for, method_note
from dataagent.agent.critic import BLOCK, WARN, CriticFinding, CriticVerdict
from dataagent.agent.state import ExecutionRef, ResearchState
from dataagent.agent.tools.finalize import FinalizeIn


def _state(*executions: ExecutionRef, iteration: int = 1, tables: list[str] | None = None):
    state = ResearchState(run_id=uuid.uuid4(), org_id=uuid.uuid4(), question="q")
    state.executions = list(executions)
    state.iteration = iteration
    state.table_names = tables if tables is not None else ["public.orders"]
    return state


def _ref(name: str = "e1", *, rows: int | None = 3, ok: bool = True) -> ExecutionRef:
    return ExecutionRef(execution_id=name, row_count=rows, ok=ok, summary="a row")


def _draft(*, answered: bool = True, confidence: str = "high") -> FinalizeIn:
    return FinalizeIn(
        answer="Revenue was 1,234.00.",
        answered=answered,
        supported_by=["e1"],
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Limitations
# ---------------------------------------------------------------------------


def test_a_clean_run_has_nothing_to_add() -> None:
    """The common case, and a good one. A component that always finds something
    to say teaches people to stop reading what it says."""
    assert limitations_for(_state(_ref()), CriticVerdict(verdict="pass")) == ()


def test_a_ceiling_that_stopped_the_search_comes_first() -> None:
    """It changes how much of the answer to trust, so it leads."""
    notes = limitations_for(
        _state(_ref()),
        CriticVerdict(verdict="pass", findings=(CriticFinding("numbers", WARN, "check 9,999"),)),
        caveat="I reached the maximum number of research steps for one question.",
    )

    assert len(notes) == 2
    assert notes[0].startswith("The investigation stopped before it was finished")
    assert "research steps" in notes[0]
    assert notes[1] == "check 9,999"


def test_a_critic_warning_becomes_a_limitation() -> None:
    """This is what makes `WARN` different from a rule nobody acts on: it does
    not block, and it does not vanish either."""
    verdict = CriticVerdict(
        verdict="pass",
        findings=(CriticFinding("numbers_from_results", WARN, "9,999.00 appears in no result"),),
    )

    assert limitations_for(_state(_ref()), verdict) == ("9,999.00 appears in no result",)


def test_a_blocking_finding_that_survived_is_the_first_thing_said() -> None:
    """**Reversed by D-034, and the old reasoning is worth keeping visible.**

    This test used to assert the opposite, on the grounds that a block either
    sent the run round again or was already reflected in an answer saying what it
    could not establish. A live run disproved both halves at once (**B-079**):
    the critic blocked, the run took its one permitted re-entry, came back with
    the same shape, was blocked again — and the draft shipped claiming to have
    done precisely what the critic said it had not.

    A block that reaches this function is therefore unresolved by construction,
    and the answer is going out anyway. It goes first, in the critic's own words.
    """
    verdict = CriticVerdict(
        verdict="revise", findings=(CriticFinding("range_matches", BLOCK, "wrong month"),)
    )

    notes = limitations_for(_state(_ref()), verdict)

    assert notes
    assert "did not pass" in notes[0]
    assert "wrong month" in notes[0]


def test_insufficient_evidence_says_so_in_the_answer() -> None:
    verdict = CriticVerdict(verdict="insufficient_evidence")

    notes = limitations_for(_state(_ref()), verdict)

    assert len(notes) == 1
    assert "insufficient" in notes[0]


def test_some_queries_returning_nothing_is_worth_saying() -> None:
    notes = limitations_for(_state(_ref("e1", rows=0), _ref("e2", rows=4)), None)

    assert len(notes) == 1
    assert notes[0].startswith("1 of the queries returned no rows")


def test_every_query_returning_nothing_is_the_critic_s_business_not_a_footnote() -> None:
    """When *all* of them are empty the critic blocks and the answer says so;
    adding a limitation as well would be a footnote on a refusal."""
    assert limitations_for(_state(_ref("e1", rows=0), _ref("e2", rows=0)), None) == ()


def test_the_same_note_is_not_said_twice() -> None:
    repeated = CriticFinding("numbers", WARN, "the same worry")
    verdict = CriticVerdict(verdict="pass", findings=(repeated, repeated))

    assert limitations_for(_state(_ref()), verdict) == ("the same worry",)


# ---------------------------------------------------------------------------
# The method note
# ---------------------------------------------------------------------------


def test_the_method_says_what_was_done_in_one_line() -> None:
    """For a reader who will not read SQL — and built from the controller's own
    counts, not from a model's account of its reasoning."""
    note = method_note(_state(_ref("e1"), _ref("e2"), iteration=2, tables=["public.orders"]))

    assert note == "2 queries over 2 steps, against orders."


def test_the_method_is_singular_when_there_was_one_of_each() -> None:
    assert method_note(_state(_ref())) == "1 query over one step, against orders."


def test_several_tables_read_as_a_list() -> None:
    note = method_note(_state(_ref(), tables=["public.orders", "public.stores", "public.staff"]))

    assert "orders, stores and staff" in note


def test_a_refusal_that_ran_nothing_says_so() -> None:
    assert method_note(_state()) == "Answered without running a query."


def test_a_failed_execution_is_not_a_query_that_answered_anything() -> None:
    assert method_note(_state(_ref("e1", ok=False), _ref("e2"))) == (
        "1 query over one step, against orders."
    )


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def test_the_assembled_answer_carries_all_four_parts() -> None:
    verdict = CriticVerdict(verdict="pass", findings=(CriticFinding("n", WARN, "a caveat"),))

    composed = assemble(_draft(), _state(_ref()), verdict, citations=("e1",))

    assert composed.text == "Revenue was 1,234.00."
    assert composed.answered is True
    assert composed.citations == ("e1",)
    assert composed.method.startswith("1 query")
    assert composed.limitations == ("a caveat",)


def test_a_confidence_outside_the_three_words_becomes_medium() -> None:
    """The column has a CHECK constraint; a model that invents "very high" must
    not take the run down with it."""
    composed = assemble(_draft(confidence="very high"), _state(_ref()), None, citations=())

    assert composed.confidence == "medium"
