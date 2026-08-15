"""How a run says it is done (architecture Part 4.6's ``finalize``).

A tool rather than "whatever the model wrote last", because finishing is a
decision with a shape: an answer, the executions that back it, and — when the
data could not answer the question — the reason, stated plainly.

Two fields carry the weight.

``supported_by`` is the citation, and it is checked. A model may only name
execution ids that this run really produced; the runner rejects anything else
rather than passing it on, because architecture 4.2 makes findings-cite-real-rows
the spine of trust and an unverifiable citation is worse than none — it looks
like evidence.

``answered`` is how a refusal stays honest. A run that could not answer sets it
false and says why in ``answer``; nothing downstream has to infer refusal from
the shape of a sentence, and the composer in Phase 9 gets a fact rather than a
guess.

The handler is deliberately inert: it validates and echoes. Writing the answer,
the findings and the run's ending is the runner's job, because those are
transitions and only one thing may own them.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dataagent.agent.tools.base import Tool, ToolContext

__all__ = ["FINALIZE", "FinalizeIn"]


class FinalizeIn(BaseModel):
    """``extra="forbid"`` and every field required — so a provider that can
    constrain decoding natively enforces this shape rather than suggesting it
    (B-033), and the repair path becomes the exception."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(
        min_length=1,
        max_length=4000,
        description=(
            "The answer, in plain words, for the person who asked. If you could "
            "not answer, say what is missing instead — do not guess."
        ),
    )
    answered: bool = Field(
        description=(
            "True only if the data answered the question. False when you are "
            "explaining why it could not be answered."
        )
    )
    supported_by: list[str] = Field(
        default_factory=list[str],
        max_length=20,
        description=(
            "Execution ids from run_sql results in this run, and nothing else. "
            "Every number in your answer must come from one of them."
        ),
    )
    confidence: str = Field(
        default="medium", description="high | medium | low — how sure you are of this answer."
    )


class FinalizeOut(BaseModel):
    accepted: bool = True


async def _finalize(context: ToolContext, params: BaseModel) -> BaseModel:
    """Accept the model's ending. The runner is what acts on it."""
    return FinalizeOut()


FINALIZE = Tool(
    name="finalize",
    description=(
        "Finish the run with an answer. Cite the execution ids of the queries "
        "your answer rests on. If the data cannot answer the question, set "
        "answered=false and say what is missing."
    ),
    params=FinalizeIn,
    handler=_finalize,
)
