"""The critic: what arithmetic can settle, and what it cannot (architecture 4.5).

The flagship is M9's own acceptance line — **a seeded wrong-date-range draft is
caught deterministically** — and the part of it that matters most is the second
half: *with no model call at all*. A critic that needs a model to notice that a
question about July was answered from June's rows has not saved anything; it has
added a call and a second opinion about arithmetic.

The stage-1 tests are pure and need nothing. The wiring tests go through
`execute_run` against the real fixture, because "the re-entry happened exactly
once" is a claim about statuses, events and budget, and only the runner settles it.
"""

from __future__ import annotations

import uuid
from datetime import date

from dataagent.agent.critic import (
    BLOCK,
    WARN,
    CriticFinding,
    CriticOut,
    Evidence,
    check,
    combine,
    stated_range,
)
from dataagent.agent.state import ExecutionRef, ResearchState
from dataagent.agent.tools.finalize import FinalizeIn

AS_OF = date(2026, 8, 16)
RUN = uuid.uuid4()
ORG = uuid.uuid4()


def _state(
    *executions: ExecutionRef,
    question: str = "q",
    capability: dict[str, object] | None = None,
) -> ResearchState:
    state = ResearchState(run_id=RUN, org_id=ORG, question=question, as_of=AS_OF.isoformat())
    state.executions = list(executions)
    if capability:
        state.capability = capability
    return state


def _ref(execution_id: str, *, rows: int = 3, ok: bool = True) -> ExecutionRef:
    return ExecutionRef(execution_id=execution_id, row_count=rows, ok=ok, summary="1 row")


def _draft(
    answer: str = "Revenue was 1,234.00.",
    *,
    unanswered: str = "",
    cites: tuple[str, ...] = ("e1",),
) -> FinalizeIn:
    return FinalizeIn(answer=answer, unanswered=unanswered, supported_by=list(cites))


def _evidence(
    state: ResearchState,
    statements: dict[str, str] | None = None,
    previews: tuple[tuple[str, str], ...] = (),
    question: str | None = None,
) -> Evidence:
    return Evidence(
        question=question if question is not None else state.question,
        as_of=AS_OF,
        state=state,
        statements=statements or {},
        previews=previews,
    )


# ---------------------------------------------------------------------------
# Reading a period out of a question (D-027 made this possible)
# ---------------------------------------------------------------------------


def test_a_named_month_is_a_period() -> None:
    found = stated_range("How many orders were placed in July 2026?", AS_OF)

    assert found is not None
    assert (found.start, found.end) == (date(2026, 7, 1), date(2026, 8, 1))


def test_a_relative_period_resolves_against_this_run_s_anchor() -> None:
    """Before D-027 this had no answer at all: "last month" relative to what?"""
    found = stated_range("What was revenue last full month?", AS_OF)

    assert found is not None
    assert (found.start, found.end) == (date(2026, 7, 1), date(2026, 8, 1))


def test_a_question_with_no_period_states_none() -> None:
    """And the check then does not fire. A critic that misparses a question and
    blocks on its own mistake is worse than one that says nothing."""
    assert stated_range("Which outlet sells the most?", AS_OF) is None


def test_a_period_is_covered_by_either_spelling_of_its_end() -> None:
    """`< '2026-08-01'` and `<= '2026-07-31'` are the same range, and demanding
    one of them would fail correct SQL."""
    found = stated_range("orders in July 2026", AS_OF)

    assert found is not None
    assert found.covered_by(("2026-07-01", "2026-08-01"))
    assert found.covered_by(("2026-07-01", "2026-07-31"))
    assert not found.covered_by(("2026-06-01", "2026-07-01"))


# ---------------------------------------------------------------------------
# The flagship: a wrong date range, caught by arithmetic
# ---------------------------------------------------------------------------


