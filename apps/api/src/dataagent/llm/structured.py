"""Getting an object back from something that emits text.

Every decision the agent makes — the plan, the reflection, the critic's verdict —
arrives as a pydantic model or the run cannot continue (architecture 4.3). Two
routes get there, and this module makes them converge:

* the provider constrains its own decoding to a JSON schema, and we validate the
  result anyway;
* the provider cannot, so the schema is rendered into the prompt, and the reply
  is parsed.

**Validation happens on both routes.** A native structured call still returns
text over HTTP, "supports JSON schema" means different things on different APIs,
and the cost of checking is a microsecond. Trusting the provider here would mean
the type of ``Completion.parsed`` depends on which provider answered.

**One repair, never a loop.** When parsing fails, the model is shown its own
reply and the specific error, and asked once more. Once, because a model that
cannot follow a schema twice will not follow it on the fifth attempt, and because
each attempt is real money and real latency against a run budget (arch 4.4). The
second failure is an ``LLMError``, which the caller handles as a failure —
architecture 8.5's "malformed model output at any decision point defaults to
finish".

**What comes back is data, not instruction** (architecture 4.8, layer L5→L4). A
parsed object is a shape this code chose; nothing in it is executed, and every
field that reaches a database goes through the DAL's validator regardless.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ValidationError

from dataagent.llm.base import LLMError, Message

__all__ = [
    "StructuredOutputError",
    "instruction_for",
    "parse",
    "repair_messages",
]

#: A fenced block, with or without a language tag. Models add them even when told
#: not to, and refusing that reply would be pedantry with a price tag.
_FENCE = re.compile(r"```(?:[a-zA-Z0-9_-]+)?\s*(.*?)```", re.DOTALL)


class StructuredOutputError(LLMError):
    """The reply did not become the object the caller asked for.

    Never retryable in the fallback sense: another provider would be asked the
    same question and the fault is in the answer, not in the connection. The
    repair attempt is this module's business and has already happened by the time
    this escapes.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


def instruction_for(schema: type[BaseModel]) -> Message:
    """The system message that asks for JSON of a given shape.

    Sent only to providers without native schema support. It is deliberately
    blunt and short: a long explanation of JSON competes for attention with the
    actual task, and the failure it guards against — a preamble before the
    object — is handled by ``parse`` anyway.
    """
    body = json.dumps(schema.model_json_schema(), sort_keys=True)
    return Message(
        role="system",
        content=(
            "Reply with a single JSON object and nothing else — no prose before "
            "or after it. It must validate against this JSON Schema:\n" + body
        ),
    )


def parse[ModelT: BaseModel](schema: type[ModelT], text: str) -> ModelT:
    """The reply as an object, or ``StructuredOutputError`` saying what was wrong.

    Tolerant in exactly two ways, both of which are things every model does and
    neither of which is ambiguous: a fenced code block, and prose around a single
    top-level object. Anything further — repairing quotes, guessing at truncated
    JSON — would be this module inventing content, which is the one thing it must
    not do.

    The message is written to be *fed back*: it is what the repair attempt shows
    the model, so it names the failure precisely rather than politely.
    """
    candidate = _extract(text)
    if candidate is None:
        raise StructuredOutputError(
            "the reply contained no JSON object. Reply with a single JSON object and nothing else."
        )
    try:
        payload: object = json.loads(candidate)
    except json.JSONDecodeError as error:
        raise StructuredOutputError(
            f"the reply is not valid JSON: {error.msg} at position {error.pos}."
        ) from None
    # No "is it a dict" check: ``_extract`` only ever returns text starting with
    # ``{``, so anything that parses is one. A bare array reaches the message
    # above instead, which is the accurate complaint — there was no object.
    try:
        return schema.model_validate(payload)
    except ValidationError as error:
        raise StructuredOutputError(
            "the JSON does not match the required shape: " + _readable(error)
        ) from None


def repair_messages(
    previous: list[Message], *, reply: str, problem: str, schema: type[BaseModel]
) -> list[Message]:
    """The conversation for the one repair attempt.

    The model sees its own reply as an assistant turn and the problem as a user
    turn, which is the shape every chat API is trained on — and which keeps the
    original request intact, so a repair cannot quietly change the question.
    """
    return [
        *previous,
        Message(role="assistant", content=reply),
        Message(
            role="user",
            content=(
                f"That reply could not be used: {problem}\n"
                "Reply again with only a single JSON object matching this JSON "
                "Schema, and no other text:\n"
                + json.dumps(schema.model_json_schema(), sort_keys=True)
            ),
        ),
    ]


def _extract(text: str) -> str | None:
    """The JSON object inside a reply, whatever it is wrapped in."""
    stripped = text.strip()
    if not stripped:
        return None

    fenced = _FENCE.search(stripped)
    if fenced is not None:
        stripped = fenced.group(1).strip()

    if stripped.startswith("{"):
        return stripped

    # Prose around one object: take from the first brace to the last. Cheap, and
    # correct whenever there is exactly one object, which is the case this is
    # for. When there is more than one the parse fails, which is the honest
    # outcome — picking one of them would be a guess.
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end <= start:
        return None
    return stripped[start : end + 1]


def _readable(error: ValidationError) -> str:
    """A pydantic error as one line a model can act on."""
    parts = [
        f"{'.'.join(str(item) for item in problem['loc']) or '(root)'}: {problem['msg']}"
        for problem in error.errors()[:5]
    ]
    return "; ".join(parts)
