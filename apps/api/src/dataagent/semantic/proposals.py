"""Importing the definitions a database already carries (**B-059**, WP10.2d).

The F&B trial on 2026-08-16 is the whole argument. That warehouse arrived with
**18 metrics**, each with a definition and the tables it requires, plus an
assumptions table, a capability matrix and a data-quality gate table whose open
questions name — in English — the exact trap the agent then fell into. All of it
sat in the catalog as ordinary tables, indistinguishable from facts, and the
agent aggregated straight past it: asked how many units of the top-selling set
were sold, it answered **0**, with correct SQL and business nonsense.

A mature warehouse tends to arrive this way, and **a product that can only accept
a definition retyped will mostly be given none** — nobody re-keys eighteen
metrics they already wrote down. So the layer imports.

Four properties, and each is a refusal to take a shortcut.

**Nothing is imported silently.** An imported definition constrains generated
SQL, which makes it a privileged object rather than data a crawler may trust —
the same rule that says the LLM is never a security boundary says a customer's
own metadata table is not one either. Every row arrives `proposed`, and only an
Admin's acceptance makes it `active`. `definitions_for` loads active ones alone,
so a proposal binds nothing while it waits.

**The rows are read through the DAL, like all customer data.** No connector is
opened here. The statement is validated against the catalog, the row cap applies,
sensitive columns come back masked, and the read leaves a `query_executions` row
— an import is a customer-data access and is recorded as one.

**The mapping is configuration, not a guess.** Nothing here divines that a table
called `meta_metric` is authoritative or that `definition_text` is a description.
An Admin says which table and which columns; being wrong about that is then a
visible mistake in a proposal they are about to review, rather than an invisible
one in a definition that already binds.

**Provenance is kept.** A definition records the source, the table and the
snapshot it came from, so that when the customer's own table moves on, the drift
is visible rather than silently stale.

One consequence is worth stating plainly rather than discovering later: an
imported definition arrives as **prose**, because a metric table holds sentences
and not machine-readable filters. By **D-033** that means it *informs* and does
not *bind*, and an answer resting on it still carries the limitation saying so.
Adding `required_filters` is what converts it, and `accept` is where an Admin
does that — the moment prose becomes a constraint.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from sqlalchemy import select

from dataagent.db.models import SemanticDefinition
from dataagent.semantic.definitions import Definition, RequiredFilter, validate
from dataagent.tenancy.session import org_session

__all__ = [
    "ColumnMapping",
    "Proposal",
    "accept",
    "proposals_for",
    "propose_from_table",
    "reject",
]

STATUS_PROPOSED = "proposed"
STATUS_ACTIVE = "active"
STATUS_RETIRED = "retired"

#: How many rows one import may read. A metric table is tens of rows; anything
#: past this is not a metric table, and importing a thousand "definitions" would
#: be a way to fill an organization's semantic layer with somebody's fact table.
MAX_ROWS = 200

#: A bare SQL identifier. Everything this module interpolates into a statement —
#: schema, table, column — must match, and anything that does not is refused
#: rather than quoted or escaped.
#:
#: **The DAL is not the only line here, deliberately.** Every statement below
#: goes through `dal.run`, which grounds each name against the catalog and
#: refuses anything it cannot resolve — so an injected fragment would be caught
#: there. But building SQL by concatenation and relying on a downstream check is
#: how the check eventually gets moved; 5.10 asks for two independent layers and
#: this is the cheap one. It is also a better error: an Admin who mistypes a
#: column name is told which one, here, instead of receiving a policy violation
#: about a statement they did not write.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class ColumnMapping:
    """Which of the customer's columns mean what.

    Per source, supplied by the Admin doing the import. ``name`` and
    ``description`` are required because a definition without either is not one;
    the rest are optional because most metric tables do not have them.
    """

    name: str
    description: str
    expression: str | None = None
    synonyms: str | None = None

    def columns(self) -> tuple[str, ...]:
        """The columns to select, deduplicated and in order.

        Two mapped fields may legitimately read the same column, and `SELECT a,
        a` is a statement the validator would have to think about for no reason.
        """
        seen: dict[str, None] = {}
        for column in (self.name, self.description, self.expression, self.synonyms):
            if column:
                seen.setdefault(column, None)
        return tuple(seen)


@dataclass(frozen=True, slots=True)
class Proposal:
    """A definition awaiting an Admin, and where it came from."""

    id: uuid.UUID
    name: str
    description: str
    expression: str | None
    synonyms: tuple[str, ...]
    provenance: dict[str, object]


async def propose_from_table(
    *,
    org_id: uuid.UUID,
    data_source_id: uuid.UUID,
    table: str,
    mapping: ColumnMapping,
    actor_user_id: uuid.UUID | None = None,
    schema: str = "public",
) -> tuple[Proposal, ...]:
    """Read a customer's metric table and write what it says as proposals.

    Returns what was created. Nothing binds until an Admin accepts it, and a name
    this source already knows is **skipped rather than overwritten** — an import
    must not be able to silently redefine a metric somebody blessed, nor to
    re-propose one they have already turned down.
    """
    from dataagent.dal import service as dal

    qualified = f"{_identifier(schema, 'schema')}.{_identifier(table, 'table')}"
    columns = ", ".join(_identifier(name, "column") for name in mapping.columns())
    # Through the DAL, so the catalog grounds it, the cap applies and the read is
    # recorded. A hand-built connector call here would be the one path around the
    # boundary Part 7 exists to keep singular.
    execution = await dal.run(
        org_id=org_id,
        data_source_id=data_source_id,
        # Every fragment here has been through `_IDENTIFIER`, so nothing a
        # caller supplied can be anything but a bare name — and `dal.run`
        # grounds those names against the catalog regardless.
        sql=f"SELECT {columns} FROM {qualified}",  # noqa: S608
        actor_user_id=actor_user_id,
        max_rows=MAX_ROWS,
    )

    position = {name.lower(): index for index, name in enumerate(execution.frame.columns)}
    snapshot_id = _snapshot_of(execution)

    async with org_session(org_id) as session:
        taken = {
            name.lower()
            for name in (
                await session.execute(
                    select(SemanticDefinition.name).where(
                        SemanticDefinition.data_source_id == data_source_id
                    )
                )
            ).scalars()
        }

        created: list[Proposal] = []
        for row in execution.frame.rows:
            name = _cell(row, position, mapping.name)
            description = _cell(row, position, mapping.description)
            if not name.strip() or not description.strip():
                # A row missing either is not a definition. Skipped quietly: a
                # metric table with a blank line in it is ordinary, and failing
                # a whole import over one would make the feature unusable on the
                # databases it exists for.
                continue
            key = name.strip().lower()
            if key in taken:
                continue
            taken.add(key)

            provenance: dict[str, object] = {
                "kind": "import",
                "table": qualified,
                "snapshot_id": str(snapshot_id) if snapshot_id else None,
                "columns": {
                    "name": mapping.name,
                    "description": mapping.description,
                    "expression": mapping.expression,
                },
            }
            proposal = Proposal(
                id=uuid.uuid4(),
                name=key,
                description=description.strip(),
                expression=_cell(row, position, mapping.expression).strip() or None,
                synonyms=_synonyms(_cell(row, position, mapping.synonyms)),
                provenance=provenance,
            )
            session.add(
                SemanticDefinition(
                    id=proposal.id,
                    org_id=org_id,
                    data_source_id=data_source_id,
                    name=proposal.name,
                    kind="metric",
                    description=proposal.description,
                    expression=proposal.expression,
                    required_filters=[],
                    synonyms=list(proposal.synonyms),
                    provenance=provenance,
                    status=STATUS_PROPOSED,
                    created_by=actor_user_id,
                )
            )
            created.append(proposal)
        await session.flush()
    return tuple(created)


async def proposals_for(org_id: uuid.UUID, data_source_id: uuid.UUID) -> tuple[Proposal, ...]:
    """Everything waiting for an Admin, oldest first."""
    async with org_session(org_id) as session:
        rows = (
            (
                await session.execute(
                    select(SemanticDefinition)
                    .where(
                        SemanticDefinition.data_source_id == data_source_id,
                        SemanticDefinition.status == STATUS_PROPOSED,
                    )
                    .order_by(SemanticDefinition.created_at)
                )
            )
            .scalars()
            .all()
        )
    return tuple(
        Proposal(
            id=row.id,
            name=row.name,
            description=row.description,
            expression=row.expression,
            synonyms=tuple(str(word) for word in row.synonyms),
            provenance=dict(row.provenance or {}),
        )
        for row in rows
    )


async def accept(
    *,
    org_id: uuid.UUID,
    definition_id: uuid.UUID,
    required_filters: Sequence[Mapping[str, object]] = (),
    synonyms: Sequence[str] | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> Definition:
    """Bless a proposal into a definition, optionally giving it filters.

    **This is where prose becomes a constraint** (D-033). An imported definition
    arrives as sentences, which inform the model and bind nothing; adding
    `required_filters` here is what lets the critic enforce it — and it is an
    Admin's act, deliberately, because a filter that binds generated SQL is a
    privileged thing to write.

    Filters are validated against the catalog **before** the row is activated, so
    one naming a column this database does not have is refused now rather than
    surfacing later as a critic finding nobody can act on.

    **`synonyms` is the other half of making it real** (B-085). A definition is
    matched to a question by name and synonym, and an imported one answers only
    to its key and to the label its own table carried. Nobody asks a question in
    those words: the F&B warehouse's own golden question for `prep_quantity` is
    *"how much should I prepare of each item tomorrow?"*, which reaches neither
    the key nor the label. An import that no question can reach binds nothing, so
    acceptance is where an Admin says what people actually call it. `None` keeps
    what the import found; a list replaces it, because correcting a bad label has
    to be possible and appending forever is not a correction.
    """
    from dataagent.dal.policy import source_policy

    async with org_session(org_id) as session:
        row = await session.get(SemanticDefinition, definition_id)
        if row is None or row.status != STATUS_PROPOSED:
            raise LookupError("No such proposal")
        parsed = tuple(RequiredFilter.of(dict(item)) for item in required_filters)
        definition = Definition(
            id=row.id,
            name=row.name,
            kind=row.kind,
            description=row.description,
            expression=row.expression,
            required_filters=parsed,
            synonyms=tuple(str(word) for word in row.synonyms),
        )
        if synonyms is not None:
            words = tuple(word.strip() for word in synonyms if word.strip())
            definition = replace(definition, synonyms=words)
        if parsed:
            validate(definition, await source_policy(org_id, row.data_source_id))
        row.required_filters = [
            {
                "table": item.table,
                "column": item.column,
                "op": item.op,
                "values": list(item.values),
            }
            for item in parsed
        ]
        row.synonyms = list(definition.synonyms)
        row.status = STATUS_ACTIVE
        # Who blessed it, over who imported it: acceptance is the act that made
        # this bind anything, and it is the one worth being able to ask about.
        row.created_by = actor_user_id or row.created_by
        await session.flush()
    return definition


async def reject(*, org_id: uuid.UUID, definition_id: uuid.UUID) -> None:
    """Retire a proposal rather than deleting it.

    Kept, so that *"we looked at this and said no"* is answerable — and so a
    second import of the same table does not silently re-propose what an Admin
    has already turned down.
    """
    async with org_session(org_id) as session:
        row = await session.get(SemanticDefinition, definition_id)
        if row is None or row.status != STATUS_PROPOSED:
            raise LookupError("No such proposal")
        row.status = STATUS_RETIRED
        await session.flush()


def _identifier(name: str, kind: str) -> str:
    """A bare SQL identifier, or a refusal naming what was wrong with it."""
    if not _IDENTIFIER.match(name or ""):
        raise ValueError(
            f"{name!r} is not a valid {kind} name. An import names plain tables and "
            "columns; quoting, dots and expressions are not accepted here."
        )
    return name


def _cell(row: Sequence[object], position: Mapping[str, int], column: str | None) -> str:
    if not column:
        return ""
    index = position.get(column.lower())
    if index is None or index >= len(row):
        return ""
    value = row[index]
    return "" if value is None else str(value)


def _synonyms(raw: str) -> tuple[str, ...]:
    """A comma-separated cell, as a tuple. Empty when the column was not mapped."""
    return tuple(word.strip() for word in raw.split(",") if word.strip())


def _snapshot_of(execution: object) -> uuid.UUID | None:
    """The catalog snapshot the read was grounded against, when it is knowable.

    Best effort on purpose: provenance missing a snapshot is still worth keeping
    — it names the source and the table — and an import that failed because it
    could not label itself would be a poor trade.
    """
    validated = getattr(execution, "validated", None)
    found = getattr(validated, "snapshot_id", None)
    return found if isinstance(found, uuid.UUID) else None
