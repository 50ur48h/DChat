"""Which rows are observed and which are modelled (D-053, D-058, B-157).

The ranking rule's shape is the thing these tests hold. A straight sort by
provenance is the obvious implementation and it is wrong on the very dataset that
motivated the feature: `source_mode` splits MiseQ's *dimensions* as sharply as
its facts, so sorting the whole bundle would put seven real dimensions ahead of
`fact_waste` on a question about waste.
"""

from __future__ import annotations

from dataagent.agent.provenance import caveat, modes_of, rank_of, reorder


def _card(
    name: str, *, measures: bool, modes: tuple[str, ...]
) -> tuple[str, bool, tuple[str, ...]]:
    return (name, measures, modes)


def _reorder(*cards: tuple[str, bool, tuple[str, ...]]) -> list[str]:
    out = reorder(
        list(cards),
        modes=lambda card: card[2],
        answers_questions=lambda card: card[1],
    )
    return [card[0] for card in out]


# ---------------------------------------------------------------------------
# Reading the label
# ---------------------------------------------------------------------------


def test_the_modes_come_from_what_the_profiler_measured() -> None:
    assert modes_of([("source_mode", [{"value": "synthetic", "count": 120}])]) == ("synthetic",)


def test_a_table_with_no_provenance_column_says_nothing() -> None:
    assert modes_of([("net_amt", [{"value": "1.0", "count": 3}])]) == ()


def test_an_unprofiled_column_says_nothing_either() -> None:
    """Same silence, different cause — and the caller is what reports which."""
    assert modes_of([("source_mode", None)]) == ()


def test_a_table_is_judged_by_its_weakest_mode() -> None:
    """`fact_recipe_portion` really does hold 369 synthetic rows and 367 derived
    ones. An answer resting on it can rest on either, so the caveat is about the
    worse of the two."""
    assert rank_of(["derived", "synthetic"]) == rank_of(["synthetic"])


def test_an_unlabelled_table_ranks_with_the_observed_ones() -> None:
    """Absence of a label is not evidence of modelling. Demoting every unlabelled
    table would demote the whole of any database that does not use this
    convention — which is most of them."""
    assert rank_of([]) == rank_of(["real"])


# ---------------------------------------------------------------------------
# The ranking rule
# ---------------------------------------------------------------------------


def test_observed_data_comes_first_among_tables_that_could_each_answer() -> None:
    """**B-157 in miniature.** Retrieval put the back-cast first because its name
    contains the word the question used; both tables offer measures, so the
    observed one is what the model sees first."""
    assert _reorder(
        _card("fact_sale_monthly_history", measures=True, modes=("synthetic",)),
        _card("fact_sale", measures=True, modes=("real",)),
    ) == ["fact_sale", "fact_sale_monthly_history"]


def test_a_dimension_never_overtakes_the_table_that_answers_the_question() -> None:
    """**The measurement that killed the obvious implementation.** On MiseQ,
    `dim_calendar`, `dim_outlet` and `dim_vertical` are `real` while `fact_waste`
    is `synthetic`. A straight sort would put all three ahead of it — and with
    `CARDS_KEPT_IN_FULL` at five, the one table that answers a waste question
    would lose its detail to a three-row dimension."""
    order = _reorder(
        _card("fact_waste", measures=True, modes=("synthetic",)),
        _card("dim_calendar", measures=False, modes=("real",)),
        _card("dim_outlet", measures=False, modes=("real",)),
        _card("dim_vertical", measures=False, modes=("real",)),
    )

    assert order[0] == "fact_waste"
    assert order == ["fact_waste", "dim_calendar", "dim_outlet", "dim_vertical"]


def test_nothing_is_dropped_however_modelled_it_is() -> None:
    """Context, not a prohibition (D-058). 2023-2024 exists nowhere else in this
    database, so a question about it must still be answerable."""
    order = _reorder(
        _card("fact_sale_monthly_history", measures=True, modes=("synthetic",)),
        _card("fact_sale", measures=True, modes=("real",)),
    )

    assert set(order) == {"fact_sale", "fact_sale_monthly_history"}


def test_ties_keep_the_order_the_search_chose() -> None:
    """Provenance is a nudge between equals, not a replacement for relevance."""
    assert _reorder(
        _card("fact_sale", measures=True, modes=("real",)),
        _card("fact_purchase", measures=True, modes=("real",)),
        _card("fact_stock_move", measures=True, modes=("real",)),
    ) == ["fact_sale", "fact_purchase", "fact_stock_move"]


def test_one_candidate_is_left_exactly_where_it_was() -> None:
    assert _reorder(_card("fact_waste", measures=True, modes=("synthetic",))) == ["fact_waste"]


# ---------------------------------------------------------------------------
# What the reader is told
# ---------------------------------------------------------------------------


def test_an_answer_built_from_modelled_rows_says_so_in_words() -> None:
    sentence = caveat({"fact_sale_monthly_history": ["synthetic"]})

    assert "fact_sale_monthly_history" in sentence
    assert "modelled" in sentence
    assert "source_mode" not in sentence, "the customer's vocabulary is not the reader's"


def test_an_answer_built_from_observed_rows_says_nothing() -> None:
    assert caveat({"fact_sale": ["real"]}) == ""


def test_an_unlabelled_table_produces_no_caveat() -> None:
    assert caveat({"orders": []}) == ""


def test_the_worst_label_among_the_tables_read_is_the_one_stated() -> None:
    sentence = caveat({"gold_dish_cost_margin": ["derived"], "fact_waste": ["synthetic"]})

    assert "synthetic" in sentence
    assert "gold_dish_cost_margin" in sentence and "fact_waste" in sentence
