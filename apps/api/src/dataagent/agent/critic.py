"""Is this draft answer defensible? (architecture 4.5, M9)

Two stages, and the order is the design. **Stage 1 is deterministic and free**:
it holds the rules a model should not be asked to adjudicate, because a model
asked whether a number appears in a result will sometimes say yes about a number
that does not. **Stage 2 is one cheap model call** against a fixed rubric, for
the judgements no rule can make — correlation offered as causation, a dimension
nobody looked at, a sample too small to carry the claim.

**Stage 1 runs first so stage 2 often need not run at all.** A draft that cites
an execution this run never produced, or answers "July" from June's rows, is
wrong for a reason arithmetic can state; spending a model call to be told so
would be paying for a worse version of an answer already in hand. The test that
matters here asserts exactly that: a wrong-date-range draft is caught with **no
model call**.

**Findings carry a severity, and most of them warn.** `BLOCK` sends the run back
round once; `WARN` travels into the answer as a limitation and changes nothing
else. That split is architecture 4.5's, which says of the numbers check that
*"violations become warnings in V1, blocks in V1.1"* — a critic that blocks on a
heuristic would refuse good answers, and a false block is this component's
version of the false refusal the capability check spent WP8.4 avoiding.

**What the critic may not do.** It never re-runs a query, never reads a customer's
rows, and never edits an answer. It reads what is already durable — the
executions of this run, their statements, their row counts — and returns
findings. Everything it says is recorded in `critic_verdict`, which is in 10.3's
closed vocabulary and already has a label in the trace UI.

**The stated-range check is what D-027 bought.** Until a run knew what "today"
was, *"revenue last month"* had no range to compare a statement against; now it
does, so "the dates in the SQL cover the period the question asked for" is a
check rather than a guess. Where a question states no range this stays silent,
and that bias is deliberate: a critic that misparses a question and blocks on its
own mistake is worse than one that says nothing.
"""

from __future__ import annotations

import re
import uuid
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from dataagent.agent.context import HistoryTurn, history_block
from dataagent.agent.state import ResearchState
from dataagent.agent.tools.finalize import FinalizeIn
from dataagent.semantic.definitions import Definition as SemanticDefinition
from dataagent.semantic.definitions import RequiredFilter

__all__ = [
    "BLOCK",
    "WARN",
    "CriticFinding",
    "CriticVerdict",
    "Verdict",
    "check",
    "stated_range",
]

#: A finding that sends the run back round, at most once (M9).
BLOCK = "block"
#: A finding that becomes a limitation in the answer and stops nothing.
WARN = "warn"

#: The three verdicts architecture 4.5 names, as a type so the LLM half's schema
#: and the loop's branching cannot drift apart.
Verdict = Literal["pass", "revise", "insufficient_evidence"]

_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

#: Months as a person writes them. Both the full name and the three-letter form,
#: because a question says "in Jul 2026" as often as "in July 2026". Keyed on the
#: first three letters throughout, which makes the two spellings one lookup.
_MONTHS: dict[str, int] = {
    name.lower()[:3]: number for number, name in enumerate(_MONTH_NAMES, start=1)
}

# `\w*` after the three-letter stem, so "Jul", "July" and "July's" all match the
# same group — and the group is only ever read through its first three letters.
_MONTH_YEAR = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS)) + r")\w*\s+(\d{4})\b",
    re.IGNORECASE,
)
# "1 March 2026" / "15th Mar 2026" — a day, a month and a year, which is how a
# person writes a bounded range in a sentence. Two of these make a range, and
# they must be read *before* the month-year pattern, which sees the same words
# and calls them a whole month.
_DAY_MONTH_YEAR = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(sorted(_MONTHS)) + r")\w*\s+(\d{4})\b",
    re.IGNORECASE,
)
_BARE_YEAR = re.compile(r"\b(19|20)(\d{2})\b")
_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_LAST_MONTH = re.compile(r"\blast\s+(?:full\s+|complete\s+)?month\b", re.IGNORECASE)
_LAST_YEAR = re.compile(r"\blast\s+(?:full\s+|complete\s+)?year\b", re.IGNORECASE)

#: Numbers in a draft answer, with the separators a composed sentence uses.
#: Anchored on a digit so a bare year inside a date is not read as a claim.
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

