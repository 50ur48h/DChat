"""How much of a result reaches the model, and what it is told about the rest.

**B-113.** The preview was a fixed twenty rows, and a fixed row count is the
wrong unit for the rule it was enforcing — architecture 4.4's *summaries flow
forward*, which is about how much of the prompt raw values are allowed to
occupy. Twenty narrow rows and twenty wide ones are not the same cost, so the
constant starved one shape and flooded the other. It starved the shape the
product is for: eighteen months of revenue by channel is 54 rows, the model saw
twenty, and it refused a question the database had answered in full.

The property under all of these is one sentence: **what reaches the model is
governed by what it costs, and the model is told when it is holding part of
something.**
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence

from dataagent.agent.tools.base import MAX_RENDERED_CHARS, RESULT_FRAME
from dataagent.agent.tools.sql import (
    PREVIEW_CHARS,
    PREVIEW_ROWS_MIN,
    RunSqlOut,
    preview_rows,
)

#: The owner's own result: 18 months by 3 channels, three narrow columns.
NARROW = [
    [f"2025-{month % 12 + 1:02d}-01", channel, 119558.51]
    for month in range(18)
    for channel in ("delivery", "dine_in", "pickup")
]

#: Twelve columns of prose. The same row count costs an order of magnitude more.
WIDE = [[f"value {index} " * 8 for index in range(12)] for _ in range(200)]


def _render(columns: list[str], total: int) -> Callable[[list[list[object]]], str]:
    def render(rows: list[list[object]]) -> str:
        return RunSqlOut(
            execution_id="e" * 36,
            columns=columns,
            rows=rows,
            row_count=total,
            truncated=len(rows) < total,
            tables=["public.orders"],
        ).model_dump_json(indent=2)

    return render


def _shown(rows: Sequence[Sequence[object]], columns: list[str]) -> list[list[object]]:
    return preview_rows(rows, _render(columns, len(rows)))


# ---------------------------------------------------------------------------
# What the budget buys
# ---------------------------------------------------------------------------


def test_the_result_that_was_refused_now_reaches_the_model_whole() -> None:
    """**B-113's own case.** Fifty-four narrow rows cost less than twenty wide
    ones, and the fixed count could not know that. The question the database
    answered in full is now a question the model has been given in full."""
    assert len(_shown(NARROW, ["month", "channel", "revenue"])) == len(NARROW) == 54


def test_a_wide_result_gives_fewer_rows_than_the_old_constant_did() -> None:
    """The budget cuts both ways, and this is the half that was flooding the
    prompt: twenty rows of twelve prose columns is far more of the context than
    twenty rows of three numbers, and nothing used to notice."""
    shown = _shown(WIDE, [f"c{index}" for index in range(12)])

    assert PREVIEW_ROWS_MIN <= len(shown) < 20


def test_a_result_too_large_to_send_whole_is_still_generous() -> None:
    """Five hundred narrow rows do not fit, and the answer is not to fall back to
    a handful: the budget spends what it has."""
    rows = [[f"2025-01-{index % 28 + 1:02d}", "x", float(index)] for index in range(500)]

    shown = _shown(rows, ["d", "c", "v"])

    assert 20 < len(shown) < 500


def test_an_empty_result_stays_empty() -> None:
    assert _shown([], ["a"]) == []


# ---------------------------------------------------------------------------
# The invariant the budget exists for
# ---------------------------------------------------------------------------


def test_a_governed_preview_is_never_cut_by_the_renderer() -> None:
    """**The property, not the constant.** `ToolResult.render` truncates at
    `MAX_RENDERED_CHARS` and says so only to the model, with no flag anything
    downstream can read (**B-112**). A preview the budget governs stays inside
    that ceiling, so this tool does not reach it.

    Asserted against the *rendered* payload plus the frame it is wrapped in,
    because that is the string the cap is applied to. Measuring a proxy for it is
    what the first version of this budget did — it counted compact JSON while the
    payload ships indented, an under-count of nearly two to one.
    """
    frame_cost = len(RESULT_FRAME) + len("run_sql returned:") + 2

    for rows, columns in (
        (NARROW, ["month", "channel", "revenue"]),
        ([[f"2025-01-{i % 28 + 1:02d}", "x", float(i)] for i in range(500)], ["d", "c", "v"]),
        (WIDE, [f"c{index}" for index in range(12)]),
    ):
        render = _render(columns, len(rows))
        shown = preview_rows(rows, render)
        assert len(render(shown)) + frame_cost <= MAX_RENDERED_CHARS, columns


def test_the_floor_beats_the_budget_and_the_comment_says_so() -> None:
    """**The one case the invariant above does not cover**, asserted so it stays
    a known trade rather than becoming a surprise. A row wider than the whole
    budget is sent anyway, because a model shown no rows and a row count cannot
    tell an empty result from an expensive one — and that is the single path by
    which this tool still reaches B-112's unannounced cut."""
    rows: list[list[object]] = [["z" * 6_000] for _ in range(4)]

    shown = preview_rows(rows, _render(["big"], len(rows)))

    assert len(shown) == PREVIEW_ROWS_MIN
    assert len(_render(["big"], len(rows))(shown)) > MAX_RENDERED_CHARS


def test_rows_are_kept_whole() -> None:
    """A result sliced through the middle of a row is one the model has to parse
    around. The budget drops rows; it never cuts one."""
    shown = _shown(WIDE, [f"c{index}" for index in range(12)])

    for row in shown:
        assert len(row) == 12
        assert row in [[str(cell) for cell in original] for original in WIDE]


def test_the_budget_is_derived_from_the_ceiling_it_protects() -> None:
    """Chosen numbers drift from the thing they were chosen against. This one
    cannot: if `MAX_RENDERED_CHARS` moves, the budget moves with it."""
    assert PREVIEW_CHARS < MAX_RENDERED_CHARS


# ---------------------------------------------------------------------------
# What the model is told about the rest
# ---------------------------------------------------------------------------


def test_a_trimmed_preview_says_so_and_says_how_many_there_were() -> None:
    """**B-111 and B-113 need the same two numbers.** `row_count` is what the
    query returned and the rows are what fitted; without both, an answer can only
    guess whether it is holding all of something. With both, *20 of 54* is a fact
    it can state rather than infer."""
    rows = [[f"2025-01-{index % 28 + 1:02d}", "x", float(index)] for index in range(500)]
    shown = _shown(rows, ["d", "c", "v"])

    payload = json.loads(_render(["d", "c", "v"], len(rows))(shown))

    assert payload["truncated"] is True
    assert payload["row_count"] == 500
    assert len(payload["rows"]) < 500


def test_a_whole_result_is_not_marked_truncated() -> None:
    """The flag has to mean something. Marked on a complete result it would teach
    a model to hedge every answer, which is the mirror of the defect it exists to
    prevent."""
    payload = json.loads(
        _render(["month", "channel", "revenue"], len(NARROW))(
            _shown(NARROW, ["month", "channel", "revenue"])
        )
    )

    assert payload["truncated"] is False
