"""What a run knows about itself (architecture 4.2), and what survives a restart.

WP7.2b checkpointed a small `_State` — step, call count, execution ids — at every
step boundary, because 0.2.4 requires an interrupted run to be explicable. This
is that state grown into the one 4.2 specifies, and the reason it grows now is
that a *loop* has something to remember between iterations where a single-shot
run had almost nothing.

Three properties are load-bearing.

**Raw rows never accumulate.** 4.4's Observe step summarises a result into a
compact typed summary, and the state carries the summary plus an execution
reference — never the rows. A loop that appended result sets to its own state
would grow its next prompt with every iteration, which is how a bounded loop
becomes an expensive one; and the rows are already durable in
`result_artifacts`, masked, where a citation can reach them.

**`support` may only name executions this run produced.** 4.2 calls
`Finding.support` the spine of trust, and it is checkable precisely because the
ids are real `query_executions` rows. WP7.2b verified citations before storing
them; this keeps the same rule as the loop accumulates findings across
iterations, since a model that cited an id in iteration two has more chances to
invent one by iteration six.

**It round-trips.** The whole point of a checkpoint is being read back, so
``as_json`` and ``restore`` are inverses over everything the loop needs to
continue — proved by a test rather than asserted here. What is deliberately *not*
restored is the wall clock: a run resumed an hour later must not believe it has
been running for an hour, nor that it has a fresh 240 seconds. Phase 8's resume
path decides that explicitly rather than inheriting it by accident.

**The budget is stored beside this, not inside it.** 4.2 sketches `budget` as a
field of `ResearchState`, and 10.1 gives `agent_runs` two columns — `state` and
`budget` — so the allowance and the counters go in the second and everything here
goes in the first. Two columns rather than one nested object because they answer
different questions and are read by different things: "what was this run allowed
to spend, and what did it spend" is an operational question that should not
require parsing a research state to answer.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ExecutionRef",
    "Hypothesis",
    "ResearchState",
    "StateFinding",
    "Step",
]

#: Where the loop is. Named after 4.4's state diagram so the trace, the code and
#: the picture use one vocabulary.
Phase = Literal[
    "starting",
    "context",
    "planning",
    "executing",
    "observing",
    "reflecting",
    "validating",
    "composing",
    "finished",
]


class ExecutionRef(BaseModel):
    """A query this run ran, and one line about what came back.

    The summary is what the next prompt sees; the id is what a citation resolves
    through. Rows appear in neither — they are in `result_artifacts`, masked, one
    HTTP call away for a person who wants them (B-034).
    """

    model_config = ConfigDict(extra="forbid")

    execution_id: str
    purpose: str = ""
    #: Identifies *what was asked of the database*, so asking it again can be
    #: recognised and refused (4.4's duplicate-query rule).
    sql_hash: str = ""
    row_count: int | None = None
    summary: str = ""
    ok: bool = True


class StateFinding(BaseModel):
    """Something the run concluded, and the executions that back it."""

    model_config = ConfigDict(extra="forbid")

    statement: str
    support: list[str] = Field(default_factory=list[str])
    confidence: str = "medium"


class Hypothesis(BaseModel):
    """A thing the run is trying to establish, and where it got to."""

    model_config = ConfigDict(extra="forbid")

    text: str
    status: Literal["open", "supported", "rejected"] = "open"
    tested_by: list[str] = Field(default_factory=list[str])


class Step(BaseModel):
    """One plan step. Small and re-plannable, per 4.3."""

    model_config = ConfigDict(extra="forbid")

    purpose: str
    sql: str = ""
    status: Literal["pending", "done", "failed", "skipped"] = "pending"


class ResearchState(BaseModel):
    """Architecture 4.2's state, as much of it as Phase 8 fills in.

    Fields 4.2 names that later work packages own are present and empty rather
    than absent — `capability` is WP8.2's, `critic` is WP9.1's — so the shape a
    checkpoint round-trips does not change underneath a run that is already
    stored. A field that appears later would make every state written before it
    unreadable by the code that reads them.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: uuid.UUID
    org_id: uuid.UUID
    question: str = ""
    #: The date this run resolved "last month" against (**D-027**). Stored as an
    #: ISO string on the checkpoint, so a run resumed tomorrow keeps yesterday's
    #: anchor — an interrupted investigation that silently changed what "recently"
    #: meant halfway through would be worse than one that failed.
    as_of: str = ""

    phase: Phase = "starting"
    iteration: int = 0

    table_names: list[str] = Field(default_factory=list[str])
    plan: list[Step] = Field(default_factory=list[Step])
    executions: list[ExecutionRef] = Field(default_factory=list[ExecutionRef])
    findings: list[StateFinding] = Field(default_factory=list[StateFinding])
    hypotheses: list[Hypothesis] = Field(default_factory=list[Hypothesis])
    open_questions: list[str] = Field(default_factory=list[str])

    #: WP8.2's join-path verdicts and WP9.1's verdict. Declared now so the stored
    #: shape is stable; neither is written by this work package.
    capability: dict[str, Any] = Field(default_factory=dict[str, Any])
    critic: dict[str, Any] | None = None

    #: Set when a ceiling stopped the run, so the composed answer can say which.
    stopped_by: str | None = None
    #: How many consecutive iterations added nothing — 4.4's monotone-progress
    #: rule counts here rather than in the loop, so a resumed run does not get a
    #: fresh two attempts at going nowhere.
    barren_iterations: int = 0

    def execution_ids(self) -> tuple[str, ...]:
        """The ids a citation may name — successful executions only.

        A refused statement is recorded here as well, because the next planner
        needs to know it was refused and the duplicate rule needs its hash. But
        it produced no result, so **nothing may cite it**: a claim resting on a
        query that never ran would be exactly the unverifiable evidence 4.2's
        support list exists to prevent.
        """
        return tuple(
            reference.execution_id
            for reference in self.executions
            if reference.ok and reference.execution_id
        )

    def has_run(self, sql_hash: str) -> bool:
        """Whether this exact statement has already been sent (4.4).

        By hash of the *validated* statement rather than of the model's text, so
        two spellings of one query are recognised as the same question. Without
        this a loop that has run out of ideas re-runs its best one, spending a
        query budget to learn nothing.
        """
        return bool(sql_hash) and any(
            reference.sql_hash == sql_hash for reference in self.executions
        )

    def record_execution(self, reference: ExecutionRef) -> None:
        self.executions.append(reference)

    def add_finding(self, finding: StateFinding) -> bool:
        """Keep a finding, dropping citations this run did not produce.

        Returns whether anything was actually added, which is what the
        progress rule counts. A finding whose every citation was invented is
        **not** kept: it would look like evidence, and 4.2 makes the support
        list the reason anyone should believe the answer.
        """
        real = [item for item in finding.support if item in self.execution_ids()]
        if finding.support and not real:
            return False
        if any(
            existing.statement.strip() == finding.statement.strip() for existing in self.findings
        ):
            # The same sentence twice is not progress, and would let a model
            # keep the loop alive by repeating itself.
            return False
        self.findings.append(finding.model_copy(update={"support": real}))
        return True

    def as_json(self) -> dict[str, Any]:
        """The checkpoint written to `agent_runs.state` at every transition."""
        return self.model_dump(mode="json")

    @classmethod
    def restore(cls, payload: Any) -> Self | None:
        """Read a checkpoint back, or None if it is not one.

        None rather than raising: a run whose state predates this shape, or was
        written by an older build, must be *reportable* rather than a crash on
        the resume path. The caller decides whether to start over.
        """
        if not isinstance(payload, dict):
            return None
        try:
            return cls.model_validate(payload)
        except Exception:
            return None
