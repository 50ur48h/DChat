"""One model call that turns a question into SQL (architecture Part 4.3).

Planning in the single-shot runner is deliberately small: one call, one
structured answer, grounded in the context bundle rather than in the raw
catalog. Architecture 4.3's fuller planner — two to six re-plannable steps, a
capability pre-check — belongs to Phase 8's loop; what is here is the part M7
needs and no more.

**The role is `sql`, not `plan`.** This call writes a statement, and STATUS is
explicit about not economising on it: the DAL refuses SQL it cannot ground, so a
weaker model buys a refusal, a repair round trip and another billed call. `plan`
is the tier the Phase 8 planner will use for deciding *what to investigate*,
which is a different job.

**The schema is closed and required.** `extra="forbid"` with no optional fields
lets a provider constrain decoding natively where it can (B-033); where it
cannot, `llm.complete` renders the schema into the prompt and repairs once. Both
paths end at the same pydantic object, which is the point of the front door.

**The task asks for aliased projections, and that is a mitigation rather than a
fix.** Canonicalising runs sqlglot's qualifier, which names an unaliased output
column `_col_1` — stable, unambiguous, and meaningless to the person now being
shown it in WP7.3's evidence panel (**B-020**). A model that aliases its own
columns produces a better name than any pass we could write, so it is asked to.
It may forget, and then `_col_1` is what a person sees: the deterministic naming
pass in `dal/validator.py` is still owed, and asking nicely here does not close
that item.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from dataagent.agent.context import ContextBundle, render
from dataagent.agent.tools.registry import ToolRegistry
from dataagent.config import Settings
from dataagent.llm import service as llm
from dataagent.llm.base import CallLimits, Message

__all__ = ["Plan", "plan_query"]


class Plan(BaseModel):
    """What the model proposes to run, and why."""

    model_config = ConfigDict(extra="forbid")

    sql: str = Field(
        min_length=1,
        max_length=20_000,
        description=(
            "One SELECT statement answering the question, using only tables and "
            "columns from the reference data. No trailing semicolon needed."
        ),
    )
    purpose: str = Field(
        min_length=1,
        max_length=300,
        description="One line on what this query establishes. It appears in the run's trace.",
    )
    answerable: bool = Field(
        description=(
            "False if the reference data cannot answer the question at all — say "
            "so rather than writing a query that looks plausible and is not."
        )
    )
    reason: str = Field(
        default="",
        max_length=600,
        description="When answerable is false, what is missing. Empty otherwise.",
    )


#: Room to write the statement this call's own schema allows.
#:
#: `CallLimits` defaults to 1,024 output tokens, which is about 4,000 characters
#: — while `Plan.sql` permits 20,000. A schema that promises more room than the
#: call can deliver truncates the reply **mid-string**, and a truncated JSON
#: object is not a parse failure the repair path can fix: the second attempt is
#: cut off in the same place. The M8 revenue-decline scenario failed exactly
#: this way, with `did not produce valid Plan in two attempts`.
#:
#: Sized to the schema rather than guessed: a 20,000-character statement is
#: ~5,000 tokens, plus the envelope and the other fields.
PLAN_OUTPUT_TOKENS = 6_000

#: Appended to the assembled context for this one call. Kept here rather than in
#: `context.py` because it is about *this* step, not about the run: the layered
#: prompt describes the world, and this describes the task.
_TASK = """\
Write one SQL SELECT that answers the question, using only the tables and columns
in the reference data above.

Give every column you select a short, readable alias — `count(*) AS order_count`,
not a bare `count(*)`. The person who asked will be shown these column names.

Before you write it: if the reference data does not contain what the question
needs, set answerable to false and say what is missing in reason. Do not invent a
table or a column, and do not write a query that is merely plausible — a
statement naming anything outside the catalog will be refused, and refusals cost
the person asking a round trip for nothing."""


async def plan_query(
    *,
    org_id: uuid.UUID,
    bundle: ContextBundle,
    registry: ToolRegistry,
    role: str = "reader",
    run_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    settings: Settings | None = None,
    repair_of: str | None = None,
) -> tuple[Plan, int]:
    """Ask for a statement. Returns the plan and the tokens it cost.

    ``repair_of`` carries the previous failure back in — the second and last
    attempt the M7 criterion allows. It is a separate parameter rather than
    something the caller splices into the question, so that a repair is visibly
    a repair in the prompt and in this signature, and so it cannot be issued
    twice by accident.
    """
    messages = list(render(bundle))
    task = _TASK if repair_of is None else f"{_TASK}\n\n{repair_of}"
    # The tool list goes in the system turn: it describes what exists, which is
    # world rather than task, and putting it beside the question would let a
    # crafted question sit next to the capability list.
    messages[0] = Message(
        role="system", content=f"{messages[0].content}\n\n[L4] Tools\n{registry.describe_for(role)}"
    )
    messages.append(Message(role="user", content=task))

    completion = await llm.complete(
        role="sql",
        org_id=org_id,
        messages=messages,
        schema=Plan,
        limits=CallLimits(max_output_tokens=PLAN_OUTPUT_TOKENS),
        run_id=run_id,
        actor_user_id=actor_user_id,
        settings=settings,
    )
    return completion.parsed_as(Plan), completion.usage.total_tokens