def test_a_draft_answering_the_wrong_month_is_blocked() -> None:
    """M9's acceptance line. The question says July; the statement filtered June.

    Nothing about the answer looks wrong — it is fluent, it cites a real
    execution, and the number in it is genuinely the number that query returned.
    That is exactly why a rule has to catch it.
    """
    state = _state(_ref("e1"), question="How many orders were placed in July 2026?")
    evidence = _evidence(
        state,
        statements={
            "e1": (
                "SELECT count(*) FROM orders WHERE order_date >= CAST('2026-06-01' AS DATE) "
                "AND order_date < CAST('2026-07-01' AS DATE)"
            )
        },
    )

    findings = check(_draft("There were 3,510 orders."), evidence)

    assert [finding.rule for finding in findings] == ["range_matches"]
    assert findings[0].severity == BLOCK
    assert "2026-07-01" in findings[0].detail, "the refusal names the period asked for"
    assert "2026-06-01" in findings[0].detail, "and the period actually used"


def test_the_right_month_passes() -> None:
    state = _state(_ref("e1"), question="How many orders were placed in July 2026?")
    evidence = _evidence(
        state,
        statements={
            "e1": (
                "SELECT count(*) FROM orders WHERE order_date >= CAST('2026-07-01' AS DATE) "
                "AND order_date < CAST('2026-08-01' AS DATE)"
            )
        },
    )

    assert check(_draft("There were 3,718 orders."), evidence) == ()


def test_a_query_with_no_dates_is_not_a_wrong_range() -> None:
    """A statement filtering on something else is not evidence of a bad period,
    and reading it as one would block correct answers."""
    state = _state(_ref("e1"), question="How many orders were placed in July 2026?")
    evidence = _evidence(state, statements={"e1": "SELECT count(*) FROM orders"})

    assert check(_draft(), evidence) == ()


def test_a_refusal_is_not_checked_for_a_range() -> None:
    """`answered=false` is an explanation, not a claim about a period."""
    state = _state(_ref("e1"), question="How many orders were placed in July 2026?")
    evidence = _evidence(state, statements={"e1": "SELECT 1 WHERE '2020-01-01' = '2020-01-01'"})

    assert check(_draft(unanswered="the period", cites=()), evidence) == ()


# ---------------------------------------------------------------------------
# The other deterministic rules
# ---------------------------------------------------------------------------


def test_a_citation_this_run_never_produced_is_blocked() -> None:
    """A citation nobody can resolve looks exactly like evidence while being
    none, which is what 4.2's support list exists to prevent."""
    evidence = _evidence(_state(_ref("e1")))

    findings = check(_draft(cites=("e1", "e9")), evidence)

    assert [finding.rule for finding in findings] == ["citation_resolves"]
    assert findings[0].severity == BLOCK
    assert "e9" in findings[0].detail


def test_an_answer_built_only_on_empty_results_is_blocked() -> None:
    """ "There were none" is a fine answer and `answered=false` is how it is
    said. A positive claim resting on nothing is not."""
    evidence = _evidence(_state(_ref("e1", rows=0)))

    findings = check(_draft("Revenue was 1,234.00."), evidence)

    assert [finding.rule for finding in findings] == ["row_count_sanity"]
    assert findings[0].severity == BLOCK


def test_one_empty_result_among_several_is_not_a_block() -> None:
    """An investigation asks questions that come back empty; that is research,
    not a defect."""
    evidence = _evidence(_state(_ref("e1", rows=0), _ref("e2", rows=5)))

    assert check(_draft(cites=("e1", "e2")), evidence) == ()


def test_a_figure_in_no_result_is_a_warning_and_not_a_block() -> None:
    """Architecture 4.5 is explicit — *"violations become warnings in V1"*.

    Prose rounds and computes: a difference between two returned figures appears
    in neither, and blocking on it would refuse correct arithmetic.
    """
    evidence = _evidence(_state(_ref("e1")), previews=(("e1", "revenue: 1234.00\ncount: 42"),))

    findings = check(_draft("Revenue was 9,999.00 across 42 orders."), evidence)

    assert [finding.rule for finding in findings] == ["numbers_from_results"]
    assert findings[0].severity == WARN
    assert "9,999.00" in findings[0].detail


