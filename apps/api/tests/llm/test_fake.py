"""The fake every later phase depends on.

Two properties are load-bearing and both are tested directly rather than
implied: the same requests always produce the same completions, and a request
nobody scripted fails with a message that says what was asked. Everything from
Phase 7's agent tests to Phase 9's evals inherits whatever is true here, so a
subtle wobble in this file would surface much later as "the suite is flaky".
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import BaseModel

from dataagent.llm.base import CallLimits, LLMError, LLMRequest, Message, Role, Tags, Usage
from dataagent.llm.fake import FakeLLM, NoScriptedResponseError

ORG = uuid.uuid4()


def request_for(role: Role = "plan", text: str = "how many orders?") -> LLMRequest:
    return LLMRequest(
        model="fake-strong",
        messages=[
            Message(role="system", content="platform rules"),
            Message(role="user", content=text),
        ],
        tags=Tags(org_id=ORG, role=role),
    )


async def test_the_same_request_twice_gives_the_same_answer() -> None:
    """Determinism, stated as a test rather than as a docstring."""
    fake = FakeLLM().script("42", role="plan")

    first = await fake.complete(request_for())
    second = await fake.complete(request_for())

    assert first == second


async def test_scripts_match_on_role_and_on_prompt_text_together() -> None:
    fake = (
        FakeLLM()
        .script("a plan", role="plan")
        .script("a critique", role="critic")
        .script("about revenue", role="plan", contains="revenue")
    )

    assert (await fake.complete(request_for("plan"))).text == "a plan"
    assert (await fake.complete(request_for("critic"))).text == "a critique"
    # First match wins, so the broad plan script still answers — which is why a
    # narrower script goes first. Stated here because it is the thing a test
    # author gets wrong.
    assert (await fake.complete(request_for("plan", "revenue by store"))).text == "a plan"


async def test_a_narrower_script_placed_first_wins() -> None:
    fake = FakeLLM().script("about revenue", role="plan", contains="revenue").script("a plan")

    assert (await fake.complete(request_for("plan", "revenue by store"))).text == "about revenue"
    assert (await fake.complete(request_for("plan", "orders by store"))).text == "a plan"


async def test_times_scripts_a_sequence() -> None:
    """The shape every repair and retry test needs: fail once, then behave."""
    fake = FakeLLM().script("not json", times=1).script('{"ok": true}')

    assert (await fake.complete(request_for())).text == "not json"
    assert (await fake.complete(request_for())).text == '{"ok": true}'
    assert (await fake.complete(request_for())).text == '{"ok": true}'


async def test_a_script_can_raise_so_failure_paths_are_testable() -> None:
    fake = FakeLLM().script(raises=LLMError("rate limited", retryable=True), role="plan")

    with pytest.raises(LLMError) as raised:
        await fake.complete(request_for())

    assert raised.value.retryable
    # The failure is recorded too: a call that raised still happened, and a test
    # asserting "the agent tried twice" has to be able to see both.
    assert len(fake.calls) == 1
    assert fake.calls[0].completion is None


async def test_an_unscripted_request_says_what_was_asked_and_what_was_on_offer() -> None:
    """The commonest way a downstream test breaks is a prompt that changed shape.

    That must not arrive as an empty string that fails three assertions later.
    """
    fake = FakeLLM().script("only for critics", role="critic", contains="draft")

    with pytest.raises(NoScriptedResponseError) as raised:
        await fake.complete(request_for("plan", "how many orders?"))

    message = str(raised.value)
    assert "role='plan'" in message
    assert "fake-strong" in message
    assert "critic" in message and "draft" in message
    assert "how many orders?" in message


async def test_a_default_answers_anything_when_a_test_asks_for_one() -> None:
    fake = FakeLLM(default="whatever")

    assert (await fake.complete(request_for("intake"))).text == "whatever"


async def test_recording_keeps_the_whole_request() -> None:
    """Assertions are about what the agent *asked*, so the request is kept whole."""
    fake = FakeLLM(default="ok")

    await fake.complete(request_for("sql", "select from orders"))

    call = fake.last_call()
    assert call.role == "sql"
    assert call.model == "fake-strong"
    assert call.system_prompt == "platform rules"
    assert call.user_prompt == "select from orders"
    assert call.request.tags.org_id == ORG
    assert call.completion is not None


async def test_calls_are_filtered_and_counted_by_role() -> None:
    fake = FakeLLM(default="ok")

    await fake.complete(request_for("plan"))
    await fake.complete(request_for("critic"))
    await fake.complete(request_for("plan"))

    assert fake.count() == 3
    assert fake.count("plan") == 2
    assert [call.role for call in fake.calls_for("critic")] == ["critic"]
    assert fake.last_call("plan").role == "plan"


async def test_asking_for_a_call_that_never_happened_names_what_did() -> None:
    fake = FakeLLM(default="ok")
    await fake.complete(request_for("plan"))

    with pytest.raises(AssertionError) as raised:
        fake.last_call("compose")

    assert "compose" in str(raised.value)
    assert "plan" in str(raised.value)


async def test_reset_forgets_calls_and_use_counts_but_keeps_scripts() -> None:
    fake = FakeLLM().script("once", times=1)
    await fake.complete(request_for())

    fake.reset()

    assert fake.calls == ()
    assert (await fake.complete(request_for())).text == "once"


async def test_usage_is_estimated_from_the_text_and_says_so() -> None:
    """Deterministic token counts, flagged as estimates all the way to the ledger."""
    fake = FakeLLM(default="four")

    completion = await fake.complete(request_for())

    assert completion.usage.estimated
    assert completion.usage.input_tokens > 0
    assert completion.usage.total_tokens == (
        completion.usage.input_tokens + completion.usage.output_tokens
    )


async def test_a_script_can_pin_usage_for_cost_assertions() -> None:
    fake = FakeLLM().script("x", usage=Usage(input_tokens=1000, output_tokens=500))

    completion = await fake.complete(request_for())

    assert completion.usage == Usage(input_tokens=1000, output_tokens=500)
    assert not completion.usage.estimated


async def test_script_json_takes_the_model_the_code_expects() -> None:
    """A fake that can script a shape the code could never accept is a fake that
    can lie convincingly. Passing the pydantic model closes that door."""

    class Plan(BaseModel):
        steps: list[str]

    fake = FakeLLM().script_json(Plan(steps=["count orders"]))

    completion = await fake.complete(request_for())

    assert Plan.model_validate_json(completion.text).steps == ["count orders"]


def test_from_mapping_builds_from_plain_data() -> None:
    """Phase 9 keeps eval scripts in files; this package does not acquire a parser."""
    fake = FakeLLM.from_mapping(
        [{"role": "plan", "contains": "orders", "respond": "counted", "times": 1}]
    )

    assert len(fake.scripts) == 1
    assert fake.scripts[0].role == "plan"
    assert fake.scripts[0].times == 1


def test_from_mapping_refuses_a_role_that_does_not_exist() -> None:
    with pytest.raises(ValueError, match="not an LLM role"):
        FakeLLM.from_mapping([{"role": "planner", "respond": "x"}])


async def test_the_fake_declares_no_native_structured_output() -> None:
    """On purpose: it exercises the harder path — schema in the prompt, reply
    parsed, one repair — which is the path that actually goes wrong. A fake
    claiming native schema support would leave it untested everywhere."""
    caps = FakeLLM().capabilities()

    assert not caps.supports_response_schema
    assert caps.is_stub


async def test_limits_reach_the_provider_unchanged() -> None:
    fake = FakeLLM(default="ok")
    limits = CallLimits(max_output_tokens=32, temperature=0.7, timeout_seconds=5.0)

    await fake.complete(
        LLMRequest(
            model="fake-small",
            messages=[Message(role="user", content="hi")],
            tags=Tags(org_id=ORG, role="intake"),
            limits=limits,
        )
    )

    assert fake.last_call().request.limits == limits
