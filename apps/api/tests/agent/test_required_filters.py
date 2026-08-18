"""The rule that makes a definition bind (**D-033**, B-078, WP10.2c).

*Prose informs the model; a structured definition binds it.* WP10.2a let the
agent read a policy mid-run and WP10.2b made an answer that rested on one say
nothing had checked it. This is the check.

**The central criterion is here and it is about enforcement, not compliance**
(owner, 2026-08-18): the case that matters is a run where the definition's filter
is *required*, the model *drops* it, and the critic *catches* it. A run where the
model happens to comply demonstrates nothing about the constraint — which is
exactly what B-078's live run did, complying at iteration 2 and ceasing to comply
at iteration 4.

**Every rule here ships with its false-block twin** (standing note 5). A critic
that refuses a correct answer is worse than one that misses, because a fluent
refusal of a good answer teaches people the product is broken. So each blocking
case below has a partner asserting the rule stays silent on a legitimate query
near it — `status = 'completed'` being the sharp one, since it honours "exclude
cancelled orders" without containing the word.
"""

from __future__ import annotations

import uuid
from datetime import date

from dataagent.agent import critic
from dataagent.agent.state import ExecutionRef, ResearchState
from dataagent.agent.tools.finalize import FinalizeIn
from dataagent.semantic.definitions import Definition, RequiredFilter

EXECUTION = "11111111-1111-1111-1111-111111111111"

NET_REVENUE = Definition(
    id=uuid.uuid4(),
    name="net_revenue",
    kind="metric",
    description="Revenue excluding cancelled and refunded orders.",
    expression="sum(orders.total_amount)",
    required_filters=(
        RequiredFilter(
            table="orders", column="status", op="not_in", values=("cancelled", "refunded")
        ),
    ),
    synonyms=("net revenue",),
)


def _evidence(
    statement: str, *, definitions: tuple[Definition, ...] = (NET_REVENUE,)
) -> critic.Evidence:
    state = ResearchState(run_id=uuid.uuid4(), org_id=uuid.uuid4())
    state.question = "What was net revenue last month?"
    # A real execution on the state, so `_citations_resolve` is satisfied and
    # what these tests observe is this rule rather than that one.
    state.record_execution(
        ExecutionRef(
            execution_id=EXECUTION,
            purpose="net revenue",
            sql_hash="abc123",
            row_count=1,
            summary="net revenue: 1234",
            ok=True,
        )
    )
    return critic.Evidence(
        question=state.question,
        as_of=date(2026, 8, 1),
        state=state,
        statements={EXECUTION: statement},
        definitions=definitions,
    )


def _draft() -> FinalizeIn:
    return FinalizeIn(
        answer="Net revenue was 1,234.", answered=True, supported_by=[EXECUTION], confidence="high"
    )


def _rules(findings: tuple[critic.CriticFinding, ...]) -> set[str]:
    return {finding.rule for finding in findings}


# ---------------------------------------------------------------------------
# The central criterion: the model drops the filter and the critic catches it
# ---------------------------------------------------------------------------


def test_a_query_that_ignores_a_required_filter_is_blocked() -> None:
    """**The criterion.** The definition requires the query to constrain
    `status`; this one never mentions it, so the number is for a different
    population than the metric names. Not a style judgement — arithmetic."""
    findings = critic.check(
        _draft(), _evidence("SELECT sum(total_amount) AS net_revenue FROM orders")
    )

    assert "required_filter_missing" in _rules(findings)
    blocked = next(f for f in findings if f.rule == "required_filter_missing")
    assert blocked.severity == critic.BLOCK
    assert "net_revenue" in blocked.detail
    assert "orders.status" in blocked.detail


def test_the_block_names_what_the_definition_required() -> None:
    """A finding a person cannot act on is a finding that gets ignored. It has
    to say which metric, which column, and what the definition asked for."""
    findings = critic.check(
        _draft(), _evidence("SELECT sum(total_amount) AS net_revenue FROM orders")
    )

    detail = next(f for f in findings if f.rule == "required_filter_missing").detail
    assert "none of" in detail
    assert "cancelled" in detail and "refunded" in detail


# ---------------------------------------------------------------------------
# The false-block twins (standing note 5)
# ---------------------------------------------------------------------------


def test_the_definition_written_exactly_passes() -> None:
    findings = critic.check(
        _draft(),
        _evidence(
            "SELECT sum(total_amount) AS net_revenue FROM orders "
            "WHERE status NOT IN ('cancelled', 'refunded')"
        ),
    )

    assert not {rule for rule in _rules(findings) if rule.startswith("required_filter")}


