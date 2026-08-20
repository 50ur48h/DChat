"""What a run remembers between iterations (architecture 4.2).

The properties here are the ones a loop makes matter. A single-shot run held one
execution and one answer, so most of these could not be got wrong; a loop
accumulates, and every accumulation is a chance to grow the prompt without
bound, to keep a citation nobody can resolve, or to mistake repetition for
progress.
"""

from __future__ import annotations

import uuid

from dataagent.agent.state import (
    ExecutionRef,
    Hypothesis,
    ResearchState,
    StateFinding,
    Step,
    merge_by_evidence,
)

ORG = uuid.UUID("11111111-1111-1111-1111-111111111111")
RUN = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _state(**overrides: object) -> ResearchState:
    return ResearchState(run_id=RUN, org_id=ORG, question="How many orders?", **overrides)  # pyright: ignore[reportArgumentType]


def _ran(state: ResearchState, execution_id: str, sql_hash: str = "") -> None:
    state.record_execution(
        ExecutionRef(execution_id=execution_id, sql_hash=sql_hash, summary="1 row", row_count=1)
    )


# ---------------------------------------------------------------------------
# Surviving a restart
# ---------------------------------------------------------------------------


def test_a_checkpoint_reads_back_as_what_was_written() -> None:
    """The whole point of a checkpoint is being read back. Asserted as a
    round-trip rather than field by field, so a field added later is covered
    without anybody remembering to extend this."""
    state = _state(phase="reflecting", iteration=3)
    _ran(state, "x1", "hash-1")
    state.plan.append(Step(purpose="count orders", sql="SELECT 1", status="done"))
    state.hypotheses.append(Hypothesis(text="July was busier", status="open"))
    state.open_questions.append("which store?")
    assert state.add_finding(StateFinding(statement="3,718 orders", support=["x1"]))

    restored = ResearchState.restore(state.as_json())

    assert restored is not None
    assert restored == state


def test_a_state_from_an_older_build_is_reportable_rather_than_a_crash() -> None:
    """None rather than raising: a run whose checkpoint predates this shape must
    leave the resume path a decision to make, not an exception to handle."""
    assert ResearchState.restore(None) is None
    assert ResearchState.restore("not a checkpoint") is None
    assert ResearchState.restore({"run_id": "not-a-uuid"}) is None
    assert ResearchState.restore({}) is None


def test_the_fields_later_phases_own_are_already_in_the_shape() -> None:
    """`capability` is WP8.2's and `critic` is WP9.1's. They exist now, empty, so
    a checkpoint written today is still readable by the code that adds them — a
    field that appeared later would make every stored state unreadable."""
    recorded = _state().as_json()

    assert recorded["capability"] == {}
    assert recorded["critic"] is None


# ---------------------------------------------------------------------------
# Not going round in circles
# ---------------------------------------------------------------------------


def test_the_same_query_twice_is_recognised() -> None:
    """4.4's duplicate rule. A loop out of ideas re-runs its best query, paying a
    query budget to learn what it already knew."""
    state = _state()
    _ran(state, "x1", "abc123")

    assert state.has_run("abc123")
    assert not state.has_run("def456")


def test_a_statement_with_no_hash_is_never_called_a_duplicate() -> None:
    """A refused statement never became a validated query and has no hash;
    treating the empty string as "already run" would block every later attempt."""
    state = _state()
    _ran(state, "x1", "")

    assert not state.has_run("")


def test_the_same_finding_twice_is_not_progress() -> None:
    """Otherwise a model keeps the loop alive by repeating itself, and the
    monotone-progress rule never fires."""
    state = _state()
    _ran(state, "x1", "abc")

    assert state.add_finding(StateFinding(statement="3,718 orders", support=["x1"]))
    assert not state.add_finding(StateFinding(statement="  3,718 orders  ", support=["x1"]))
    assert len(state.findings) == 1


# ---------------------------------------------------------------------------
# The spine of trust (4.2)
# ---------------------------------------------------------------------------


def test_a_citation_this_run_did_not_produce_is_dropped() -> None:
    """A model that cites an execution it did not run is completing a pattern.
    The result is the same either way: a citation that looks checkable and is
    not."""
    state = _state()
    _ran(state, "x1", "abc")

    assert state.add_finding(StateFinding(statement="July was busy", support=["x1", "invented"]))

    assert state.findings[0].support == ["x1"]


def test_a_finding_whose_every_citation_is_invented_is_refused() -> None:
    """Keeping it would put an unsupported claim in front of a person wearing the
    same clothes as a supported one — and 4.2 makes `support` the reason anyone
    should believe the answer at all."""
    state = _state()
    _ran(state, "x1", "abc")

    kept = state.add_finding(StateFinding(statement="Revenue doubled", support=["nope"]))

    assert kept is False
    assert state.findings == []


def test_a_finding_that_cites_nothing_is_allowed_through() -> None:
    """An observation with no query behind it is legitimate — "the catalog has no
    order_items table" is a finding — and the UI already says "no supporting
    query" rather than implying evidence that is not there."""
    state = _state()

    assert state.add_finding(StateFinding(statement="No linking table exists", support=[]))
    assert state.findings[0].support == []


def test_the_ids_a_citation_is_checked_against_are_the_ones_really_run() -> None:
    state = _state()
    _ran(state, "x1", "a")
    _ran(state, "x2", "b")

    assert state.execution_ids() == ("x1", "x2")


