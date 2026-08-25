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

``chart`` is WP11.1's, and it rides here rather than on ``Plan`` for two
reasons. It is asked **once per run instead of once per step**, which is the
whole of the schema cost this adds (B-033 keeps the shape closed, so a field
exists on every call whether or not it is used). And this is the moment the model
knows what it answered and which execution backs it — a chart belongs to an
answer, which is also why B-048 puts it inside the answer card rather than beside
it.

**The model chooses the chart; it does not get to draw an impossible one.**
`charts.decide` can refuse a chart the data cannot support, but it cannot know
*which* chart answers the question — the same numbers are a comparison or a trend
depending on what was asked, and picking one from the data alone would be exactly
the silent choice B-060 was filed for.

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

__all__ = ["FINALIZE", "ChartAsk", "FinalizeIn"]


class ChartAsk(BaseModel):
    """The chart to draw, if one helps (WP11.1).

    One nested field on ``FinalizeIn`` rather than four flat ones, and every
    member defaulted rather than optional: B-033 wants a closed schema a provider
    can constrain natively, and ``None`` is the shape that costs a repair round.
    An empty ``of`` means no chart was asked for, which is the common case — the
    same idiom ``Plan.define`` uses for a lookup nobody needs.
    """

    model_config = ConfigDict(extra="forbid")

    of: str = Field(
        default="",
        max_length=64,
        description=(
            "Leave empty unless a chart genuinely helps. Otherwise the execution "
            "id from supported_by whose rows should be drawn."
        ),
    )
    mark: str = Field(default="", description="bar | line | point | area")
    x: str = Field(default="", max_length=120, description="Column for the horizontal axis")
    y: str = Field(
        default="", max_length=120, description="Column for the vertical axis. Must hold numbers."
    )
    series: str = Field(
        default="",
        max_length=120,
        description=(
            "Optional column to split the marks by colour, when the split is "
            "part of the answer — revenue by month *per channel*. Leave empty "
            "for a single series: colouring one series by its own x values adds "
            "no information the axis does not already carry, and is refused."
        ),
    )


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
            "not answer, say what is missing instead — do not guess. Write about "
            "the data and never about the chart: whether one can be drawn is "
            "decided after you answer, and the card says so in its own place. An "
            "apology for what a chart cannot do reads to the reader as the "
            "product being broken."
        ),
    )
    unanswered: str = Field(
        default="",
        max_length=200,
        description=(
            "The part of the question you could not answer, in a few words — "
            "'the cost', 'the date range'. Empty if you answered all of it. Name "
            "the part, not the reason; the reason belongs in your answer."
        ),
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
    chart: ChartAsk = Field(
        default_factory=ChartAsk,
        description=(
            "A chart of one of the results above, when a picture says something "
            "the sentence cannot — a trend over time, a comparison across "
            "categories. Leave `of` empty when it would not."
        ),
    )

    @property
    def claims_an_answer(self) -> bool:
        """Whether this draft asserts something a reviewer should check.

        **Replaces the removed `answered` boolean at the one place it was still
        doing work** (**D-044**). Four critic rules used `draft.answered` to mean
        *"is this a draft making a positive claim"* — not *"how much of the
        question did it cover"* — and that question has an observable answer, so
        it no longer needs to be asked of a model.

        A draft is not a claim when it cites nothing **and** names what it could
        not answer: that is a refusal, and the rules skip it exactly as they did
        before. Everything else is checked, which now includes a **partial**
        answer with citations — the case B-134 is about, and one the old boolean
        skipped precisely when its cited half deserved review.
        """
        return bool(self.supported_by) or not self.unanswered


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
