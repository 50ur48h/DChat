"""The definitions the eval organization is provisioned with (**B-070**).

Golden **#10** has two defensible readings and the English does not choose
between them: *"what proportion of customers ordered more than once?"* is
7861/**7985** if the denominator is customers who have ordered, and
7861/**8000** if it is everyone on file. The truth uses the first; a live model
wrote a `LEFT JOIN` from `customers` and computed the second, missing a
tolerance of 0.001 by 0.0018 — and did nothing wrong.

Widening the tolerance would accept genuinely wrong numbers; rewriting the
question to match the answer is the self-deception B-070 is about. Saying which
reading is authoritative, once, is the semantic layer's job.

What these tests hold is the part that would rot silently. The definition is
only useful if the question **reaches** it, and matching is whole-word against
the question's own words — so the day somebody rewords golden #10, or trims a
synonym that looked redundant, the grounding disappears and the eval goes back
to depending on a coin flip. Nothing else would notice: the FakeLLM run takes
its SQL from `golden.yaml` and passes either way.

No model and no database here. Whether the definition actually changes what a
model writes was settled by running it — recorded in B-070 and in STATUS.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from dataagent.semantic.definitions import Definition, RequiredFilter, matching

_EVALS = Path(__file__).resolve().parents[4] / "ops" / "evals"
if str(_EVALS) not in sys.path:
    sys.path.insert(0, str(_EVALS))

from provision import EVAL_DEFINITIONS  # noqa: E402

REPEAT_RATE = next(spec for spec in EVAL_DEFINITIONS if spec["name"] == "repeat_rate")


def _definitions() -> tuple[Definition, ...]:
    """The provisioned specs as the objects `matching` is given at run time."""
    import uuid

    return tuple(
        Definition(
            id=uuid.uuid4(),
            name=str(spec["name"]),
            kind="metric",
            description=str(spec["description"]),
            expression=str(spec["expression"]),
            required_filters=tuple[RequiredFilter](),
            synonyms=tuple(str(word) for word in spec["synonyms"]),  # type: ignore[union-attr]
        )
        for spec in EVAL_DEFINITIONS
    )


def _golden_question(case_id: int) -> str:
    cases = yaml.safe_load((_EVALS / "golden.yaml").read_text(encoding="utf-8"))
    return next(case["question"] for case in cases if case["id"] == case_id)


def test_golden_ten_reaches_the_definition_that_disambiguates_it() -> None:
    """**The test that stops this rotting.** Nobody types `repeat_rate`, so the
    definition is reachable only through a synonym that appears in the question
    as asked. Reword the question or trim the synonym and the grounding is gone
    — with every scripted run still green, because FakeLLM mode takes its SQL
    from `golden.yaml`."""
    applied = matching(_definitions(), _golden_question(10))

    assert [definition.name for definition in applied] == ["repeat_rate"]


def test_the_definition_says_which_denominator_is_authoritative() -> None:
    """The whole content of the fix. A definition that restated the question
    without settling the denominator would leave the coin flip in place."""
    description = str(REPEAT_RATE["description"])

    assert "denominator" in description
    assert "not every customer on file" in description


def test_it_binds_nothing_and_does_not_pretend_to() -> None:
    """The ambiguity is in which rows are counted, and no `{table, column, op,
    values}` predicate expresses "customers that appear in orders". So this one
    informs and does not bind (D-033) — claiming to check a denominator the
    critic cannot read would be worse than saying plainly that only filters
    bind."""
    assert "required_filters" not in REPEAT_RATE


def test_it_is_not_attached_to_a_question_about_a_different_proportion() -> None:
    """The synonyms are narrow on purpose. "proportion of customers" would have
    matched here too, and a definition applied to a question it is not about is
    how a metric's rules end up enforced on an answer that never needed them."""
    assert matching(_definitions(), "what proportion of customers are in the north region?") == ()


def test_a_question_about_orders_alone_is_left_alone() -> None:
    assert matching(_definitions(), "how many orders were placed last month?") == ()
