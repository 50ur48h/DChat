"""What a metric means here, and the half of it a check can act on (arch 5.4).

**Prose informs the model; a structured definition binds it** (DECISIONS
**D-033**). WP10.2a let the agent read a policy document mid-run; WP10.2b made an
answer that rested on one say that nothing had checked it. This module is the
other half — the kind of definition the critic can enforce — and the split runs
through every object here.

`description` and `expression` are **for the prompt**. They are what make a model
compute the metric correctly in the first place, and they are prose: nothing
parses them and nothing pretends to.

`required_filters` is **for the critic**. Each one names a table, a column, an
operator and its values, which is exactly enough for an AST check to ask whether
a statement honoured it. **B-078** is why the two are separate rather than one
paragraph: given a definition it had only *read*, a live model wrote it into its
SQL and then reasoned its way back out two iterations later, answering 1,054
where the document said 747 — and nothing could object.

**A definition is validated against the catalog when it is written.** A filter
naming a column that does not exist is a definition that would silently never
match, and finding that out during a run — as a critic finding nobody can act on
— is the worst moment to find it. So `validate` runs at the door, and the error
names the column.

**Matching is by name and synonym, against the question.** A metric nobody can
name is a metric nobody gets, and `net_revenue` is not what anyone types. The
match is deliberately narrow — a whole-word occurrence of the name or one of its
synonyms — because the alternative failure is worse than missing: a definition
applied to a question it is not about becomes a critic rule enforcing a filter
the answer never needed, which is a **false block**, and standing note 5 calls
that this component's characteristic failure.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import cast

from sqlalchemy import select

from dataagent.dal.policy import SourcePolicy
from dataagent.db.models import SemanticDefinition
from dataagent.tenancy.session import org_session

__all__ = [
    "FILTER_OPERATORS",
    "Definition",
    "RequiredFilter",
    "create",
    "definitions_for",
    "matching",
    "validate",
]

STATUS_ACTIVE = "active"

#: The operators a filter may use. Closed, and small on purpose: every one of
#: these has an unambiguous reading in a `WHERE` clause, and an operator the
#: critic cannot check is one the product would claim to enforce and not.
FILTER_OPERATORS: tuple[str, ...] = ("in", "not_in", "eq", "ne", "gt", "gte", "lt", "lte")

#: Operators whose meaning is carried by their values. `status not in
#: ('cancelled')` is about the word 'cancelled'; `total_amount > 40` is about the
#: number, and both are values — the distinction that matters to the critic is
#: only whether there are any, which every operator here has.
_NEEDS_VALUES = frozenset(FILTER_OPERATORS)


class DefinitionError(ValueError):
    """A definition that could not be accepted, with the reason in its message.

    A `ValueError` because it is a fact about the input rather than about the
    system: the caller wrote a filter naming a column this database does not
    have, and the answer is to fix the definition.
    """


@dataclass(frozen=True, slots=True)
class RequiredFilter:
    """One predicate a statement must honour to be computing this metric.

    ``table`` and ``column`` are unqualified names as the catalog holds them.
    ``values`` are compared as text: a statement's literals are read out of the
    AST, and `40` and `'40'` are the same thing to a reader of SQL.
    """

    table: str
    column: str
    op: str
    values: tuple[str, ...] = ()

    @property
    def qualified(self) -> str:
        return f"{self.table}.{self.column}"

    def describe(self) -> str:
        """The filter as a person would read it, for a prompt and for a finding."""
        readable = {
            "in": "one of",
            "not_in": "none of",
            "eq": "equal to",
            "ne": "not equal to",
            "gt": "greater than",
            "gte": "at least",
            "lt": "less than",
            "lte": "at most",
        }[self.op]
        return f"{self.qualified} {readable} {', '.join(self.values)}"

    @classmethod
    def of(cls, raw: dict[str, object]) -> RequiredFilter:
        table = str(raw.get("table", "")).strip()
        column = str(raw.get("column", "")).strip()
        op = str(raw.get("op", "")).strip().lower()
        values = raw.get("values", [])
        if not table or not column:
            raise DefinitionError("a required filter must name a table and a column")
        if op not in FILTER_OPERATORS:
            raise DefinitionError(
                f"{op!r} is not a filter operator this layer can check. "
                f"Use one of: {', '.join(FILTER_OPERATORS)}."
            )
        listed: tuple[str, ...] = ()
        if isinstance(values, list):
            listed = tuple(str(value) for value in cast("list[object]", values))
        if op in _NEEDS_VALUES and not listed:
            raise DefinitionError(f"the {op!r} filter on {table}.{column} names no values")
        return cls(table=table, column=column, op=op, values=listed)


@dataclass(frozen=True, slots=True)
class Definition:
    """One metric or dimension, as the prompt and the critic each need it."""

    id: uuid.UUID
    name: str
    kind: str
    description: str
    expression: str | None = None
    required_filters: tuple[RequiredFilter, ...] = ()
    synonyms: tuple[str, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        """Everything this definition answers to, lowercased.

        The bare name with underscores replaced too: nobody types `net_revenue`
        into a question, and a definition that only answers to its own key is one
        the layer will almost never apply.
        """
        spoken = {self.name.lower(), self.name.lower().replace("_", " ")}
        return tuple(sorted(spoken | {synonym.lower() for synonym in self.synonyms}))

    def render(self) -> str:
        """How the definition reaches the prompt (L3).

        The filters are stated **as rules rather than as suggestions**, and they
        say that they are checked. A model told a constraint is enforced complies
        with it more often than one told a preference — and when it does not, the
        critic is what the sentence was promising.
        """
        lines = [f"### {self.name}", self.description.strip()]
        if self.expression:
            lines.append(f"Computed as: {self.expression}")
        if self.required_filters:
            lines.append(
                "This metric is only correct with these filters, and the query you "
                "write is checked against them: "
                + "; ".join(item.describe() for item in self.required_filters)
                + "."
            )
        return "\n".join(lines)


def _word(term: str) -> re.Pattern[str]:
    """A whole-word matcher for a term that may contain spaces.

    Whole-word because substring matching turns `orders` into a match inside
    `reorders`, and a definition applied to a question it is not about is a
    critic rule enforcing a filter the answer never needed — a false block.
    """
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)


def matching(definitions: Sequence[Definition], question: str) -> tuple[Definition, ...]:
    """The definitions this question is about, in the order they were given.

    Narrow on purpose — see the module docstring. A question that names no metric
    gets none, and that is the common case: most questions are about rows rather
    than about a defined measure.
    """
    if not question.strip():
        return ()
    found: list[Definition] = []
    for definition in definitions:
        if any(_word(term).search(question) for term in definition.names):
            found.append(definition)
    return tuple(found)


def validate(definition: Definition, policy: SourcePolicy) -> None:
    """Refuse a definition this database cannot support, naming what is wrong.

    Checked at the door rather than at the point of use, because a filter naming
    a column that does not exist would never match anything — and would surface
    as a critic finding during somebody's run, which is both too late and
    unactionable by the person reading it.
    """
    known = {
        (table.table_name.lower(), column.name.lower())
        for table in policy.catalog.tables
        for column in table.columns
    }
    tables = {table.table_name.lower() for table in policy.catalog.tables}
    for item in definition.required_filters:
        if item.table.lower() not in tables:
            raise DefinitionError(
                f"{definition.name!r} requires a filter on {item.qualified}, and this "
                f"data source has no table called {item.table!r}."
            )
        if (item.table.lower(), item.column.lower()) not in known:
            raise DefinitionError(
                f"{definition.name!r} requires a filter on {item.qualified}, and "
                f"{item.table!r} has no column called {item.column!r}."
            )


async def create(
    *,
    org_id: uuid.UUID,
    data_source_id: uuid.UUID,
    name: str,
    description: str,
    required_filters: Sequence[dict[str, object]] = (),
    expression: str | None = None,
    synonyms: Sequence[str] = (),
    kind: str = "metric",
    status: str = STATUS_ACTIVE,
    actor_user_id: uuid.UUID | None = None,
    policy: SourcePolicy | None = None,
) -> Definition:
    """Write a definition, after checking this database can support it.

    **Validated at the door.** A filter naming a column that does not exist would
    never match anything, and the run that discovered it would report a critic
    finding nobody could act on. `policy` is optional only so a caller that
    already loaded one need not load it twice; skipping the check is not on
    offer.

    The name is lowercased, because "Net Revenue" and "net_revenue" are one
    metric and a catalog of near-duplicates is worse than no catalog.

    Routes and the admin UI are WP10.2d's. This exists now because a table
    nothing can write to is a table nothing exercises — and because the critic
    rule it feeds has to be provable against a real row.
    """
    from dataagent.dal.policy import source_policy

    resolved = policy if policy is not None else await source_policy(org_id, data_source_id)
    definition = Definition(
        id=uuid.uuid4(),
        name=name.strip().lower(),
        kind=kind,
        description=description.strip(),
        expression=expression,
        required_filters=tuple(RequiredFilter.of(dict(item)) for item in required_filters),
        synonyms=tuple(word.strip() for word in synonyms if word.strip()),
    )
    validate(definition, resolved)

    async with org_session(org_id) as session:
        session.add(
            SemanticDefinition(
                id=definition.id,
                org_id=org_id,
                data_source_id=data_source_id,
                name=definition.name,
                kind=definition.kind,
                description=definition.description,
                expression=definition.expression,
                required_filters=[
                    {
                        "table": item.table,
                        "column": item.column,
                        "op": item.op,
                        "values": list(item.values),
                    }
                    for item in definition.required_filters
                ],
                synonyms=list(definition.synonyms),
                status=status,
                created_by=actor_user_id,
            )
        )
        await session.flush()
    return definition


async def definitions_for(org_id: uuid.UUID, data_source_id: uuid.UUID) -> tuple[Definition, ...]:
    """Every **active** definition for one data source, by name.

    Proposed and retired ones are left where they are: a proposal has not been
    blessed by an Admin and must not constrain anything (B-059's rule, since an
    imported definition is a privileged object), and a retired one is kept only
    so that a run which cited it can still explain itself.
    """
    async with org_session(org_id) as session:
        rows = (
            (
                await session.execute(
                    select(SemanticDefinition)
                    .where(
                        SemanticDefinition.data_source_id == data_source_id,
                        SemanticDefinition.status == STATUS_ACTIVE,
                    )
                    .order_by(SemanticDefinition.name)
                )
            )
            .scalars()
            .all()
        )
    return tuple(_definition_of(row) for row in rows)


def _definition_of(row: SemanticDefinition) -> Definition:
    filters: list[RequiredFilter] = []
    for raw in row.required_filters:
        # A row that cannot be read is skipped rather than fatal: the
        # definition's prose is still worth putting in front of the model, and a
        # malformed filter that took the whole metric out of the prompt would
        # turn a bad edit into a silently unguided run.
        with suppress(DefinitionError):
            filters.append(RequiredFilter.of(raw))
    return Definition(
        id=row.id,
        name=row.name,
        kind=row.kind,
        description=row.description,
        expression=row.expression,
        required_filters=tuple(filters),
        synonyms=tuple(str(word) for word in row.synonyms),
    )
