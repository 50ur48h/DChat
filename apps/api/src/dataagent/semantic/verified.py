"""Admin-approved question→SQL pairs, shown to the planner (arch 5.4).

A semantic definition says what a word means. A **verified query** shows what a
good answer to a question looks like *in this database* — which join, which
grain, which date column, which of the four plausible tables people actually
use. Architecture 5.4 calls it the highest-leverage accuracy feature per dollar,
and the reason is that a worked example carries judgement that no amount of
schema does: nothing in a catalog says that `orders.placed_at` is the date the
business means and `orders.created_at` is not.

**It informs; it does not bind.** This is the same seam D-033 draws through the
whole layer, and it is why this is not a column on `SemanticDefinition`. A
definition's `required_filters` are compared against the AST and a statement that
ignores one is blocked. An example is shown, and **nothing checks that the model
followed it** — because a question that merely resembles the example is not the
example's question, and demanding the same SQL would be a false block on a
correct answer. Standing note 5 calls that this component's characteristic
failure, and it applies here before a single rule has been written.

**Validated by the validator that guards execution.** `create` runs the approved
statement through `dal.validator.validate` against this data source's own
catalog, so an Admin cannot bless something the platform would refuse to run. It
matters more for an example than for an ordinary query: an approved statement
naming a table that does not exist is not merely broken, it is a worked
demonstration of hallucination sitting in the prompt, teaching the exact habit
the catalog grounding exists to prevent. Nothing is executed — the statement is
judged, not run, and no customer row is read to save an example.

**Matching is lexical, deterministic and free.** Content words shared between the
new question and the stored one, best few first, and nothing at all when nothing
overlaps. No embedding, so no spend and no dependency on a provider being
reachable — and a miss costs the run an example it never had, while a wrong
example is actively misleading. When in doubt this returns fewer.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select

from dataagent.db.models import VerifiedQuery as VerifiedQueryRow
from dataagent.tenancy.session import org_session

__all__ = [
    "MAX_EXAMPLES",
    "VerifiedQuery",
    "create",
    "matching",
    "retire",
    "verified_for",
]

STATUS_ACTIVE = "active"
STATUS_RETIRED = "retired"

#: How many examples one prompt may carry. Three, because the point is to show a
#: shape rather than to supply a library: past a handful the examples crowd out
#: the table cards, and a planner reading six near-misses is being invited to
#: pick the closest rather than to answer the question it was asked.
MAX_EXAMPLES = 3

#: Words that appear in every question and so distinguish none of them. Matching
#: on them would make every example match everything, which is the failure that
#: looks most like success — a prompt full of confidently irrelevant SQL.
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "from",
        "by",
        "with",
        "without",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "do",
        "does",
        "did",
        "what",
        "which",
        "who",
        "whom",
        "whose",
        "when",
        "where",
        "why",
        "how",
        "many",
        "much",
        "me",
        "my",
        "our",
        "we",
        "us",
        "you",
        "your",
        "show",
        "tell",
        "give",
        "list",
        "find",
        "get",
        "me",
        "please",
        "about",
        "into",
        "over",
        "under",
        "per",
        "each",
        "all",
        "any",
        "some",
        "no",
        "not",
    ]
)

_WORD = re.compile(r"[a-z0-9_]+")


class VerifiedQueryError(ValueError):
    """An example that could not be accepted, with the reason in its message.

    A `ValueError` because it is a fact about the input: the statement names a
    table this database does not have, and the answer is to fix the statement.
    """


@dataclass(frozen=True, slots=True)
class VerifiedQuery:
    """One approved question and the statement that answers it."""

    id: uuid.UUID
    question: str
    sql: str
    notes: str | None = None

    def render(self) -> str:
        """How the example reaches the prompt (L3).

        The question, the reason and then the SQL — reason before statement on
        purpose, because an example read without its judgement teaches copying.
        """
        lines = [f"Question: {self.question}"]
        if self.notes:
            lines.append(f"Why this shape: {self.notes}")
        lines.append(f"SQL:\n{self.sql.strip()}")
        return "\n".join(lines)


def _terms(text: str) -> frozenset[str]:
    """The content words of a question, lowercased."""
    return frozenset(word for word in _WORD.findall(text.lower()) if word not in _STOPWORDS)


def matching(examples: Sequence[VerifiedQuery], question: str) -> tuple[VerifiedQuery, ...]:
    """The examples worth showing for this question, best first.

    Scored by how much of the stored question the new one shares, as a fraction
    of the stored question's own content words — so a short, sharp example is not
    beaten by a long one that happens to contain more words. Ties keep the given
    order, which is the caller's (oldest first), so the ranking is stable and a
    prompt does not change because two rows scored alike.

    **An example is never shown on a single common word.** One shared term is a
    coincidence at this vocabulary size, and a coincidence rendered as *"here is
    how we answer that"* is worse than silence.
    """
    asked = _terms(question)
    if not asked:
        return ()

    scored: list[tuple[float, int, VerifiedQuery]] = []
    for position, example in enumerate(examples):
        stored = _terms(example.question)
        if not stored:
            continue
        shared = asked & stored
        if len(shared) < 2:
            continue
        scored.append((len(shared) / len(stored), position, example))

    # Descending by score, ascending by original position: `-score` rather than
    # `reverse=True` so the position stays a tiebreak instead of being reversed
    # along with it.
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    return tuple(example for _, _, example in scored[:MAX_EXAMPLES])


async def create(
    *,
    org_id: uuid.UUID,
    data_source_id: uuid.UUID,
    question: str,
    sql: str,
    notes: str | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> VerifiedQuery:
    """Approve one example, after checking this platform would run it.

    Raises ``VerifiedQueryError`` when the validator refuses the statement, with
    the validator's own message — which names the identifier at fault, because
    it was written to be repaired from.

    The statement is **judged, not executed**. Approving an example is not a
    reason to read a customer's rows, and a validator that has already decided
    the statement is answerable against the catalog has established the only
    thing this needs to know.
    """
    from dataagent.dal.errors import PolicyViolation
    from dataagent.dal.policy import source_policy
    from dataagent.dal.validator import validate

    statement = sql.strip()
    asked = question.strip()
    if not statement or not asked:
        raise VerifiedQueryError("A verified query needs both a question and a statement.")

    policy = await source_policy(org_id, data_source_id)
    try:
        validate(statement, source=policy)
    except PolicyViolation as violation:
        raise VerifiedQueryError(
            f"That statement is not one this platform would run, so it cannot be an "
            f"approved example: {violation}"
        ) from violation

    example = VerifiedQuery(id=uuid.uuid4(), question=asked, sql=statement, notes=notes)
    async with org_session(org_id) as session:
        session.add(
            VerifiedQueryRow(
                id=example.id,
                org_id=org_id,
                data_source_id=data_source_id,
                question=example.question,
                sql=example.sql,
                notes=example.notes,
                status=STATUS_ACTIVE,
                created_by=actor_user_id,
            )
        )
        await session.flush()
    return example


async def verified_for(org_id: uuid.UUID, data_source_id: uuid.UUID) -> tuple[VerifiedQuery, ...]:
    """Every **active** example for one data source, oldest first.

    Oldest first because `matching` uses the given order as its tiebreak, and an
    order that changed with each write would change a prompt without anybody
    editing anything.
    """
    async with org_session(org_id) as session:
        rows = (
            (
                await session.execute(
                    select(VerifiedQueryRow)
                    .where(
                        VerifiedQueryRow.data_source_id == data_source_id,
                        VerifiedQueryRow.status == STATUS_ACTIVE,
                    )
                    .order_by(VerifiedQueryRow.created_at)
                )
            )
            .scalars()
            .all()
        )
    return tuple(
        VerifiedQuery(id=row.id, question=row.question, sql=row.sql, notes=row.notes)
        for row in rows
    )


async def retire(*, org_id: uuid.UUID, verified_query_id: uuid.UUID) -> None:
    """Stop showing an example, without forgetting it existed.

    Kept, so a run that was grounded in it last month can still be explained —
    the same reason D-016 keeps an audit row past its subject.
    """
    async with org_session(org_id) as session:
        row = await session.get(VerifiedQueryRow, verified_query_id)
        if row is None or row.status != STATUS_ACTIVE:
            raise LookupError("No such verified query")
        row.status = STATUS_RETIRED
        await session.flush()
