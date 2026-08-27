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

from dataagent.agent.capability import (
    JoinGraph,
    decode_inferred_joins,
    encode_inferred_joins,
)
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


UNREACHABLE = "could not connect to the data source"


def _failed(*, error: str = UNREACHABLE) -> ExecutionRef:
    """A query that was sent and never came back.

    `error` empty is the *repairable* refusal — a statement the policy rejected,
    which the next planner is told about and routinely corrects. The loop makes
    that distinction when it records the execution; these tests are what holds
    it in place.
    """
    return ExecutionRef(
        execution_id="", ok=False, summary=f"refused (engine_error): {error}", error=error
    )


def _draft(*, unanswered: str = "", confidence: str = "high") -> FinalizeIn:
    return FinalizeIn(
        answer="Revenue was 1,234.00.",
        unanswered=unanswered,
        supported_by=["e1"],
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Limitations
# ---------------------------------------------------------------------------


def _sourced(*, read: list[str], candidates: list[str], ok: bool = True) -> ResearchState:
    """A run that was offered `candidates` and read `read`."""
    state = _state(
        ExecutionRef(execution_id="e1", row_count=3, ok=ok, summary="a row", tables=read)
    )
    state.candidate_sources = candidates
    return state


def test_an_answer_says_which_source_it_came_from_when_another_was_available() -> None:
    """**B-093, and the reason it exists is B-060.** Asked which raw ingredients
    cost the most, the agent was handed a purchase ledger *and* a stock-movement
    table, used one, and said nothing — while the two disagree by more than a
    factor of a hundred depending on which filter you believe. The SQL was fine
    and cited correctly; what was missing is that a choice existed.
    """
    notes = limitations_for(
        _sourced(
            read=["public.fact_purchase"],
            candidates=["public.fact_purchase", "public.fact_stock_move"],
        ),
        CriticVerdict(verdict="pass"),
    )

    assert len(notes) == 1
    assert "public.fact_purchase" in notes[0]
    assert "public.fact_stock_move" in notes[0]
    assert "A different source can give a different number" in notes[0]


def test_the_note_states_the_choice_and_does_not_judge_it() -> None:
    """The run has no way to know the other source would disagree without
    running it, so the sentence claims only that an alternative existed."""
    notes = limitations_for(
        _sourced(read=["public.a"], candidates=["public.a", "public.b"]),
        CriticVerdict(verdict="pass"),
    )

    assert "wrong" not in notes[0] and "incorrect" not in notes[0]
    assert "could have been answered from" in notes[0]


def test_a_question_with_one_source_says_nothing() -> None:
    """Most runs. A note on every answer is a note nobody reads — which is the
    same argument the clean-run test above makes."""
    assert (
        limitations_for(
            _sourced(read=["public.orders"], candidates=["public.orders"]),
            CriticVerdict(verdict="pass"),
        )
        == ()
    )


def test_reading_every_source_offered_says_nothing() -> None:
    """Nothing was passed over, so there is no choice to disclose."""
    assert (
        limitations_for(
            _sourced(read=["public.a", "public.b"], candidates=["public.a", "public.b"]),
            CriticVerdict(verdict="pass"),
        )
        == ()
    )


def test_a_dimension_the_answer_did_not_read_is_not_an_alternative() -> None:
    """`candidate_sources` holds only tables with figures to aggregate, so a
    dimension table that was retrieved and not read never reaches this. Asserted
    here because the alternative — every unused table named — would put a
    warning on every answer in the product."""
    state = _sourced(read=["public.fact_purchase"], candidates=["public.fact_purchase"])
    state.table_names = ["public.fact_purchase", "public.dim_ingredient"]

    assert limitations_for(state, CriticVerdict(verdict="pass")) == ()


def test_a_run_that_read_nothing_makes_no_claim_about_sources() -> None:
    """A refused query read no table, so there is no "this answer reads X" to
    write and nothing to compare it against."""
    state = _sourced(read=[], candidates=["public.a", "public.b"], ok=False)

    notes = limitations_for(state, CriticVerdict(verdict="pass"))

    assert not any("could have been answered from" in note for note in notes)


def test_only_two_alternatives_are_named() -> None:
    """A broad retrieval is a broad retrieval, not five competing answers, and a
    sentence listing all of them is one nobody finishes."""
    notes = limitations_for(
        _sourced(read=["public.a"], candidates=["public.a", "public.b", "public.c", "public.d"]),
        CriticVerdict(verdict="pass"),
    )

    assert "and others" in notes[0]
    assert "public.d" not in notes[0]


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


# ---------------------------------------------------------------------------
# Queries that never reached the database (**B-095**)
# ---------------------------------------------------------------------------


def test_a_run_whose_queries_all_failed_says_so_instead_of_saying_nothing() -> None:
    """**B-095.** Seen live: both executions ended in `gaierror` — the platform
    never reached the database — and the answer read *"no data was returned from
    the queries"* over `limitations: []`. A reader was told their data was empty
    when nothing had been asked of it.

    The thin-evidence rule could not see it, and correctly so: it asks about a
    query that *succeeded* and returned no rows. A failed one matched no rule at
    all, which is how a run that knew ended up saying nothing.
    """
    notes = limitations_for(_state(_failed(), _failed()), None)

    assert len(notes) == 1
    assert notes[0].startswith("Every query this run tried failed to run")
    assert UNREACHABLE in notes[0], "the connector's own words, so the reader can act on them"


def test_the_note_names_how_many_failed_when_some_succeeded() -> None:
    """The partial case is the one a reader most needs the count for: part of the
    answer stands on data and part of it was never asked."""
    notes = limitations_for(_state(_ref("e1"), _ref("e2"), _failed()), None)

    assert len(notes) == 1
    assert notes[0].startswith("1 of the 3 queries this run tried failed to run")
    assert "what it would have returned" in notes[0], "one query is not a they"


def test_one_query_that_failed_is_not_described_as_all_of_them() -> None:
    notes = limitations_for(_state(_failed()), None)

    assert notes[0].startswith("The one query this run tried failed to run")
    assert "what it would have returned" in notes[0]


def test_the_note_agrees_with_the_number_that_failed_not_the_number_tried() -> None:
    """The two differ, and the live run is what noticed: the opening clause of
    *"1 of the 3 queries…"* names three and the sentence is about one."""
    notes = limitations_for(_state(_failed(), _failed()), None)

    assert "what they would have returned" in notes[0]


def test_a_refusal_the_loop_was_meant_to_repair_is_not_a_limitation() -> None:
    """**The reason `error` exists apart from `ok`.** A statement the policy
    refused is recorded as a failed execution too — that is how the next planner
    learns what was wrong — and it is routinely followed by the corrected query
    that answers the question. Caveating that would put a warning about a
    self-correction on a large share of healthy runs, which is how a reader
    learns to skip warnings.
    """
    state = _state(_failed(error=""), _ref("e2"))

    assert limitations_for(state, None) == ()


def test_a_query_that_never_ran_is_said_before_a_reviewer_s_warning() -> None:
    """Order, and the two are different kinds of doubt. A warning is about what
    the answer *claims*; a query that never reached the database is about how
    much of the investigation happened at all — and it is the more actionable,
    because an unreachable connector is something a person can go and fix."""
    verdict = CriticVerdict(verdict="pass", findings=(CriticFinding("n", WARN, "a caveat"),))

    notes = limitations_for(_state(_ref("e1"), _failed()), verdict)

    assert len(notes) == 2
    assert notes[0].startswith("1 of the 2 queries")
    assert notes[1] == "a caveat"


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


def test_a_run_that_tried_and_failed_is_not_a_run_that_chose_not_to_ask() -> None:
    """**B-095, one field further up the card.** "Answered without running a
    query" over two failed executions describes a failure as a decision — the
    same substitution the limitations exist to stop."""
    note = method_note(_state(_failed(), _failed()))

    assert note == "2 queries tried against orders; nothing came back."


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
    assert composed.state == "answered"
    assert composed.citations == ("e1",)
    assert composed.method.startswith("1 query")
    assert composed.limitations == ("a caveat",)


def test_a_confidence_outside_the_three_words_becomes_medium() -> None:
    """The column has a CHECK constraint; a model that invents "very high" must
    not take the run down with it."""
    composed = assemble(_draft(confidence="very high"), _state(_ref()), None, citations=())

    assert composed.confidence == "medium"


# ---------------------------------------------------------------------------
# An answer that rests on a measured join says so (D-050)
# ---------------------------------------------------------------------------


def _with_inferred(*, read: list[str], offered: list[str]) -> ResearchState:
    """A run whose graph had an inferred edge among `offered`, and read `read`.

    **The capability dict is built by the product's own encoder**, not by a
    literal written here. A test that hand-rolls `{"left": ..., "right": ...}`
    proves the composer can parse a shape this file invented — which is how
    B-109 stayed green over a field the schema never carried.
    """
    graph = JoinGraph(
        edges={"fact_sale": frozenset({"dim_outlet"}), "dim_outlet": frozenset({"fact_sale"})},
        inferred=frozenset({frozenset({"fact_sale", "dim_outlet"})}),
    )
    state = _state(
        ExecutionRef(execution_id="e1", row_count=3, ok=True, summary="a row", tables=read)
    )
    state.capability = {"inferred": encode_inferred_joins(graph.inferred_joins(offered))}
    return state


def test_an_answer_built_on_a_measured_join_says_the_join_was_measured() -> None:
    """`miseq` declares no foreign keys, so every join it offers was inferred.

    Both are good enough to join on. Only a declared key is good enough to pass
    off as the database's own word, and silently treating an inference as a
    declaration is how B-057's cartesian product gets back in.
    """
    notes = limitations_for(
        _with_inferred(
            read=["public.fact_sale", "public.dim_outlet"],
            offered=["fact_sale", "dim_outlet"],
        ),
        CriticVerdict(verdict="pass"),
    )

    assert any("measured rather than declared" in note for note in notes)
    assert any("dim_outlet and fact_sale" in note for note in notes)


def test_a_caveat_about_a_join_nobody_used_is_not_written() -> None:
    """Narrowed to the tables the run actually read.

    A caveat on every answer is a caveat nobody reads, which is the same
    argument the single-source test above makes — and it would train people to
    skip the one that matters.
    """
    notes = limitations_for(
        _with_inferred(read=["public.dim_calendar"], offered=["fact_sale", "dim_outlet"]),
        CriticVerdict(verdict="pass"),
    )

    assert not any("measured rather than declared" in note for note in notes)


def test_the_two_ends_of_the_capability_record_agree() -> None:
    """**The seam itself**, because both sides of it live in different modules.

    `runner` encodes, `composer` decodes, and for most of this repository's
    history a pair like that has been two literals that happened to match. If a
    rename breaks one, this goes red rather than every caveat quietly vanishing.
    """
    pairs = (("dim_outlet", "fact_sale"), ("dim_item", "fact_sale_line"))

    assert decode_inferred_joins(encode_inferred_joins(pairs)) == pairs


def test_a_run_recorded_before_inference_existed_has_no_caveat() -> None:
    """`capability` is a plain dict round-tripped through the database, so an
    older run simply has no such key. That is an answer without a caveat, not an
    error."""
    assert decode_inferred_joins(None) == ()
    assert decode_inferred_joins([{"left": "a"}, "nonsense", {"left": "a", "right": "b"}]) == (
        ("a", "b"),
    )


# ---------------------------------------------------------------------------
# The period an answer is about (B-157, D-059)
# ---------------------------------------------------------------------------


def test_an_answer_outside_the_catalogs_period_says_so_beside_the_answer() -> None:
    """B-157's first screenshot: 24 figures for 2023-2024 while every dated column
    in the bundle ran through 2025. The reader gets both measurements, not a
    verdict, because the useful thing is the pair — and because a reader who
    disagrees can go and look at either."""
    state = _state(_ref())
    state.capability["coverage"] = {
        "status": "outside",
        "reason": "",
        "answered": "2023-01 to 2024-12",
        "available": "2025-01 to 2025-12",
    }

    notes = limitations_for(state, CriticVerdict(verdict="pass"))

    assert any("2023-01 to 2024-12" in note and "2025-01 to 2025-12" in note for note in notes)


def test_an_answer_inside_the_catalogs_period_adds_no_caveat() -> None:
    """*"Sales last month"* returns one month out of a year and is correct. A
    caveat here would teach people to skip caveats, which is the failure D-034's
    budget note and B-146's coverage floor were both written against."""
    state = _state(_ref())
    state.capability["coverage"] = {
        "status": "contained",
        "reason": "",
        "answered": "2025-12",
        "available": "2025-01 to 2025-12",
    }

    assert limitations_for(state, CriticVerdict(verdict="pass")) == ()


def test_an_abstention_is_loud_in_the_trace_and_silent_on_the_answer_card() -> None:
    """The two live in different places on purpose.

    A reader of an answer is owed caveats **about their answer**; *"a check could
    not run"* is not one, and putting it here would be the padding that makes
    people stop reading the ones that matter. The trace is where that belongs,
    and `answer_composed` carries it — asserted in `test_runner.py`, on the
    payload, because that is the object a person can actually look at.
    """
    state = _state(_ref())
    state.capability["coverage"] = {
        "status": "abstained",
        "reason": "the result was cut off at the row limit",
        "answered": None,
        "available": "2025-01 to 2025-12",
    }

    assert limitations_for(state, CriticVerdict(verdict="pass")) == ()