def test_a_figure_that_matches_a_result_passes() -> None:
    evidence = _evidence(_state(_ref("e1")), previews=(("e1", "revenue: 1234.00\ncount: 42"),))

    assert check(_draft("Revenue was 1,234.00 across 42 orders."), evidence) == ()


def test_small_numbers_in_prose_are_not_claims() -> None:
    """ "the top 5", "two stores" — ordinal and structural, not figures taken
    from a result, and checking them produces noise."""
    evidence = _evidence(_state(_ref("e1")), previews=(("e1", "revenue: 1234.00"),))

    assert check(_draft("The top 3 of 5 stores made 1,234.00."), evidence) == ()


def test_a_number_the_question_itself_stated_is_not_a_claim() -> None:
    """ "July 2026" puts 2026 into the answer, and warning that the year appears
    in no result is noise a reader would have to learn to ignore. Found on the
    first live question after this shipped, not by the suite."""
    state = _state(_ref("e1"), question="How many orders were placed in July 2026?")
    evidence = _evidence(state, previews=(("e1", "order_count: 3718"),))

    assert check(_draft("3718 orders were placed in July 2026."), evidence) == ()


def test_a_claim_over_a_statement_that_was_refused_is_blocked() -> None:
    """`answerable: false` is written by the per-statement check when it actually
    refuses one."""
    state = _state(_ref("e1"), capability={"answerable": False, "verdicts": []})

    findings = check(_draft(), _evidence(state))

    assert [finding.rule for finding in findings] == ["capability_respected"]


def test_a_catalog_that_merely_has_a_gap_does_not_block_anything() -> None:
    """Almost every real schema has some pair that cannot be joined. Blocking on
    the presence of one would refuse answers to questions that never went near
    it — the false block this component must not produce."""
    state = _state(_ref("e1"), capability={"unreachable": [{"left": "a", "right": "b"}]})

    assert check(_draft(), _evidence(state)) == ()


def test_the_semantic_filter_hook_is_inert_and_says_so() -> None:
    """WP10.2 fills this in. Until then an organization's rule that "revenue
    excludes cancelled orders" is enforced by nothing here — the hook is not the
    feature (B-038)."""
    findings = check(_draft(), _evidence(_state(_ref("e1"))))

    assert [f.rule for f in findings if f.rule == "required_filters"] == []


# ---------------------------------------------------------------------------
# Combining the two stages
# ---------------------------------------------------------------------------


def test_a_deterministic_block_decides_alone_and_the_model_is_never_asked() -> None:
    """The property that makes stage 1 "free": the call it saves is the cost."""
    blocking = (CriticFinding(rule="range_matches", severity=BLOCK, detail="wrong month"),)

    verdict = combine(blocking, model=None)

    assert verdict.verdict == "revise"
    assert verdict.consulted_model is False
    assert verdict.blocked is True


def test_warnings_alone_do_not_block() -> None:
    warnings = (CriticFinding(rule="numbers_from_results", severity=WARN, detail="check 9,999"),)

    verdict = combine(warnings, model=CriticOut(verdict="pass", reasons=[]))

    assert verdict.verdict == "pass"
    assert verdict.blocked is False
    assert verdict.warnings == warnings
    assert verdict.consulted_model is True


def test_the_model_can_block_what_arithmetic_passed() -> None:
    verdict = combine(
        (), model=CriticOut(verdict="revise", reasons=["Correlation is offered as cause."])
    )

    assert verdict.verdict == "revise"
    assert verdict.blocking[0].detail == "Correlation is offered as cause."
    assert "Correlation" in verdict.reasons()


def test_no_model_and_no_findings_is_a_pass_that_says_nobody_looked() -> None:
    """A run whose budget stopped stage 2 passed the rules and was not reviewed,
    and the trace has to be able to tell those apart."""
    verdict = combine((), model=None)

    assert verdict.verdict == "pass"
    assert verdict.consulted_model is False
