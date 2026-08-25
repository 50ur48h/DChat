"""A provider that answers from a fixed script, so CI can prove the stack (WP11.2b).

**What this is for, and what it can never show.** The phase gate wants a browser
driving the real product — real API, real database, real DAL, real catalog, real
seeded data — and the only piece of that chain which cannot run in CI for free
and deterministically is the model. So this stands in for one. It proves that
signing in, registering a source, asking a question, composing an answer, storing
it and rendering the card **all connect**. It cannot show that a question was
understood, and the gate wording says so explicitly: the chart criterion is met
by a live walk against a real model, never by this.

**It is a stub reaching a shipped image, and that is the risk.** `ProviderCaps`
already carries `is_stub` for exactly this hazard — *"a stub reaching production
would not fail, it would fabricate, confidently, in a product whose entire claim
is that its answers are evidenced"*. Two independent guards stand between this
module and production, and they fail in different places on purpose:

* **At boot**, `Settings.assert_llm_providers_are_production_safe` refuses to
  start when a production build or environment names a stub in `LLM_PROVIDERS`.
  Boot rather than first-call, for the reason the auth and secrets assertions
  give one screen above it: a service that starts and *then* fails open is
  indistinguishable from one that works until somebody looks.
* **At first use**, `registry.get_provider` refuses to hand out any provider
  whose capabilities report `is_stub`. That one catches what the first cannot —
  a stub registered at runtime through `register_provider`, which no
  configuration names.

**Deliberately not clever.** It answers by *role*, with the smallest thing that
satisfies each schema, and it reads the execution id back out of the prompt when
it composes — the one piece of state it cannot know in advance. It does not
model the agent's judgement, and nothing here should grow to. The moment this
file starts trying to look intelligent is the moment a green smoke starts
implying something it has not tested.
"""

from __future__ import annotations

import json
import re

from dataagent.llm.base import Completion, LLMRequest, ProviderCaps, Usage

__all__ = ["PROVIDER_NAME", "SCRIPTED_SQL", "ScriptedProvider"]

PROVIDER_NAME = "scripted"

#: What the smoke's question resolves to. Fixed, and deliberately the dullest
#: statement that still exercises the whole path: the DAL validates it, resolves
#: `orders` against the organization's own catalog, applies the row cap, records
#: an execution and stores a masked artifact. A more interesting query would
#: test the seed data rather than the plumbing.
SCRIPTED_SQL = "SELECT count(*) AS order_count FROM orders"

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _cited(prompt: str) -> list[str]:
    """The execution the composing prompt was shown, if it was shown one.

    Read out of the prompt rather than fabricated, because an invented id is
    dropped by `state.add_finding` and the answer would arrive uncited — which
    would make the smoke pass while proving the citation path broken. This is the
    same trick the runner's own tests use, and for the same reason: the id is
    minted at run time and nothing can know it in advance.
    """
    found = _UUID.search(prompt)
    return [found.group(0)] if found else []


def scripted_body(request: LLMRequest) -> str:
    """The scripted reply for this role, as the JSON the front door will parse.

    Keyed on `tags.role` rather than on the schema, because the role is what the
    caller meant and the schema is how it is enforced — and two roles can share
    a schema. Every branch emits only required fields plus what the run actually
    needs, so a schema gaining an optional field does not silently change what
    the smoke asserts.
    """
    role = request.tags.role
    if role == "sql":
        return json.dumps(
            {
                "sql": SCRIPTED_SQL,
                "purpose": "count the orders",
                "answerable": True,
            }
        )
    if role == "plan":
        # One iteration is enough: the smoke is about the path, not the search.
        return json.dumps({"done": True, "rationale": "that answers it"})
    if role == "critic":
        return json.dumps({"verdict": "pass"})
    if role == "compose":
        return json.dumps(
            {
                "answer": "The scripted model answered from a fixed script.",
                # No `answered`, and no `unanswered`: D-044 deleted the boolean and
                # `FinalizeIn` is `extra="forbid"`, so a stale key here is not an
                # ignored field — it fails validation twice and kills the run. That
                # is how this was found: the browser e2e went red because every
                # question in the compose stack failed.
                "supported_by": _cited(request.prompt_text),
                "confidence": "high",
            }
        )
    # Any other role — `intake`, `observe` — gets free text. The loop treats
    # these as prose, so there is no schema to satisfy and nothing to invent.
    return "A scripted reply."


class ScriptedProvider:
    """`LLMProvider`, answering from `scripted_body` and costing nothing.

    Usage is reported as zero rather than estimated from the text. The meter
    writes every call to `usage_ledger` either way, so the ledger still shows
    that a call *happened* — but pricing a fabricated call would put invented
    money in a table whose whole purpose is to answer "what did this cost".
    """

    def capabilities(self) -> ProviderCaps:
        return ProviderCaps(
            name=PROVIDER_NAME,
            # No native structured decoding: replies go through
            # `llm/structured.py` like a prompted provider's, so the smoke
            # exercises the same parsing path a real deployment uses.
            supports_response_schema=False,
            supports_system_message=True,
            is_stub=True,
        )

    async def complete(self, request: LLMRequest) -> Completion:
        return Completion(
            text=scripted_body(request),
            model=request.model,
            provider=PROVIDER_NAME,
            usage=Usage(input_tokens=0, output_tokens=0),
            latency_ms=0,
        )

    async def aclose(self) -> None:
        """Nothing to release: this provider opens no client."""
        return None
