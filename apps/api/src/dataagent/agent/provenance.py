"""Which rows are observed and which are modelled (D-053, D-058, B-157).

The partner's contract labels every row: `real` is observed source data, `derived`
is an estimate computed from it, `synthetic` is scenario data. B-157 is what
happens when a platform cannot read that — 24 figures from a back-cast at 78% of
2025 actuals, marked **answered**, **high confidence**, no caveat, while
`fact_sale`'s 112,327 observed rows went unread.

**This is a convention, not a discovery, and the difference matters.**
`inference.py` refuses to read a column name by construction; this module reads
one on purpose, because a customer *told us* what it means. `PROVENANCE_COLUMN`
is the single place that assumption lives. On a database without such a column
this module returns nothing and every caller carries on unchanged — which is most
databases, and is why D-058 orders the coverage check ahead of this one.

**Measured, not asked.** The values come from `CatalogColumn.top_values`, which
the profiler writes for any column with few enough distinct values. Nothing here
consults a model, and nothing reads prose.

**Ranking is a nudge among comparable tables, never a sort of the bundle**, and
the reason is a measurement rather than a preference. On MiseQ, `source_mode`
splits the *dimensions* as sharply as the facts: `dim_calendar`, `dim_outlet`,
`dim_item`, `dim_ingredient`, `dim_supplier`, `dim_business` and `dim_vertical`
are `real`, while `dim_member`, `dim_weather`, `dim_waste_category` and
`dim_industry_benchmark` are `synthetic`. A straight sort by provenance would
therefore put **seven real dimensions ahead of `fact_waste` on a question about
waste** — and with `CARDS_KEPT_IN_FULL` at five, the one table that answers the
question would lose its detail to `dim_vertical`, which has three rows.

So the reordering is restricted to cards that **offer measures** — `offers_measures`
is already the platform's cheapest honest test for *could this table have answered
the question* (B-093). Dimensions never move. Among tables that could each answer,
observed data comes first.

**It is context, not a prohibition** (D-058). A modelled table is never removed:
2023-2024 exists nowhere else in this database, and a question about it must still
be answerable. What changes is which table is the *default* when two could serve,
and B-157's default was chosen because a table's name contained a word from the
question.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

__all__ = [
    "MODES",
    "PROVENANCE_COLUMN",
    "caveat",
    "modes_of",
    "rank_of",
    "reorder",
]

#: The column a customer uses to say how a row came to exist. Named once, here,
#: so that making it configurable later is one edit rather than a search.
PROVENANCE_COLUMN = "source_mode"

#: Worst to best. A table is judged by its **weakest** mode, because a table
#: holding one modelled row among observed ones is a table whose figures can be
#: partly modelled, and the caveat is about what an answer might rest on.
MODES: tuple[str, ...] = ("synthetic", "derived", "real")

#: How a mode reads to somebody who did not write the contract.
_PLAIN: Mapping[str, str] = {
    "synthetic": "modelled scenario data rather than observed records",
    "derived": "estimates computed from observed records rather than observed records",
}


def rank_of(modes: Sequence[str]) -> int:
    """Where a table sits, by its weakest mode. Higher is more observed.

    A table with no recognised mode ranks **with the observed ones** rather than
    below them. Absence of a label is not evidence of modelling, and demoting
    every unlabelled table would demote the whole of any database that does not
    use this convention.
    """
    known = [mode for mode in modes if mode in MODES]
    return min(MODES.index(mode) for mode in known) if known else len(MODES) - 1


def modes_of(
    columns: Sequence[tuple[str, Sequence[Mapping[str, object]] | None]],
) -> tuple[str, ...]:
    """The modes measured on one table, over ``(column_name, top_values)``.

    Empty when the table has no provenance column, and empty when the profiler
    has not run — those are the same outcome for a caller and a different one for
    a reader, which is why the caller reports *why* it said nothing.
    """
    for name, top_values in columns:
        if name.lower() != PROVENANCE_COLUMN or not top_values:
            continue
        found = [str(entry.get("value", "")) for entry in top_values]
        return tuple(mode for mode in MODES if mode in found)
    return ()


def reorder[T](
    cards: Sequence[T],
    *,
    modes: Callable[[T], Sequence[str]],
    answers_questions: Callable[[T], bool],
) -> tuple[T, ...]:
    """Observed data first, **among the cards that could each answer**.

    Cards that offer no measures keep their positions exactly; the ones that do
    are re-laid into those same positions in provenance order, ties keeping
    search order. So a dimension never overtakes a fact, the bundle's membership
    is unchanged, and the only thing that moves is which of two tables that could
    both answer is seen first.
    """
    positions = [index for index, card in enumerate(cards) if answers_questions(card)]
    if len(positions) < 2:
        return tuple(cards)
    ordered = sorted(
        (cards[index] for index in positions),
        key=lambda card: -rank_of(modes(card)),
    )
    out = list(cards)
    for index, card in zip(positions, ordered, strict=True):
        out[index] = card
    return tuple(out)


def caveat(read: Mapping[str, Sequence[str]]) -> str:
    """What to say when an answer rests on rows that were not observed.

    ``read`` is the tables an answer actually read and the modes measured on
    each — narrowed by the caller, because a caveat about a table nobody used is
    noise, and noise is how a reader learns to skip caveats.

    Names the tables and what their labels mean in words, because *"source_mode
    is synthetic"* is the customer's vocabulary and not the reader's. Says
    nothing at all when everything read was observed, which is the common case
    and a good one.
    """
    modelled = {
        table: rank_of(modes)
        for table, modes in read.items()
        if modes and rank_of(modes) < len(MODES) - 1
    }
    if not modelled:
        return ""
    worst = MODES[min(modelled.values())]
    names = ", ".join(sorted(modelled))
    plural = "table" if len(modelled) == 1 else "tables"
    return (
        f"This answer reads {plural} the database labels as {worst}: {names}. "
        f"That means {_PLAIN.get(worst, 'not observed records')}, so the figures "
        f"describe what was modelled rather than what was measured."
    )
