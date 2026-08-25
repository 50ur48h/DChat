"""Everything the scripted provider emits is a shape the product will accept.

**Written after the third time.** D-044 deleted `FinalizeIn.answered`, and three
separate producers of that JSON still sent it:

* the unit-test fakes — caught by the suite, in the same commit;
* `ops/evals/runner.py`'s fake composer — caught by the `evals` job failing
  **20/20**, because `extra="forbid"` makes a stale key a validation error rather
  than an ignored field;
* `llm/scripted.py` — caught by the **browser e2e**, two hours later, with
  `scripted-1 did not produce valid FinalizeIn in two attempts`.

The first two are test code. The third is **shipped in the product image** and is
what `LLM_PROVIDERS=scripted` selects in CI (D-040), so it is the one that can be
tested here — and the one whose failure looked like a broken product rather than
a stale fixture.

**Why a schema test and not a bigger e2e.** The e2e did catch it, at the cost of
two minutes of Chromium and a compose stack, and it reported the symptom (*"the
answer never appeared"*) rather than the cause. This runs in milliseconds and
names the field. The e2e stays: it is the only thing that proves the whole chain,
and it is what found this. This just means it should not have to.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, cast

import pytest
from pydantic import BaseModel

from dataagent.agent.critic import CriticOut
from dataagent.agent.loop import Reflection
from dataagent.agent.planner import Plan
from dataagent.agent.tools.finalize import FinalizeIn
from dataagent.llm.base import LLMRequest, Message, Tags
from dataagent.llm.scripted import scripted_body

#: Each role the scripted provider answers structurally, and the model the
#: product parses that answer into. A role the provider starts answering with
#: free text is not covered here and does not need to be — the loop treats
#: unstructured roles as prose, which is exactly why only these four can break.
STRUCTURED_ROLES: list[tuple[str, type[BaseModel]]] = [
    ("sql", Plan),
    ("plan", Reflection),
    ("critic", CriticOut),
    ("compose", FinalizeIn),
]


def _reply(role: str) -> str:
    """What the provider says for a role, given a prompt that names an execution.

    The uuid matters for `compose`: the scripted provider cites whatever execution
    ids it finds in the prompt, so a prompt without one produces an answer with no
    citations — which validates fine and would make this test weaker than it
    looks.
    """
    request = LLMRequest(
        model="scripted-1",
        messages=[
            Message(
                role="user",
                content="execution 3f7c1a02-5d21-4a9b-8c33-9e1f0b6d4a77 returned 3 rows",
            )
        ],
        tags=Tags(org_id=uuid.uuid4(), role=cast("Any", role), run_id=uuid.uuid4()),
    )
    return scripted_body(request)


@pytest.mark.parametrize(("role", "model"), STRUCTURED_ROLES, ids=[r for r, _ in STRUCTURED_ROLES])
def test_the_scripted_reply_validates(role: str, model: type[BaseModel]) -> None:
    """`extra="forbid"` is the reason this matters.

    A field the product no longer has is not ignored — it raises, twice, and the
    run dies with `StructuredOutputError`. Which is a broken product in every
    environment that selects this provider, from a stub nobody thinks of as code.
    """
    model.model_validate(json.loads(_reply(role)))


def test_the_composed_reply_cites_what_the_prompt_named() -> None:
    """The control on `_reply`'s uuid.

    Without it, `test_the_scripted_reply_validates[compose]` would pass against a
    provider that had stopped citing anything — a valid `FinalizeIn` with no
    support is exactly the shape B-138 is about, and a schema check cannot see it.
    """
    draft = FinalizeIn.model_validate(json.loads(_reply("compose")))

    assert draft.supported_by == ["3f7c1a02-5d21-4a9b-8c33-9e1f0b6d4a77"]