def test_the_same_rule_spelled_differently_passes() -> None:
    """`<>` twice is `NOT IN` once. A check that insisted on one spelling would
    refuse the other, which is the false block this rule is most exposed to."""
    findings = critic.check(
        _draft(),
        _evidence(
            "SELECT sum(total_amount) AS net_revenue FROM orders "
            "WHERE status <> 'cancelled' AND status <> 'refunded'"
        ),
    )

    assert "required_filter_missing" not in _rules(findings)


def test_a_positive_filter_that_honours_the_definition_warns_but_does_not_block() -> None:
    """**The sharpest case, and the reason the rule has two strengths.**

    `status = 'completed'` excludes cancelled and refunded orders without
    containing either word. It is very likely correct, so blocking it would
    refuse a good answer — and it is *not certainly* correct, because it also
    excludes anything else the column holds. A warning is exactly that weight,
    and it travels into the answer as a limitation rather than stopping the run.
    """
    findings = critic.check(
        _draft(),
        _evidence("SELECT sum(total_amount) AS net_revenue FROM orders WHERE status = 'completed'"),
    )

    assert "required_filter_missing" not in _rules(findings)
    warning = next(f for f in findings if f.rule == "required_filter_differs")
    assert warning.severity == critic.WARN


def test_a_filter_applied_inside_a_cte_counts() -> None:
    """A filter applied in a CTE is applied. Reading only the outer `WHERE`
    would block a perfectly ordinary way to write the same query."""
    findings = critic.check(
        _draft(),
        _evidence(
            "WITH billable AS ("
            "  SELECT * FROM orders WHERE status NOT IN ('cancelled', 'refunded')"
            ") SELECT sum(total_amount) AS net_revenue FROM billable"
        ),
    )

    assert "required_filter_missing" not in _rules(findings)


def test_a_query_that_does_not_touch_the_table_is_left_alone() -> None:
    """A run that never used the metric cannot have misused it. Without this the
    rule would block every query in a run that happened to mention the word."""
    findings = critic.check(_draft(), _evidence("SELECT count(*) AS shops FROM shops"))

    assert not {rule for rule in _rules(findings) if rule.startswith("required_filter")}


def test_no_definition_means_no_rule() -> None:
    """The common case. Most questions are about rows rather than about a
    defined measure, and this rule must be invisible to them."""
    findings = critic.check(
        _draft(),
        _evidence("SELECT sum(total_amount) AS total FROM orders", definitions=()),
    )

    assert not {rule for rule in _rules(findings) if rule.startswith("required_filter")}


def test_an_answer_citing_nothing_is_not_judged_on_its_filters() -> None:
    """A draft with no citations has other problems, and `_citations_resolve`
    reports them. Adding a second complaint in a different vocabulary would read
    as two faults where there is one."""
    draft = FinalizeIn(
        answer="I could not establish that.", answered=False, supported_by=[], confidence="low"
    )

    findings = critic.check(draft, _evidence("SELECT sum(total_amount) FROM orders"))

    assert "required_filter_missing" not in _rules(findings)


# ---------------------------------------------------------------------------
# Matching, which decides whether the rule applies at all
# ---------------------------------------------------------------------------


def test_a_definition_matches_the_words_a_person_actually_types() -> None:
    from dataagent.semantic.definitions import matching

    assert matching([NET_REVENUE], "What was net revenue last month?") == (NET_REVENUE,)
    assert matching([NET_REVENUE], "what was our net_revenue?") == (NET_REVENUE,)


def test_a_definition_does_not_match_a_question_it_is_not_about() -> None:
    """The false-block twin for matching, one layer up. A definition applied to
    the wrong question becomes a critic rule enforcing a filter the answer never
    needed — the same failure, arriving before the critic runs."""
    from dataagent.semantic.definitions import matching

    assert matching([NET_REVENUE], "How many shops are there?") == ()
    assert matching([NET_REVENUE], "What was gross revenue?") == ()


# ---------------------------------------------------------------------------
# D-033's seam: a defined term stops being an unverifiable one
# ---------------------------------------------------------------------------


