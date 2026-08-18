"""Semantic layer routes (architecture Part 10.2, plan WP10.2d).

**Every verb here is Admin**, the read side included. That is the unusual part of
this module and the part worth defending, because nothing else in the product
locks its *read* side to one role.

An accepted definition **constrains generated SQL**. By D-033 a structured
definition does not merely inform the model, it binds it: `required_filters`
becomes a rule the critic enforces against the AST of every statement, and a
statement that ignores one is blocked. That makes a definition a privileged
object in exactly the sense the DAL means — the same argument that says the LLM
is never a security boundary says a row that rewrites what the platform will
accept is not ordinary content. Writing one is Admin work for the obvious
reason. Reading the list is Admin work for a quieter one: this is the screen
that says which metrics bind and which are still prose, and it is an
administrative view of the platform's own controls rather than a view of the
customer's data. **Fail closed and widen deliberately** — whether a Reader
should see definitions is a real product question and it is filed as B-082
rather than answered here by default.

**Import is the route B-059 exists for.** A mature warehouse arrives with its
metrics already written down, and a product that can only accept a definition
retyped will mostly be given none. Nothing it reads binds anything: every row
arrives `proposed`, `definitions_for` loads active ones alone, and the accept
route is where an Admin turns prose into a constraint by giving it filters.

**Accept and reject are POSTs rather than a PATCH of `status`.** They are not
edits to a field; they are two different decisions with different consequences,
and `accept` takes a body — the filters — that `reject` has no meaning for. A
single status PATCH would also make the interesting one, accepting *with*
filters, look like a routine update.

**PATCH is for an active definition's content, and `DELETE` retires it**
(**B-088**). Until they existed a definition was write-once — no edit, no
un-accept, re-accepting a 404 — and the only way to correct a filter was to
delete the row in psql and import again. The two verbs are separate for the same
reason accept and reject are: changing what a metric requires and taking it out
of force are different decisions, and the second one is not a field. Both are
validated exactly as `accept` is, both are audited, and both append to the
definition's history, because an edit changes what the platform enforces on
generated SQL and *"what did this require when that answer was written"* has to
stay answerable (D-036).

Errors are mapped so that a mistake an Admin can fix says so. A filter naming a
column this database does not have is a **400** carrying the column's name, not
a 500 and not a silent success that fails during somebody's run later — which
is the whole reason `validate` runs at the door.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field

from dataagent.auth.context import RequestContext
from dataagent.auth.guards import require_admin
from dataagent.catalog.browse import NoCatalogError
from dataagent.connectors.base import ConnectorError
from dataagent.dal.errors import PolicyViolation
from dataagent.datasources.service import NotFoundError
from dataagent.semantic import definitions as definitions_service
from dataagent.semantic import proposals as proposals_service
from dataagent.semantic import verified as verified_service
from dataagent.semantic.definitions import (
    FILTER_OPERATORS,
    KEEP,
    Definition,
    DefinitionError,
    Version,
)
from dataagent.semantic.proposals import ColumnMapping, Proposal
from dataagent.semantic.verified import VerifiedQuery, VerifiedQueryError

router = APIRouter(prefix="/v1", tags=["semantic"])

DataSourceId = Annotated[uuid.UUID, Path(description="Data source within this organization")]
DefinitionId = Annotated[uuid.UUID, Path(description="Definition within this organization")]
VerifiedQueryId = Annotated[uuid.UUID, Path(description="Verified query in this organization")]


class RequiredFilterModel(BaseModel):
    """One predicate a statement must honour to be computing this metric.

    The half of a definition a check can act on. `op` is a closed set because an
    operator the critic cannot check is one the product would claim to enforce
    and would not.
    """

    table: str = Field(min_length=1, description="Unqualified, as the catalog holds it")
    column: str = Field(min_length=1)
    op: str = Field(description=" | ".join(FILTER_OPERATORS))
    values: list[str] = Field(
        default_factory=list[str],
        description="Compared as text: 40 and '40' are the same thing to a reader of SQL.",
    )


class DefinitionOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    description: str
    expression: str | None = None
    required_filters: list[RequiredFilterModel] = Field(default_factory=list[RequiredFilterModel])
    synonyms: list[str] = Field(default_factory=list[str])
    binds: bool = Field(
        description=(
            "Whether this definition constrains generated SQL. False means it is "
            "prose: it informs the model and the critic checks nothing (D-033), "
            "and an answer resting on it says so."
        )
    )
    version: int = Field(
        default=1,
        description=(
            "Which state of this definition is in force. Bumped by every edit, "
            "and the key into its history (B-088)."
        ),
    )

    @classmethod
    def of(cls, definition: Definition) -> DefinitionOut:
        return cls(
            id=definition.id,
            name=definition.name,
            kind=definition.kind,
            description=definition.description,
            expression=definition.expression,
            required_filters=[
                RequiredFilterModel(
                    table=item.table, column=item.column, op=item.op, values=list(item.values)
                )
                for item in definition.required_filters
            ],
            synonyms=list(definition.synonyms),
            binds=bool(definition.required_filters),
            version=definition.version,
        )


class ProposalOut(BaseModel):
    """A definition awaiting an Admin, and where it came from.

    `provenance` is on the wire because the review screen's first question is
    *"where did this sentence come from"* — the source table and the snapshot it
    was read at. A proposal whose origin cannot be shown is one nobody can
    responsibly accept.
    """

    id: uuid.UUID
    name: str
    description: str
    expression: str | None = None
    synonyms: list[str] = Field(default_factory=list[str])
    provenance: dict[str, object] = Field(default_factory=dict[str, object])

    @classmethod
    def of(cls, proposal: Proposal) -> ProposalOut:
        return cls(
            id=proposal.id,
            name=proposal.name,
            description=proposal.description,
            expression=proposal.expression,
            synonyms=list(proposal.synonyms),
            provenance=dict(proposal.provenance),
        )


class CreateDefinitionIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=4000)
    expression: str | None = Field(default=None, max_length=4000)
    synonyms: list[str] = Field(
        default_factory=list[str],
        description="What people actually type. `net_revenue` is not one of them.",
    )
    required_filters: list[RequiredFilterModel] = Field(default_factory=list[RequiredFilterModel])
    kind: str = Field(default="metric")


class ImportIn(BaseModel):
    """Which of the customer's tables holds their metrics, and what its columns mean.

    **The mapping is configuration, not a guess.** Nothing divines that a table
    called `meta_metric` is authoritative or that `definition_text` is a
    description. Being wrong here is then a visible mistake in a proposal an
    Admin is about to review, rather than an invisible one in a definition that
    already binds.
    """

    table: str = Field(min_length=1, description="The customer's metric table")
    schema_name: str = Field(default="public", alias="schema")
    name_column: str = Field(min_length=1)
    description_column: str = Field(min_length=1)
    expression_column: str | None = None
    synonyms_column: str | None = Field(
        default=None, description="Comma-separated in the customer's own cell"
    )

    model_config = {"populate_by_name": True}


class AcceptIn(BaseModel):
    """The filters that turn an imported sentence into a constraint.

    Optional, and an empty list is a real answer rather than a missing one: an
    Admin may bless a definition as prose, which puts it in front of the model
    without binding it. What they cannot do is bless it as binding *by accident*,
    which is why the filters are typed here and not inferred from the sentence.
    """

    required_filters: list[RequiredFilterModel] = Field(default_factory=list[RequiredFilterModel])
    synonyms: list[str] | None = Field(
        default=None,
        description=(
            "What people actually call it. Omit to keep what the import found; send a "
            "list to replace it. An imported metric answers only to its key and to its "
            "own table's label until somebody says otherwise, and a metric nobody can "
            "name is a metric nobody gets (B-085)."
        ),
    )


class UpdateDefinitionIn(BaseModel):
    """A correction to an active definition (**B-088**).

    **Every field is optional and omission means "leave it alone."** A partial
    update is the shape this needs: an Admin fixing a filter should not have to
    resend a description they did not touch, because the resend is where the
    description quietly loses a sentence.

    `name` is absent on purpose. It is what a question is matched against and
    what a run's trace recorded, so renaming would silently change which
    questions the definition answers and orphan every citation of it. What people
    call the metric is `synonyms`, which is what the matcher actually reads.
    """

    description: str | None = Field(default=None, min_length=1, max_length=4000)
    expression: str | None = Field(
        default=None,
        max_length=4000,
        description=(
            "Send null to clear the formula. Omitting the field keeps it — the "
            "one place here where null and absent differ."
        ),
    )
    synonyms: list[str] | None = Field(
        default=None, description="A list replaces what is there; omit to keep it."
    )
    required_filters: list[RequiredFilterModel] | None = Field(
        default=None,
        description=(
            "A list replaces what is enforced. An empty list is a real request — "
            "stop enforcing this and keep the prose — and an Admin has to be able "
            "to make it, since the alternative way to undo a wrong filter is psql."
        ),
    )


class VersionOut(BaseModel):
    """One state a definition has been in force in (**B-088**, arch 5.4)."""

    version: int
    change: str = Field(description=" | ".join(("created", "accepted", "updated", "retired")))
    name: str
    description: str
    expression: str | None = None
    required_filters: list[RequiredFilterModel] = Field(default_factory=list[RequiredFilterModel])
    synonyms: list[str] = Field(default_factory=list[str])
    status: str
    changed_by: uuid.UUID | None = None
    changed_at: datetime

    @classmethod
    def of(cls, version: Version) -> VersionOut:
        return cls(
            version=version.version,
            change=version.change,
            name=version.name,
            description=version.description,
            expression=version.expression,
            required_filters=[
                RequiredFilterModel(
                    table=item.table, column=item.column, op=item.op, values=list(item.values)
                )
                for item in version.required_filters
            ],
            synonyms=list(version.synonyms),
            status=version.status,
            changed_by=version.changed_by,
            changed_at=version.changed_at,
        )


class VerifiedQueryOut(BaseModel):
    """One approved question and the statement that answers it (arch 5.4)."""

    id: uuid.UUID
    question: str
    sql: str
    notes: str | None = None

    @classmethod
    def of(cls, example: VerifiedQuery) -> VerifiedQueryOut:
        return cls(id=example.id, question=example.question, sql=example.sql, notes=example.notes)


class CreateVerifiedQueryIn(BaseModel):
    """An example an Admin is approving.

    The statement is validated against this data source's catalog by the same
    validator that guards execution, so an approved example naming a table that
    does not exist is refused here rather than sitting in a prompt teaching the
    model to invent one.
    """

    question: str = Field(min_length=1, max_length=2000)
    sql: str = Field(min_length=1, max_length=20000)
    notes: str | None = Field(
        default=None,
        max_length=2000,
        description="Why this shape is right — an example without its reason teaches copying.",
    )


def _filters(models: list[RequiredFilterModel]) -> list[dict[str, object]]:
    return [
        {"table": item.table, "column": item.column, "op": item.op, "values": list(item.values)}
        for item in models
    ]


def _no_such_source(error: Exception) -> HTTPException:
    """A missing data source and an undiscovered one, told apart.

    ``NotFoundError`` is a 404 because there is nothing there. ``NoCatalogError``
    is a 409 because the source exists and the request was reasonable — it is
    simply not possible to validate a filter against a catalog nobody has built
    yet, and *"refresh the catalog first"* is an instruction rather than a
    dead end.
    """
    if isinstance(error, NoCatalogError):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            f"{error} Refresh this data source's catalog and try again.",
        )
    return HTTPException(status.HTTP_404_NOT_FOUND, "No such data source")


@router.get(
    "/orgs/{org_id}/data-sources/{data_source_id}/verified-queries",
    response_model=list[VerifiedQueryOut],
    summary="Approved question and SQL pairs the planner is shown",
)
async def list_verified_queries(
    context: Annotated[RequestContext, Depends(require_admin)],
    data_source_id: DataSourceId,
) -> list[VerifiedQueryOut]:
    """The active ones, oldest first — the order the matcher breaks ties by."""
    found = await verified_service.verified_for(context.org_id, data_source_id)
    return [VerifiedQueryOut.of(example) for example in found]


@router.post(
    "/orgs/{org_id}/data-sources/{data_source_id}/verified-queries",
    response_model=VerifiedQueryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Approve a question and the SQL that answers it",
)
async def create_verified_query(
    body: CreateVerifiedQueryIn,
    context: Annotated[RequestContext, Depends(require_admin)],
    data_source_id: DataSourceId,
) -> VerifiedQueryOut:
    """**Nothing is executed here.** The statement is judged against the catalog,
    not run: approving an example is not a reason to read a customer's rows.

    A refusal is a 400 carrying the validator's own message, which names the
    identifier at fault because it was written to be repaired from.
    """
    try:
        example = await verified_service.create(
            org_id=context.org_id,
            data_source_id=data_source_id,
            question=body.question,
            sql=body.sql,
            notes=body.notes,
            actor_user_id=context.user_id,
        )
    except VerifiedQueryError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    except (NotFoundError, NoCatalogError) as error:
        raise _no_such_source(error) from error
    return VerifiedQueryOut.of(example)


@router.delete(
    "/orgs/{org_id}/data-sources/{data_source_id}/verified-queries/{verified_query_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Stop showing an approved example",
)
async def retire_verified_query(
    context: Annotated[RequestContext, Depends(require_admin)],
    data_source_id: DataSourceId,
    verified_query_id: VerifiedQueryId,
) -> None:
    """Retired rather than deleted, so a run grounded in it last month is still
    explainable this month."""
    try:
        await verified_service.retire(org_id=context.org_id, verified_query_id=verified_query_id)
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such verified query") from error


@router.get(
    "/orgs/{org_id}/data-sources/{data_source_id}/definitions",
    response_model=list[DefinitionOut],
    summary="Every definition binding this data source",
)
async def list_definitions(
    context: Annotated[RequestContext, Depends(require_admin)],
    data_source_id: DataSourceId,
) -> list[DefinitionOut]:
    """The **active** ones, by name.

    Proposals are not here — they have their own route, because a screen that
    mixes what binds with what is merely suggested is the screen on which
    somebody mistakes the second for the first.
    """
    found = await definitions_service.definitions_for(context.org_id, data_source_id)
    return [DefinitionOut.of(definition) for definition in found]


@router.get(
    "/orgs/{org_id}/data-sources/{data_source_id}/definitions/proposals",
    response_model=list[ProposalOut],
    summary="Imported definitions waiting for an Admin",
)
async def list_proposals(
    context: Annotated[RequestContext, Depends(require_admin)],
    data_source_id: DataSourceId,
) -> list[ProposalOut]:
    """Oldest first, so a review queue reads as a queue."""
    found = await proposals_service.proposals_for(context.org_id, data_source_id)
    return [ProposalOut.of(proposal) for proposal in found]


@router.post(
    "/orgs/{org_id}/data-sources/{data_source_id}/definitions",
    response_model=DefinitionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Write a definition by hand",
)
async def create_definition(
    body: CreateDefinitionIn,
    context: Annotated[RequestContext, Depends(require_admin)],
    data_source_id: DataSourceId,
) -> DefinitionOut:
    """Validated against the catalog before it is written, never after.

    A filter naming a column this database does not have would never match
    anything, and the run that discovered it would report a critic finding
    nobody could act on. The 400 here names the column.
    """
    try:
        definition = await definitions_service.create(
            org_id=context.org_id,
            data_source_id=data_source_id,
            name=body.name,
            description=body.description,
            expression=body.expression,
            synonyms=body.synonyms,
            required_filters=_filters(body.required_filters),
            kind=body.kind,
            actor_user_id=context.user_id,
        )
    except DefinitionError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    except (NotFoundError, NoCatalogError) as error:
        raise _no_such_source(error) from error
    return DefinitionOut.of(definition)


@router.post(
    "/orgs/{org_id}/data-sources/{data_source_id}/definitions/import",
    response_model=list[ProposalOut],
    status_code=status.HTTP_201_CREATED,
    summary="Read a customer's metric table and propose what it says",
)
async def import_definitions(
    body: ImportIn,
    context: Annotated[RequestContext, Depends(require_admin)],
    data_source_id: DataSourceId,
) -> list[ProposalOut]:
    """**Nothing imported here binds anything.**

    Every row arrives `proposed` and waits for the accept route. The read goes
    through the DAL like all customer data — grounded against the catalog,
    capped, masked, and recorded as a `query_executions` row — so an import is a
    customer-data access and is auditable as one.

    **201 even when nothing was created**, and deliberately: an import of a table
    whose every name is already known is a request that succeeded and proposed
    nothing. An empty list says that plainly, and a 4xx would suggest the
    mapping was wrong when it was not.
    """
    try:
        created = await proposals_service.propose_from_table(
            org_id=context.org_id,
            data_source_id=data_source_id,
            table=body.table,
            schema=body.schema_name,
            mapping=ColumnMapping(
                name=body.name_column,
                description=body.description_column,
                expression=body.expression_column,
                synonyms=body.synonyms_column,
            ),
            actor_user_id=context.user_id,
        )
    except ValueError as error:
        # `_identifier` refusing a name that is not a bare identifier. A 400
        # naming what was wrong with it, because the Admin typed it.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    except PolicyViolation as violation:
        # The DAL declined the read — most often a table this catalog does not
        # know. Its message is written to be shown: it names the identifier and
        # never the statement or a value out of anyone's database.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(violation)) from violation
    except (NotFoundError, NoCatalogError) as error:
        raise _no_such_source(error) from error
    except ConnectorError as error:
        # Already sanitized by the connector: names what failed, never an
        # address or a credential.
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from error
    return [ProposalOut.of(proposal) for proposal in created]


@router.post(
    "/orgs/{org_id}/data-sources/{data_source_id}/definitions/{definition_id}/accept",
    response_model=DefinitionOut,
    summary="Bless a proposal, and say what it requires",
)
async def accept_proposal(
    body: AcceptIn,
    context: Annotated[RequestContext, Depends(require_admin)],
    data_source_id: DataSourceId,
    definition_id: DefinitionId,
) -> DefinitionOut:
    """**This is where prose becomes a constraint** (D-033).

    An imported definition arrives as sentences, which inform the model and bind
    nothing. The filters supplied here are what let the critic enforce it, and
    they are validated against the catalog before the row is activated — so one
    naming a column this database does not have is refused now rather than
    surfacing later as a finding nobody can act on.
    """
    try:
        definition = await proposals_service.accept(
            org_id=context.org_id,
            definition_id=definition_id,
            required_filters=_filters(body.required_filters),
            synonyms=body.synonyms,
            actor_user_id=context.user_id,
        )
    except DefinitionError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such proposal") from error
    except (NotFoundError, NoCatalogError) as error:
        raise _no_such_source(error) from error
    return DefinitionOut.of(definition)


@router.patch(
    "/orgs/{org_id}/data-sources/{data_source_id}/definitions/{definition_id}",
    response_model=DefinitionOut,
    summary="Correct an active definition",
)
async def update_definition(
    body: UpdateDefinitionIn,
    context: Annotated[RequestContext, Depends(require_admin)],
    data_source_id: DataSourceId,
    definition_id: DefinitionId,
) -> DefinitionOut:
    """**B-088.** The route that makes an Admin's decision revisable.

    Validated exactly as `accept` is — the same catalog check, the same 400
    naming the column — because an edit changes what the platform enforces on
    generated SQL and there is no reason a correction should be trusted more
    than the original. A refused edit changes nothing: the definition is left as
    it was rather than half-applied.

    **Omitted is not empty**, and the distinction is load-bearing. A field left
    out keeps its value; a list replaces one. `expression: null` clears the
    formula, which is the one place where null and absent differ, and it differs
    because a metric with no formula is a real thing to say.

    Only an **active** definition is editable. A proposal is accepted, not
    edited, and the accept route already takes the filters and synonyms; a
    retired one is history and editing history is the thing this design refuses.
    Both are a 404 for the same reason: there is no active definition there.
    """
    sent = body.model_fields_set
    try:
        definition = await definitions_service.update(
            org_id=context.org_id,
            definition_id=definition_id,
            description=body.description,
            # The sentinel, so "clear the formula" and "do not touch it" are two
            # requests rather than one. Every other field reads absence off
            # `None` because none of them has a meaningful null.
            expression=body.expression if "expression" in sent else KEEP,
            synonyms=body.synonyms,
            required_filters=(
                None if body.required_filters is None else _filters(body.required_filters)
            ),
            actor_user_id=context.user_id,
        )
    except DefinitionError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such definition") from error
    except (NotFoundError, NoCatalogError) as error:
        raise _no_such_source(error) from error
    return DefinitionOut.of(definition)


@router.delete(
    "/orgs/{org_id}/data-sources/{data_source_id}/definitions/{definition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Take a definition out of force",
)
async def retire_definition(
    context: Annotated[RequestContext, Depends(require_admin)],
    data_source_id: DataSourceId,
    definition_id: DefinitionId,
) -> None:
    """Retired rather than deleted, like everything else in this layer.

    The metric stops binding and stops reaching the prompt immediately; what it
    said is kept, so an answer checked against it last month is still
    explainable this month. Retiring it twice is a 404 — it is no longer in
    force, and a second call must not read as success.
    """
    try:
        await definitions_service.retire(
            org_id=context.org_id, definition_id=definition_id, actor_user_id=context.user_id
        )
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such definition") from error


@router.get(
    "/orgs/{org_id}/data-sources/{data_source_id}/definitions/{definition_id}/versions",
    response_model=list[VersionOut],
    summary="Everything this definition has said",
)
async def list_definition_versions(
    context: Annotated[RequestContext, Depends(require_admin)],
    data_source_id: DataSourceId,
    definition_id: DefinitionId,
) -> list[VersionOut]:
    """Oldest first, because it reads as a life rather than as a feed.

    An empty list is a real answer rather than a missing one: a definition
    written before revision 0022 has no recorded history, and saying so plainly
    beats implying one exists.
    """
    found = await definitions_service.versions_for(context.org_id, definition_id)
    return [VersionOut.of(version) for version in found]


@router.post(
    "/orgs/{org_id}/data-sources/{data_source_id}/definitions/{definition_id}/reject",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Turn a proposal down",
)
async def reject_proposal(
    context: Annotated[RequestContext, Depends(require_admin)],
    data_source_id: DataSourceId,
    definition_id: DefinitionId,
) -> None:
    """Retired rather than deleted, so *"we looked at this and said no"* is
    answerable — and so a second import of the same table does not silently
    re-propose what an Admin has already turned down."""
    try:
        await proposals_service.reject(
            org_id=context.org_id, definition_id=definition_id, actor_user_id=context.user_id
        )
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such proposal") from error
