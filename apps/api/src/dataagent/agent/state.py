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
from collections.abc import Sequence
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ExecutionRef",
    "Hypothesis",
    "ResearchState",
    "StateFinding",
    "Step",
    "merge_by_evidence",
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
    #: The tables this statement read, as the validator resolved them. Kept so
    #: the answer can say which source it came from when the question had more
    #: than one (**B-093**).
    tables: list[str] = Field(default_factory=list[str])
    row_count: int | None = None
    summary: str = ""
    ok: bool = True
    #: Why this query never returned, in the connector's own sanitized words,
    #: and **only when rewriting the SQL could not have fixed it** (**B-095**).
    #: The condition is the whole point of the field. A statement the policy
    #: refused is the loop working — the next planner is told what was wrong and
    #: corrects it — so caveating that in the answer would be a warning about
    #: nothing, on most runs, which is how a reader learns to skip warnings. A
    #: database that could not be reached is a hole nothing filled, and the
    #: person who asked is the one who has to be told. `summary` still carries
    #: every failure either way, because the model needs to see the repairable
    #: ones in order to repair them.
    error: str = ""


class StateFinding(BaseModel):
    """Something the run concluded, and the executions that back it."""

    model_config = ConfigDict(extra="forbid")

    statement: str
    support: list[str] = Field(default_factory=list[str])
    confidence: str = "medium"


#: Weakest first. A merged claim is only as strong as its least certain part.
_CONFIDENCE_ORDER = ("low", "medium", "high")


