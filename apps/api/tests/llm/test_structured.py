"""Turning a model's text into an object (architecture 4.3).

Parsing is deliberately tolerant of two things models always do — fencing the
JSON, and wrapping it in prose — and deliberately intolerant of everything else.
The line matters: each extra tolerance is this module inventing content, and the
whole product rests on the claim that it does not.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from dataagent.llm.base import Message
from dataagent.llm.structured import (
    StructuredOutputError,
    instruction_for,
    parse,
    repair_messages,
)


class Plan(BaseModel):
    steps: list[str]
    confident: bool = False


def test_a_bare_object_parses() -> None:
    assert parse(Plan, '{"steps": ["count orders"]}').steps == ["count orders"]


def test_a_fenced_object_parses() -> None:
    """Models add fences even when told not to; refusing would be pedantry with
    a price tag — a second call, for a reply that was correct."""
    text = 'Here you go:\n```json\n{"steps": ["a"], "confident": true}\n```'

    parsed = parse(Plan, text)

    assert parsed.steps == ["a"]
    assert parsed.confident


def test_an_object_wrapped_in_prose_parses() -> None:
    assert parse(Plan, 'Sure. {"steps": ["a"]} Let me know if that helps.').steps == ["a"]


def test_text_with_no_object_is_refused_with_an_instruction() -> None:
    with pytest.raises(StructuredOutputError) as raised:
        parse(Plan, "I think we should count the orders.")

    assert "no JSON object" in str(raised.value)


def test_an_empty_reply_is_refused() -> None:
    with pytest.raises(StructuredOutputError):
        parse(Plan, "   ")


def test_broken_json_is_refused_and_says_where() -> None:
    with pytest.raises(StructuredOutputError) as raised:
        parse(Plan, '{"steps": ["a",}')

    assert "not valid JSON" in str(raised.value)


def test_a_bare_json_array_is_not_an_object_and_is_refused() -> None:
    """A list is valid JSON and still not an answer: every schema in this
    product is an object with named fields."""
    with pytest.raises(StructuredOutputError, match="no JSON object"):
        parse(Plan, '["a", "b"]')


def test_two_objects_are_refused_rather_than_one_being_chosen() -> None:
    """Picking one would be a guess, and a guess here silently changes an answer."""
    with pytest.raises(StructuredOutputError):
        parse(Plan, '{"steps": ["a"]} and also {"steps": ["b"]}')


def test_the_right_json_of_the_wrong_shape_names_the_field() -> None:
    """The message is fed back to the model, so it has to be actionable."""
    with pytest.raises(StructuredOutputError) as raised:
        parse(Plan, '{"steps": "count orders"}')

    assert "steps" in str(raised.value)


def test_the_instruction_carries_the_schema_and_is_a_system_message() -> None:
    instruction = instruction_for(Plan)

    assert instruction.role == "system"
    assert "steps" in instruction.content
    assert json.dumps(Plan.model_json_schema(), sort_keys=True) in instruction.content


def test_the_repair_conversation_keeps_the_question_and_adds_the_complaint() -> None:
    """A repair must not quietly change what was asked — otherwise the second
    answer is to a different question and nobody can tell."""
    original = [Message(role="user", content="plan this")]

    repaired = repair_messages(
        original, reply="not json", problem="the reply contained no JSON object", schema=Plan
    )

    assert repaired[: len(original)] == original
    assert repaired[-2] == Message(role="assistant", content="not json")
    assert repaired[-1].role == "user"
    assert "no JSON object" in repaired[-1].content
    assert "steps" in repaired[-1].content