def test_a_defined_term_no_longer_carries_the_prose_limitation() -> None:
    """**The seam between the two halves of D-033, asserted.**

    WP10.2b gives an answer that leaned on a document a limitation saying nothing
    checked it. That is true right up until the term has a definition — at which
    point the critic *does* check it, and repeating the caveat would be a false
    warning about the one case the layer actually handles. This is what "the
    limitation goes away when an Admin blesses the passage into a definition"
    means in code.
    """
    from dataagent.agent.composer import limitations_for

    state = ResearchState(run_id=uuid.uuid4(), org_id=uuid.uuid4())
    state.prose_terms = ["net revenue"]

    before = limitations_for(state, None)
    state.applied_definitions = ["net_revenue"]
    after = limitations_for(state, None)

    assert any("read as prose" in note for note in before)
    assert not any("read as prose" in note for note in after)


def test_a_term_with_no_definition_still_carries_it() -> None:
    """The twin. Blessing one metric must not silence the caveat for another —
    the answer would then claim a check nobody ran on the term that needed it."""
    from dataagent.agent.composer import limitations_for

    state = ResearchState(run_id=uuid.uuid4(), org_id=uuid.uuid4())
    state.prose_terms = ["net revenue", "anchor order"]
    state.applied_definitions = ["net_revenue"]

    notes = limitations_for(state, None)

    assert any("anchor order" in note for note in notes)
    assert not any("net revenue" in note for note in notes)


# ---------------------------------------------------------------------------
# A block that could not be acted on reaches the reader (D-034, B-079)
# ---------------------------------------------------------------------------


def _blocked_verdict() -> critic.CriticVerdict:
    return critic.CriticVerdict(
        verdict="revise",
        findings=(
            critic.CriticFinding(
                rule="checklist",
                severity=critic.BLOCK,
                detail="The query does not exclude cancelled and refunded orders.",
            ),
        ),
        consulted_model=True,
    )


def test_an_unresolved_block_becomes_the_answers_first_limitation() -> None:
    """**The gate criterion (D-034).**

    A block that survives to the composer is unresolved by definition: the one
    permitted re-entry has happened or was unavailable, and the answer is going
    out regardless. It was silent before — `limitations_for` read only warnings —
    which is how a live run shipped saying it had "explicitly excluded cancelled
    and refunded orders" moments after the critic said it had not.
    """
    from dataagent.agent.composer import limitations_for

    state = ResearchState(run_id=uuid.uuid4(), org_id=uuid.uuid4())

    notes = limitations_for(state, _blocked_verdict(), caveat="the iteration ceiling was reached")

    assert notes, "an unresolved block left no trace in the answer"
    assert "did not pass" in notes[0]
    assert "cancelled and refunded" in notes[0]


def test_the_block_outranks_the_budget_caveat() -> None:
    """Ordering is the substance here, not presentation. A ceiling says the
    answer is *incomplete*; a block says it may be *wrong*. A reader who stops
    after the first line must have read the second kind."""
    from dataagent.agent.composer import limitations_for

    state = ResearchState(run_id=uuid.uuid4(), org_id=uuid.uuid4())

    notes = limitations_for(state, _blocked_verdict(), caveat="the iteration ceiling was reached")

    block_at = next(i for i, note in enumerate(notes) if "did not pass" in note)
    caveat_at = next(i for i, note in enumerate(notes) if "stopped before it was finished" in note)
    assert block_at < caveat_at


def test_a_passing_review_adds_no_such_note() -> None:
    """The twin. A run the critic passed must not be made to sound disputed —
    that would be the false warning that teaches readers to skip warnings."""
    from dataagent.agent.composer import limitations_for

    state = ResearchState(run_id=uuid.uuid4(), org_id=uuid.uuid4())
    passed = critic.CriticVerdict(verdict="pass", findings=(), consulted_model=True)

    assert not any("did not pass" in note for note in limitations_for(state, passed))


def test_a_disputed_draft_cannot_call_itself_highly_confident() -> None:
    """The model is the party whose work is in question, so its own `high` does
    not survive a block. Capped rather than forced to `low`: a block is a reason
    to doubt an answer, not a reason to assert it is wrong."""
    from dataagent.agent.composer import assemble

    state = ResearchState(run_id=uuid.uuid4(), org_id=uuid.uuid4())
    draft = FinalizeIn(
        answer="Net revenue was 1,234.", answered=True, supported_by=[], confidence="high"
    )

    disputed = assemble(draft, state, _blocked_verdict(), citations=())
    accepted = assemble(draft, state, critic.CriticVerdict(verdict="pass"), citations=())

    assert disputed.confidence == "medium"
    assert accepted.confidence == "high"