def test_rows_are_never_carried_in_the_state() -> None:
    """4.4's Observe step keeps a summary and a reference; the rows stay in
    `result_artifacts`, masked, where a citation can reach them. A loop that
    accumulated result sets would grow its own prompt every iteration — and
    would be carrying customer data somewhere nothing masks it."""
    fields = set(ExecutionRef.model_fields)

    assert "rows" not in fields
    assert "sample_rows" not in fields
    assert {"execution_id", "summary", "row_count"} <= fields


# ---------------------------------------------------------------------------
# One claim per set of evidence (B-107)
# ---------------------------------------------------------------------------

#: The two sentences one reflection recorded about one query, in the order the
#: model emitted them, on the run that produced this rule.
_ENUMERATION = (
    "Monthly revenue for the last four completed calendar months was $135,950.59 in "
    "April 2026, $145,341.12 in May, $123,650.61 in June, and $122,712.33 in July."
)
_SHAPE = "Revenue peaked in May 2026 and then declined in both June and July."


def test_two_sentences_about_one_query_become_one_claim() -> None:
    """**The visible defect**: two confidence badges and two "show the query"
    controls over a single execution. B-096 fixed that between the loop and the
    composer and stopped there; this is the other place a finding is recorded."""
    merged = merge_by_evidence(
        [
            StateFinding(statement=_ENUMERATION, support=["x1"], confidence="high"),
            StateFinding(statement=_SHAPE, support=["x1"], confidence="high"),
        ]
    )

    assert len(merged) == 1
    assert merged[0].support == ["x1"]


def test_the_merge_keeps_both_sentences_rather_than_choosing() -> None:
    """**Why this joins instead of dropping.** Keyed on evidence and keeping the
    first, the rule would keep the enumeration and discard the shape — the worse
    of the two by B-097's own reckoning, which says the prose should give the
    shape and let a chart carry the detail. Keeping the *last* would be right
    here and wrong the moment a model emitted them the other way round. Evidence
    cannot rank two sentences, so it is not asked to."""
    merged = merge_by_evidence(
        [
            StateFinding(statement=_ENUMERATION, support=["x1"]),
            StateFinding(statement=_SHAPE, support=["x1"]),
        ]
    )

    assert _ENUMERATION in merged[0].statement
    assert _SHAPE in merged[0].statement


def test_a_merged_claim_is_as_strong_as_its_weakest_part() -> None:
    merged = merge_by_evidence(
        [
            StateFinding(statement="A.", support=["x1"], confidence="high"),
            StateFinding(statement="B.", support=["x1"], confidence="low"),
        ]
    )

    assert merged[0].confidence == "low"


def test_findings_on_different_evidence_are_different_claims() -> None:
    merged = merge_by_evidence(
        [
            StateFinding(statement="A.", support=["x1"]),
            StateFinding(statement="B.", support=["x2"]),
            # A synthesis over more evidence is nobody else's claim.
            StateFinding(statement="C.", support=["x1", "x2"]),
        ]
    )

    assert len(merged) == 3


def test_uncited_findings_are_never_merged_into_each_other() -> None:
    """They share the empty set with every other uncited finding, so keying on
    evidence here would collapse every unsupported sentence in a run into the
    first one — a rule about evidence, applied where there is none."""
    merged = merge_by_evidence(
        [
            StateFinding(statement="No linking table exists.", support=[]),
            StateFinding(statement="The catalog has six tables.", support=[]),
        ]
    )

    assert len(merged) == 2


def test_the_order_of_the_citations_does_not_make_a_second_claim() -> None:
    merged = merge_by_evidence(
        [
            StateFinding(statement="A.", support=["x1", "x2"]),
            StateFinding(statement="B.", support=["x2", "x1"]),
        ]
    )

    assert len(merged) == 1


def test_a_later_reflection_restating_on_the_same_evidence_is_not_progress() -> None:
    """The cross-iteration half, where merging is not available: a finding row
    and its `finding_added` event are written together, so a merge after the
    fact would have to rewrite a row whose event already said something else.
    The earlier finding stands, and the iteration counts as barren — which is
    the honest reading, since nothing new was asked of the data."""
    state = _state()
    _ran(state, "x1", "abc")

    assert state.add_finding(StateFinding(statement="3,718 orders in July.", support=["x1"]))
    assert not state.add_finding(
        StateFinding(statement="July saw three thousand orders.", support=["x1"])
    )
    assert len(state.findings) == 1


def test_a_finding_over_more_evidence_is_a_new_claim() -> None:
    """Equality of the citation set, never overlap: an answer that synthesises
    two findings has a union nobody else cites, and `_write_ending` says so in as
    many words. A rule keyed on overlap would silence exactly the claims worth
    making."""
    state = _state()
    _ran(state, "x1", "a")
    _ran(state, "x2", "b")

    assert state.add_finding(StateFinding(statement="April was highest.", support=["x1"]))
    assert state.add_finding(
        StateFinding(statement="April beat every store.", support=["x1", "x2"])
    )
    assert len(state.findings) == 2


def test_uncited_findings_still_accumulate_across_reflections() -> None:
    """The guard above must not fire on the empty set, or the second observation
    a run makes without a query behind it would silently vanish."""
    state = _state()

    assert state.add_finding(StateFinding(statement="No linking table exists.", support=[]))
    assert state.add_finding(StateFinding(statement="The catalog has six tables.", support=[]))
    assert len(state.findings) == 2
