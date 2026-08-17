"""The gate: what a caller is offered, and what happens to bad arguments.

No database — the registry's own behaviour is about dispatch, not about data, and
a fake tool makes the failure paths reachable without arranging for a real one to
break. The tools themselves are exercised against a real catalog in
``test_tools_live.py``.

The property worth stating: **a tool the caller may not use is indistinguishable
from one that does not exist.** "Exists, but not for you" is a fact about the
system worth not disclosing to a prompt that may be hostile, and it also means
there is one error path rather than two.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import BaseModel, ConfigDict, Field

from dataagent.agent.tools.base import RESULT_FRAME, Tool, ToolContext, ToolError
from dataagent.agent.tools.registry import ToolRegistry, default_registry


class EchoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=50)
    times: int = Field(default=1, ge=1, le=3)


class EchoOut(BaseModel):
    said: str


async def _echo(context: ToolContext, params: BaseModel) -> BaseModel:
    args = EchoIn.model_validate(params)
    return EchoOut(said=args.text * args.times)


async def _explode(context: ToolContext, params: BaseModel) -> BaseModel:
    raise ToolError("the far end said no", code="engine_error")


ECHO = Tool(name="echo", description="Repeat something.", params=EchoIn, handler=_echo)
EXPLODE = Tool(name="explode", description="Fail.", params=EchoIn, handler=_explode)
ADMIN_ONLY_TOOL = Tool(
    name="secret", description="Admin work.", params=EchoIn, handler=_echo, required_role="admin"
)


def _context(role: str = "reader") -> ToolContext:
    return ToolContext(org_id=uuid.uuid4(), run_id=uuid.uuid4(), role=role)


# ---------------------------------------------------------------------------
# What is on offer
# ---------------------------------------------------------------------------


def test_a_reader_is_not_offered_an_admin_tool() -> None:
    registry = ToolRegistry((ECHO, ADMIN_ONLY_TOOL))

    assert [tool.name for tool in registry.available_to("reader")] == ["echo"]
    assert [tool.name for tool in registry.available_to("admin")] == ["echo", "secret"]
    assert "secret" not in registry.describe_for("reader")


async def test_a_tool_you_may_not_use_answers_exactly_like_one_that_does_not_exist() -> None:
    """Two different facts, one answer. Telling them apart would confirm that a
    capability exists, to a prompt that may be trying to find out."""
    registry = ToolRegistry((ECHO, ADMIN_ONLY_TOOL))

    forbidden = await registry.call(_context(), "secret", {"text": "x"})
    absent = await registry.call(_context(), "no_such_tool", {"text": "x"})

    assert forbidden.ok is False and absent.ok is False
    assert forbidden.code == absent.code == "unknown_tool"
    # And neither message names the tool the caller could not have.
    assert "secret" not in str(absent.error)
    assert "echo" in str(forbidden.error), "the message should say what *is* available"


def test_the_tool_list_is_in_registration_order() -> None:
    """A weak nudge about what to reach for first, and one somebody chose.

    `search_knowledge` sits before `run_sql` deliberately (WP10.1b): 5.5's
    division of labour is that a document says what a term *means* and the
    database says what its *value* is, so a run reaching for SQL before checking
    whether the business has written a definition down has skipped a step.
    """
    registry = default_registry()

    assert [tool.name for tool in registry.available_to("reader")] == [
        "search_tables",
        "describe_table",
        "search_knowledge",
        "run_sql",
    ]


def test_registering_the_same_name_twice_is_refused() -> None:
    registry = ToolRegistry((ECHO,))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(ECHO)


def test_a_tool_declaring_an_unknown_role_is_refused_at_registration() -> None:
    """Not at call time: a tool nobody can reach would otherwise sit in the
    registry looking registered."""
    bad = Tool(name="odd", description="", params=EchoIn, handler=_echo, required_role="wizard")

    with pytest.raises(ValueError, match="wizard"):
        ToolRegistry((bad,))


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


async def test_valid_arguments_reach_the_handler_as_a_typed_object() -> None:
    registry = ToolRegistry((ECHO,))

    result = await registry.call(_context(), "echo", {"text": "ha", "times": 3})

    assert result.ok
    assert isinstance(result.data, EchoOut)
    assert result.data.said == "hahaha"


async def test_bad_arguments_are_refused_before_dispatch_and_say_what_was_wrong() -> None:
    """The model's output is the least trustworthy input in the system, so this
    is checked once here rather than in every handler."""
    registry = ToolRegistry((ECHO,))

    result = await registry.call(_context(), "echo", {"text": "", "times": 99})

    assert result.ok is False
    assert result.code == "invalid_arguments"
    assert result.repairable is True
    assert "text" in str(result.error) and "times" in str(result.error)


async def test_an_unexpected_argument_is_refused_rather_than_ignored() -> None:
    """``extra="forbid"`` on every tool schema, which is also what lets a
    provider enforce the schema natively rather than merely suggest it (B-033)."""
    registry = ToolRegistry((ECHO,))

    result = await registry.call(_context(), "echo", {"text": "hi", "sneaky": "value"})

    assert result.ok is False
    assert result.code == "invalid_arguments"


async def test_missing_arguments_are_a_repairable_failure() -> None:
    registry = ToolRegistry((ECHO,))

    result = await registry.call(_context(), "echo", None)

    assert result.ok is False
    assert result.repairable is True


# ---------------------------------------------------------------------------
# The envelope
# ---------------------------------------------------------------------------


async def test_a_failing_tool_comes_back_as_an_envelope_not_an_exception() -> None:
    """One shape at every call site. A tool that raised past the registry would
    be a failure the runner could forget to record."""
    registry = ToolRegistry((EXPLODE,))

    result = await registry.call(_context(), "explode", {"text": "x"})

    assert result.ok is False
    assert result.code == "engine_error"
    assert result.repairable is False


async def test_every_rendered_result_is_framed_as_data() -> None:
    """Architecture 7.4: a query result may be hostile, so it is never presented
    to the model as though it were an instruction."""
    registry = ToolRegistry((ECHO, EXPLODE))

    ok = await registry.call(_context(), "echo", {"text": "hi"})
    failed = await registry.call(_context(), "explode", {"text": "hi"})

    assert ok.render().startswith(RESULT_FRAME)
    assert failed.render().startswith(RESULT_FRAME)
    assert "hi" in ok.render()
    assert "the far end said no" in failed.render()


async def test_a_long_argument_is_capped_before_it_reaches_an_event_payload() -> None:
    registry = ToolRegistry((ECHO,))

    result = await registry.call(_context(), "echo", {"text": "x" * 5000})

    # Refused for length, and the recorded argument is still bounded: a trace row
    # is not the place for five kilobytes of model output.
    assert result.ok is False
    assert len(str(result.safe_args["text"])) <= 501


def test_a_tools_schema_is_json_and_describes_its_arguments() -> None:
    """What a provider is handed. If this stops being JSON-serialisable, every
    structured call breaks at the provider rather than here."""
    described = ECHO.describe()

    assert "echo" in described
    assert '"text"' in described and '"times"' in described