def merge_by_evidence(findings: Sequence[StateFinding]) -> list[StateFinding]:
    """Findings from one reflection resting on the same executions, as one claim.

    **The rule is B-107's; this is the half that keeps the better sentence.**
    Keying on the citation set and dropping the loser is what the owner asked to
    be checked before it was built, and checking it is what showed it choosing
    badly: the run that prompted this recorded *"Monthly revenue for the last
    four completed calendar months was $135,950.59 in April 2026, …"* and
    *"Revenue peaked in May 2026 and then declined in both June and July"* — in
    that order, from **one** reflection. First-wins keeps the enumeration and
    discards the shape, which is the worse of the two by the codebase's own
    reckoning: **B-097** says that when a chart is drawn the prose should give the
    shape and let the picture carry the detail. Last-wins would keep the right one
    here and the wrong one whenever a model happened to emit them the other way
    round. A rule keyed on evidence has no basis for preferring either sentence,
    so it should not be made to try.

    So they are joined. One claim, one citation, one confidence badge — which is
    the defect that was actually visible, two badges and two *"show the query"*
    controls over a single query — and nothing a model wrote is thrown away.

    **Before anything is persisted, and never after.** A finding row and its
    `finding_added` event are written together, so a merge that happened later
    would need to rewrite a row whose event already said something else. Merging
    the candidates from one reflection needs no such path: what reaches
    `add_finding` is already one claim per set of evidence.

    Uncited findings are never merged. They share the empty set with each other,
    and evidence is the only thing this function knows how to compare.
    """
    merged: list[StateFinding] = []
    at: dict[tuple[str, ...], int] = {}
    for finding in findings:
        key = tuple(sorted(finding.support))
        if not key or key not in at:
            if key:
                at[key] = len(merged)
            merged.append(finding)
            continue
        held = merged[at[key]]
        statement = held.statement.rstrip()
        if not statement.endswith((".", "!", "?", ":", ";")):
            statement += "."
        merged[at[key]] = held.model_copy(
            update={
                "statement": f"{statement} {finding.statement.lstrip()}",
                "confidence": min(
                    (held.confidence, finding.confidence),
                    key=lambda word: (
                        _CONFIDENCE_ORDER.index(word) if word in _CONFIDENCE_ORDER else 1
                    ),
                ),
            }
        )
    return merged


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

    #: WP8.2's join-path verdicts and WP9.1's verdict — both written now.
    capability: dict[str, Any] = Field(default_factory=dict[str, Any])
    #: What period the question asked for, against what the data holds (D-051).
    #: On the state rather than recomputed, for the reason `capability` is: the
    #: composer and the ending both read it, and a second resolution could
    #: disagree with the one the planner was told about.
    coverage: dict[str, Any] = Field(default_factory=dict[str, Any])
    #: Whether the one permitted retry of a model-judgement refusal has been
    #: spent (**D-055**). On the state rather than in a local, so an interrupted
    #: run that comes back cannot quietly buy a second one.
    retried_judgement: bool = False
    #: What the planner said when it first judged the question unanswerable.
    #: Kept because the retried answer must still name that gap, and a reader
    #: comparing the two is checking exactly the thing the owner asked to be
    #: guarded.
    judgement_reason: str = ""
    critic: dict[str, Any] | None = None

    #: How many drafts the critic has judged. Bounded by `MAX_CRITIC_PASSES`, and
    #: on the state rather than in a local so an interrupted run cannot come back
    #: and claim a fresh re-entry it has already spent (architecture M9's "at
    #: most one" is a property of the run, not of one call to the runner).
    critic_passes: int = 0

    #: Terms this run has looked up in the organization's documents (**B-075**,
    #: D-032), lowercased. On the state rather than in a local for the reason
    #: `critic_passes` is: an interrupted run must not come back and spend the
    #: lookup budget again, and asking the same question of the same corpus twice
    #: is the duplicate-query rule's failure wearing different clothes.
    lookups: list[str] = Field(default_factory=list[str])

    #: Of those, the terms the documents actually had something to say about
    #: (**D-033**). Separate from `lookups` because the two answer different
    #: questions: `lookups` bounds the cap and refuses a repeat, and must
    #: therefore count the ones that found nothing; this one drives the answer's
    #: limitation, and a term the corpus could not explain left the model no
    #: worse informed than it was — caveating it would be a warning about
    #: nothing, which is how a reader learns to skip warnings.
    prose_terms: list[str] = Field(default_factory=list[str])

    #: Semantic definitions this question matched and the critic therefore
    #: enforces (**D-033**, WP10.2c). On the state so the trace and the
    #: answer's limitations can tell an enforced definition from a passage the
    #: run merely read — which is the entire distinction the layer exists for.
    applied_definitions: list[str] = Field(default_factory=list[str])
    #: What each applied definition makes the **answer** say (revision 0033).
    #: Carried on the state rather than re-read, for the reason `capability` is:
    #: the composer must caveat the definitions the *planner was shown*, and a
    #: second read could pick up an edit made while the run was in flight.
    definition_caveats: dict[str, str] = Field(default_factory=dict[str, str])

    #: How many active definitions this data source had when the question was
    #: asked (**B-087**). Recorded because the interesting number is not how
    #: many matched but how many *could* have: none matched out of none is
    #: silence worth keeping, and none matched out of eighteen is the sentence
    #: three gate walks needed and never got. Without this the two are
    #: indistinguishable downstream and the honest message cannot be written.
    definitions_available: int = 0
    #: The tables this question retrieved that have figures to aggregate — the
    #: sources it could have been answered from (**B-093**). Recorded at context
    #: time, where the cards are in hand, because by the time the answer is
    #: composed all that survives is a list of names.
    candidate_sources: list[str] = Field(default_factory=list[str])

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

    def every_query_failed(self) -> bool:
        """Queries were attempted and not one of them came back (**B-095**).

        Deliberately not the same thing as "nothing ran". A run that answered
        from the catalog or from a document without querying is a legitimate
        ending and has always composed an answer. This is the run that *asked*
        the database and never heard back, and the two differ in the only way
        that matters here: whether there is any evidence to compose from.
        """
        return bool(self.executions) and not any(reference.ok for reference in self.executions)

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

        **Two claims resting on exactly the same executions are one claim,
        whatever words they use** (**B-107**). That is the Phase 7 rule, and
        B-096 put it into `runner._write_ending` — where it stopped, because the
        composer was the layer that had been caught. This is the other place a
        finding is recorded, and it was still comparing characters: a model that
        reflected twice on one query in different words kept both. The rule has
        no exception for which layer wrote the sentence.

        **Only for findings that cite something.** An uncited finding shares the
        empty set with every other uncited finding, so keying on evidence there
        would collapse every unsupported sentence in the run into the first one —
        a rule about evidence, applied where there is none.

        Findings that arrive *together*, from one reflection, are merged rather
        than dropped: see `merge_by_evidence`, which the loop applies before
        anything reaches here.
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
        if real and any(sorted(existing.support) == sorted(real) for existing in self.findings):
            # Restated on evidence this run has already concluded from. The
            # earlier finding stands, and the iteration counts as barren —
            # which is the honest reading: nothing new was asked of the data.
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