#: Below this, a number in prose is ordinal or structural — "the top 5", "two
#: stores" — rather than a figure taken from a result, and checking it against
#: the rows produces noise instead of signal.
SMALL_NUMBER = 10


@dataclass(frozen=True, slots=True)
class CriticFinding:
    """One thing wrong with a draft, and how badly."""

    rule: str
    severity: str
    detail: str

    def as_payload(self) -> dict[str, object]:
        return {"rule": self.rule, "severity": self.severity, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class CriticVerdict:
    """What both stages concluded, together."""

    verdict: Verdict
    findings: tuple[CriticFinding, ...] = ()
    #: Set when the LLM half ran, so the trace can distinguish "the rules were
    #: satisfied and a model agreed" from "the rules were satisfied and nobody
    #: looked" — a run whose budget stopped the second stage is the latter.
    consulted_model: bool = False

    @property
    def blocked(self) -> bool:
        return self.verdict != "pass"

    @property
    def blocking(self) -> tuple[CriticFinding, ...]:
        return tuple(finding for finding in self.findings if finding.severity == BLOCK)

    @property
    def warnings(self) -> tuple[CriticFinding, ...]:
        return tuple(finding for finding in self.findings if finding.severity == WARN)

    def reasons(self) -> str:
        """The findings as one line each, for the composer's second attempt."""
        return "\n".join(f"- {finding.detail}" for finding in self.findings)

    def as_payload(self) -> dict[str, object]:
        """10.3's ``critic_verdict`` payload. The vocabulary is closed and this
        type was already in it, so nothing new had to be invented."""
        return {
            "verdict": self.verdict,
            "consulted_model": self.consulted_model,
            "findings": [finding.as_payload() for finding in self.findings],
        }


class CriticOut(BaseModel):
    """The LLM half's structured verdict (architecture 4.5).

    Small on purpose. **B-052** is the standing hazard — a structured call's
    output ceiling can be smaller than the schema it must fill — and the answer
    to it here is a schema that cannot be long: three reasons, 300 characters
    each, is a rubric result rather than an essay.
    """

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict = Field(
        description=(
            "pass if the draft is defensible from the evidence; revise if it "
            "overstates, misreads or contradicts the evidence; "
            "insufficient_evidence if answering it properly needs a query nobody ran."
        )
    )
    reasons: list[str] = Field(
        default_factory=list[str],
        max_length=3,
        description=(
            "One short sentence per problem, naming what is wrong and what would "
            "fix it. Empty when the verdict is pass."
        ),
    )


@dataclass
class _Range:
    """A period a question named, and how it was written."""

    start: date
    end: date  # exclusive
    phrase: str = ""

    def covered_by(self, literals: tuple[str, ...]) -> bool:
        """Whether a statement's own dates reach this period.

        Deliberately loose at the upper end: a query may write the last day
        inclusive (`<= '2026-07-31'`) or the next day exclusive (`< '2026-08-01'`)
        and both are correct. Requiring one spelling would fail good SQL.
        """
        if not literals:
            return False
        seen = set(literals)
        last_inclusive = date.fromordinal(self.end.toordinal() - 1).isoformat()
        starts = self.start.isoformat() in seen
        ends = self.end.isoformat() in seen or last_inclusive in seen
        return starts and ends


def stated_range(question: str, as_of: date) -> _Range | None:
    """The period a question names, resolved against this run's ``as_of``.

    Only unambiguous forms, and nothing inferred. A question that says nothing
    about time returns None and the range check does not fire — which is the
    common case and the safe one. **D-027** is what makes the relative forms
    resolvable at all; before it, "last month" had no fixed meaning to compare
    against.
    """
    text = question.strip()

    pair = _ISO_DATE.findall(text)
    if len(pair) >= 2:
        first, second = date.fromisoformat(pair[0]), date.fromisoformat(pair[1])
        if first <= second:
            # Written inclusively by a person; held exclusively here, so the
            # comparison against a statement's `<` and `<=` is one rule.
            return _Range(first, date.fromordinal(second.toordinal() + 1), f"{pair[0]}..{pair[1]}")

    # A bounded range written in words, before the whole-month reading of the
    # same words. "revenue between 1 March 2026 and 15 March 2026" names two days
    # inside a month; reading it as the month blocked a correct query with a
    # wrong-range refusal — a false block, found by golden eval #18.
    days = _DAY_MONTH_YEAR.findall(text)
    if len(days) >= 2:
        first = date(int(days[0][2]), _MONTHS[days[0][1].lower()[:3]], int(days[0][0]))
        second = date(int(days[1][2]), _MONTHS[days[1][1].lower()[:3]], int(days[1][0]))
        if first <= second:
            return _Range(
                first,
                date.fromordinal(second.toordinal() + 1),
                f"{first.isoformat()}..{second.isoformat()}",
            )

    months = _MONTH_YEAR.findall(text)
    if len(months) == 1:
        month, year = _MONTHS[months[0][0].lower()[:3]], int(months[0][1])
        start = date(year, month, 1)
        return _Range(start, _add_month(start), f"{_MONTH_NAMES[month - 1]} {year}")
    if len(months) > 1:
        # Two months named and no days between them: the question spans something
        # this parser cannot pin down, so it says nothing rather than guessing.
        return None

    if _LAST_MONTH.search(text):
        first_of_this = as_of.replace(day=1)
        start = _sub_month(first_of_this)
        return _Range(start, first_of_this, "last month")

    if _LAST_YEAR.search(text):
        year = as_of.year - 1
        return _Range(date(year, 1, 1), date(year + 1, 1, 1), "last year")

    year_match = _BARE_YEAR.search(text)
    if year_match:
        year = int(year_match.group(0))
        return _Range(date(year, 1, 1), date(year + 1, 1, 1), str(year))

    return None


def _add_month(day: date) -> date:
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def _sub_month(day: date) -> date:
    year, month = (day.year - 1, 12) if day.month == 1 else (day.year, day.month - 1)
    return date(year, month, min(day.day, monthrange(year, month)[1]))


@dataclass(frozen=True, slots=True)
class Evidence:
    """What the critic is allowed to look at.

    Assembled by the caller from durable rows, so the critic itself opens no
    session and can be tested without one. `statements` is keyed by execution id
    and holds the SQL that **actually ran**, read back from `query_executions`
    rather than from the state's memory of it — a state bug must not be able to
    hide a critic failure.
    """

    question: str
    as_of: date
    state: ResearchState
    statements: dict[str, str] = field(default_factory=dict[str, str])
    previews: tuple[tuple[str, str], ...] = ()
    dialect: str = "postgres"
    #: The thread the question sits in (**D-029**). The stage-2 critic needs it
    #: for the same reason the planner does, and needs it *more*: asked to judge
    #: whether a draft answers *"check again"*, a model with no thread will say
    #: it does not — a **false block** on a correct answer, which the standing
    #: note calls the critic's characteristic failure. The deterministic rules
    #: deliberately ignore it and still read the current question alone, because
    #: every one of them is an arithmetic check on words the user just typed.
    history: tuple[HistoryTurn, ...] = ()
    #: The definitions this question matched (**D-033**, WP10.2c). Loaded by the
    #: caller from `semantic_definitions`, like `statements`, so the critic opens
    #: no session — and empty for the overwhelming majority of runs, because most
    #: questions are about rows rather than about a defined measure.
    definitions: tuple[SemanticDefinition, ...] = ()


def check(draft: FinalizeIn, evidence: Evidence) -> tuple[CriticFinding, ...]:
    """Stage 1. Every deterministic rule architecture 4.5 names, and no model.

    Returns findings rather than a verdict: combining them with the LLM half's
    opinion is the caller's job, and a function that both judged and decided
    would make the "did a model see this" question unanswerable.
    """
    findings: list[CriticFinding] = []
    findings += _citations_resolve(draft, evidence)
    findings += _range_matches(draft, evidence)
    findings += _not_built_on_nothing(draft, evidence)
    findings += _numbers_appear_in_results(draft, evidence)
    findings += _refusal_is_not_an_answer(draft, evidence)
    findings += _required_filters(draft, evidence)
    return tuple(findings)


def _citations_resolve(draft: FinalizeIn, evidence: Evidence) -> list[CriticFinding]:
    """Every cited execution belongs to this run and succeeded.

    A block, not a warning: a citation nobody can resolve looks exactly like
    evidence while being none, which is the failure 4.2's support list exists to
    prevent.
    """
    real = set(evidence.state.execution_ids())
    invented = [cited for cited in draft.supported_by if cited not in real]
    if not invented:
        return []
    return [
        CriticFinding(
            rule="citation_resolves",
            severity=BLOCK,
            detail=(
                f"The answer cites {', '.join(sorted(invented))}, which this run did not "
                "produce as a successful query. Cite only executions that ran and returned."
            ),
        )
    ]


def _range_matches(draft: FinalizeIn, evidence: Evidence) -> list[CriticFinding]:
    """The dates in the SQL cover the period the question asked for.

    The flagship check, and the one D-027 made possible. Fires only when the
    question named a period *and* at least one statement wrote dates down: a
    query with no date literals is filtering on something else, and reading that
    as a wrong range would block a correct answer.
    """
    from dataagent.dal.validator import date_literals

    if not draft.answered:
        return []
    wanted = stated_range(evidence.question, evidence.as_of)
    if wanted is None:
        return []

    dated = {
        execution_id: date_literals(sql, dialect=evidence.dialect)
        for execution_id, sql in evidence.statements.items()
    }
    with_dates = {key: value for key, value in dated.items() if value}
    if not with_dates:
        return []
    if any(wanted.covered_by(literals) for literals in with_dates.values()):
        return []

    saw = sorted({literal for literals in with_dates.values() for literal in literals})
    return [
        CriticFinding(
            rule="range_matches",
            severity=BLOCK,
            detail=(
                f"The question asks about {wanted.phrase} — "
                f"{wanted.start.isoformat()} to {wanted.end.isoformat()} — but no query "
                f"covered it. The dates used were {', '.join(saw)}. Re-run the query over "
                "the period the question asked for."
            ),
        )
    ]


def _not_built_on_nothing(draft: FinalizeIn, evidence: Evidence) -> list[CriticFinding]:
    """An answer is not built on zero rows without saying so.

    A block when every cited execution came back empty and the draft still claims
    an answer: "there were no orders" is a fine answer and `answered=false` with a
    reason is how it is said, but a positive claim resting on nothing is not.
    """
    if not draft.answered or not draft.supported_by:
        return []
    counts = {
        reference.execution_id: reference.row_count
        for reference in evidence.state.executions
        if reference.ok
    }
    cited = [counts.get(execution_id) for execution_id in draft.supported_by]
    if cited and all(count == 0 for count in cited):
        return [
            CriticFinding(
                rule="row_count_sanity",
                severity=BLOCK,
                detail=(
                    "Every query this answer cites returned no rows, and the answer still "
                    "states a result. Say that the data contains nothing for this question "
                    "instead of answering from an empty result."
                ),
            )
        ]
    return []


def _numbers_appear_in_results(draft: FinalizeIn, evidence: Evidence) -> list[CriticFinding]:
    """Figures in the answer come from a result that was actually returned.

    **A warning in V1, by architecture 4.5's own instruction**, and the reason is
    visible in the implementation: prose rounds, restates and computes — "$938.28"
    may be the difference between two figures neither of which appears as written.
    Blocking on that would refuse correct arithmetic. What it is good for is the
    number invented outright, which shows up here as a figure matching nothing.
    """
    if not draft.answered or not evidence.previews:
        return []
    haystack = " ".join(rendered for _, rendered in evidence.previews)
    seen = {_clean(value) for value in _NUMBER.findall(haystack)}
    if not seen:
        return []

    # Numbers the *question* already contained are not claims the answer
    # computed — "July 2026" puts 2026 in the answer, and warning that the year
    # appears in no result is noise. Found live on the first real question after
    # this shipped, which is what running it before a gate is for.
    asked = {_clean(raw) for raw in _NUMBER.findall(evidence.question)}

    unmatched: list[str] = []
    for raw in _NUMBER.findall(draft.answer):
        value = _clean(raw)
        if value is None or abs(value) < SMALL_NUMBER or value in asked:
            continue
        if not any(_close(value, candidate) for candidate in seen if candidate is not None):
            unmatched.append(raw)
    if not unmatched:
        return []
    return [
        CriticFinding(
            rule="numbers_from_results",
            severity=WARN,
            detail=(
                f"These figures do not appear in any result this run returned: "
                f"{', '.join(unmatched[:5])}. They may be correct arithmetic over figures "
                "that do; check them before relying on them."
            ),
        )
    ]


def _refusal_is_not_an_answer(draft: FinalizeIn, evidence: Evidence) -> list[CriticFinding]:
    """A statement the capability check refused cannot come back as a claim.

    Keyed on `answerable`, which the **per-statement** check writes when it
    actually refuses one (`loop.research`), and not on the catalog-wide gap list
    the runner records up front. The difference is the whole rule: almost every
    real schema has some pair that cannot be joined, so blocking on the presence
    of a gap would refuse answers to questions that never went near it — a false
    block, which is this component's version of the false refusal WP8.4 spent
    itself avoiding. Caught by `test_a_four_step_investigation_runs_every_step`,
    which is a fixture whose catalog has an unrelated table in it.
    """
    if draft.answered and evidence.state.capability.get("answerable") is False:
        return [
            CriticFinding(
                rule="capability_respected",
                severity=BLOCK,
                detail=(
                    "The schema cannot join the tables this question needs — the query was "
                    "refused for that reason — so the answer may explain the limitation but "
                    "may not state a result."
                ),
            )
        ]
    return []


def _required_filters(draft: FinalizeIn, evidence: Evidence) -> list[CriticFinding]:
    """The filters a semantic definition demands are present in the SQL (D-033).

    **This is the rule that makes a definition bind.** *Prose informs the model;
    a structured definition binds it* — and this function is the whole of the
    second half. Without it a definition is a paragraph the model may follow, and
    **B-078** is what that looked like: given the right definition, a live model
    wrote it into its statement and then reasoned its way back out two iterations
    later, answering 1,054 where the document said 747, with nothing able to
    object.

    **Two strengths, and the split is what keeps the strong one safe.**

    *Blocks* when the statement does not constrain the column **at all**. A
    metric defined as excluding cancelled orders cannot have been computed by a
    query that never mentions `status`; that is not a judgement about SQL style,
    it is arithmetic, and it is the failure worth stopping a run for.

    *Warns* when the column is constrained but none of the definition's own
    values appear. `status = 'completed'` is that shape and is very likely
    correct, so blocking it would be a **false block** — the failure standing
    note 5 calls this component's characteristic one, and the reason `capability`
    spent a whole WP undoing its own. A warning travels into the answer as a
    limitation, which is exactly the right weight for "this may be a different
    reading of the metric".

    Silent when the draft cites nothing, when no definition matched the question,
    and when a statement does not touch the definition's table — a run that never
    used the metric cannot have misused it.
    """
    if not evidence.definitions or not draft.supported_by:
        return []

    findings: list[CriticFinding] = []
    for definition in evidence.definitions:
        for item in definition.required_filters:
            findings += _one_filter(definition, item, draft, evidence)
    return findings


def _one_filter(
    definition: SemanticDefinition,
    item: RequiredFilter,
    draft: FinalizeIn,
    evidence: Evidence,
) -> list[CriticFinding]:
    """One required filter, against every statement the answer rests on."""
    from dataagent.dal.validator import filtered_columns, literals_in, tables_named

    unconstrained: list[str] = []
    unmatched: list[str] = []
    for execution_id in draft.supported_by:
        statement = evidence.statements.get(execution_id)
        if not statement:
            # A citation that resolves to no statement is already a finding of
            # its own (`_citations_resolve`). Reporting it twice, in two
            # vocabularies, would read as two problems.
            continue
        if item.table.lower() not in {
            name.lower() for name in tables_named(statement, dialect=evidence.dialect)
        }:
            continue
        if item.column.lower() not in filtered_columns(statement, dialect=evidence.dialect):
            unconstrained.append(execution_id)
        elif item.values and not (
            {value.lower() for value in item.values}
            & literals_in(statement, dialect=evidence.dialect)
        ):
            unmatched.append(execution_id)

    if unconstrained:
        return [
            CriticFinding(
                rule="required_filter_missing",
                severity=BLOCK,
                detail=(
                    f"“{definition.name}” is defined here as requiring "
                    f"{item.describe()}, and the query behind this answer does not "
                    f"filter on {item.qualified} at all. The number is for a "
                    "different population than the one the metric names."
                ),
            )
        ]
    if unmatched:
        return [
            CriticFinding(
                rule="required_filter_differs",
                severity=WARN,
                detail=(
                    f"“{definition.name}” is defined here as requiring "
                    f"{item.describe()}. The query filters on {item.qualified} but "
                    "not on those values, so it may be a different reading of the "
                    "metric."
                ),
            )
        ]
    return []


def _clean(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _close(left: float, right: float) -> bool:
    """Approximate match, as 4.5 asks for: prose rounds and results do not."""
    if left == right:
        return True
    scale = max(abs(left), abs(right), 1.0)
    return abs(left - right) / scale < 0.005


def rubric(draft: FinalizeIn, evidence: Evidence, deterministic: tuple[CriticFinding, ...]) -> str:
    """The prompt for stage 2 — architecture 4.5's checklist, fixed.

    Fixed rather than composed per question so two runs of the same draft are
    judged by the same words. The deterministic findings are included as context
    because a model told what arithmetic already found does not spend its
    attention rediscovering it.
    """
    evidence_lines = (
        "\n".join(
            f"- {reference.execution_id} ({reference.row_count} rows): {reference.summary}"
            for reference in evidence.state.executions
            if reference.ok
        )
        or "- (no query returned a result)"
    )
    already = (
        "\n".join(f"- {finding.detail}" for finding in deterministic)
        or "- (the deterministic checks found nothing)"
    )
    thread = history_block(evidence.history)
    return (f"{thread}\n\n" if thread else "") + (
        f"The question was: {evidence.question}\n"
        f"Today's date for this question: {evidence.as_of.isoformat()}\n\n"
        f"Queries run:\n{evidence_lines}\n\n"
        f"Draft answer:\n{draft.answer}\n\n"
        f"Already checked mechanically:\n{already}\n\n"
        "Judge the draft against these five points and nothing else:\n"
        "1. Correlation offered as causation — does it claim something caused "
        "something else when the queries only show they moved together?\n"
        "2. A missing dimension — is there an obvious split (time, place, "
        "segment, channel) that could reverse or explain this conclusion?\n"
        "3. Sample adequacy — are the row counts large enough to carry the "
        "claim being made?\n"
        "4. Contradiction — does any part of the draft disagree with another "
        "part, or with a query result?\n"
        "5. An unsupported assumption — does it assert anything the queries do "
        "not show?\n\n"
        "Answer pass if the draft is defensible from this evidence. It does not "
        "have to be complete or elegant — only supportable. Answer revise if it "
        "overstates or misreads what the queries returned. Answer "
        "insufficient_evidence only if answering properly needs a query nobody "
        "ran, and say which one."
    )


def combine(
    deterministic: tuple[CriticFinding, ...],
    model: CriticOut | None,
) -> CriticVerdict:
    """One verdict from both stages.

    A blocking deterministic finding decides on its own and the model is never
    asked — that is what makes stage 1 "free" in the sense that matters, since
    the call it saves is the expensive part.
    """
    findings = list(deterministic)
    if any(finding.severity == BLOCK for finding in deterministic):
        return CriticVerdict(verdict="revise", findings=tuple(findings), consulted_model=False)

    if model is None:
        return CriticVerdict(verdict="pass", findings=tuple(findings), consulted_model=False)

    findings += [
        CriticFinding(rule="checklist", severity=BLOCK, detail=reason) for reason in model.reasons
    ]
    return CriticVerdict(
        verdict=model.verdict,
        findings=tuple(findings),
        consulted_model=True,
    )


async def statements_for(org_id: uuid.UUID, execution_ids: tuple[str, ...]) -> dict[str, str]:
    """The SQL that actually ran, read back from `query_executions`.

    From the durable row rather than from the research state: the state is the
    agent's own account of what it did, and a check that trusts it could be
    defeated by the same bug it is meant to catch.
    """
    if not execution_ids:
        return {}

    from sqlalchemy import text

    from dataagent.tenancy.session import org_session

    async with org_session(org_id) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id::text AS id, sql_text FROM query_executions "
                    "WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": list(execution_ids)},
            )
        ).all()
    return {row.id: row.sql_text for row in rows}
