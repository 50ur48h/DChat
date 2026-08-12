"""What a bounded sample of each column looks like (architecture Part 5.2).

The budget is not a safety net around this feature; it *is* the feature. A
profiler without one is a tool that reads a customer's production database until
something gives, and the first time it matters will be on the largest table
somebody owns. So three limits apply at once and none of them is optional:

* **rows** — at most ``max_rows`` per table, in a single ``LIMIT``ed statement;
* **time per query** — the connector's own ``ExecLimits`` timeout, which is a
  server-side ``statement_timeout`` on Postgres and the driver's query timeout on
  SQL Server;
* **time per source** — a wall clock checked before each table, so a schema of
  two thousand tables stops rather than running all night.

Stopping early is a normal outcome and is recorded as ``partial``, not as an
error. Half a catalog with honest provenance is worth more than a complete one
that took a production database down to get.

Two properties of the sample are stated rather than hidden. It is the *first*
``max_rows`` rows, not a random sample — ordering would mean sorting the table —
so a column that is sorted by date has a profile of its oldest rows. And every
statistic is computed from that sample in Python, so "distinct" means distinct
*within the sample*: for a column with more distinct values than rows sampled it
is a floor, not an estimate.

Everything sensitive is masked before it is written. That is in ``classify``,
and it happens here, on the way in.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dataagent.catalog.classify import Verdict, classify_column, mask_value
from dataagent.connectors import introspection
from dataagent.connectors.base import (
    Caps,
    Connector,
    ConnectorError,
    ExecLimits,
    ResultFrame,
    ValidatedQuery,
)
from dataagent.datasources import service as datasources
from dataagent.db.models import CatalogColumn, CatalogSnapshot, CatalogTable, ColumnPolicy
from dataagent.orgs.service import audit
from dataagent.tenancy.session import org_session

__all__ = ["Budget", "ProfileOutcome", "profile"]

STATUS_NONE = "none"
STATUS_PARTIAL = "partial"
STATUS_COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class Budget:
    """Every limit in one object, so "what does profiling cost" has an answer.

    The defaults are deliberately modest. Architecture Part 5.2 allows five
    minutes per source and admin tuning; the point of starting low is that the
    first time this runs against a real database, nobody has tuned anything.
    """

    #: Rows read per table. 5,000 is enough for a null fraction and a top-k to
    #: mean something, and small enough to be uninteresting to any server.
    max_rows: int = 5_000
    #: Passed to the connector as ExecLimits, so the *engine* enforces it.
    per_query_timeout_seconds: float = 10.0
    #: Checked before each table. Architecture's default.
    wall_clock_seconds: float = 300.0
    #: Top-k is only meaningful for a category, and only safe for a small one.
    top_k_max_distinct: int = 50
    max_top_values: int = 20


@dataclass(frozen=True, slots=True)
class ProfileOutcome:
    status: str
    detail: str
    tables_profiled: int = 0
    columns_profiled: int = 0
    sensitive_columns: int = 0
    tables_skipped: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ColumnProfile:
    """One column's statistics, already masked where they had to be."""

    null_frac: float
    distinct: int
    min_val: str | None
    max_val: str | None
    top_values: list[dict[str, object]] | None
    verdict: Verdict
    sample_rows: int


def _render(value: object) -> str:
    """A value as text, for a min/max or a top-k entry.

    Bytes are never rendered: a ``bytea`` column's contents are not something to
    put in a catalog, and their length says everything a profile needs.
    """
    if isinstance(value, bytes | bytearray):
        return f"<{len(value)} bytes>"
    if isinstance(value, memoryview):
        return f"<{value.nbytes} bytes>"
    return str(value)


def _comparable(values: Sequence[object]) -> bool:
    """Can these be ordered without lying about the answer?

    Mixed types compare by accident in Python or not at all, and min/max of a
    column is only meaningful when every value is the same kind of thing.
    """
    if not values:
        return False
    first = type(values[0])
    if isinstance(values[0], str | bytes | bytearray | memoryview):
        return False  # pyright: ignore[reportUnknownArgumentType]
    return all(isinstance(value, first) for value in values)


def profile_column(
    *,
    name: str,
    data_type: str,
    is_pk: bool,
    values: Sequence[object],
    sampled: int,
    budget: Budget,
) -> ColumnProfile:
    """Statistics for one column of the sample, masked as they are built."""
    present = [value for value in values if value is not None]
    null_frac = 0.0 if sampled == 0 else (sampled - len(present)) / sampled

    rendered = [_render(value) for value in present]
    distinct = len(set(rendered))

    verdict = classify_column(
        name=name,
        data_type=data_type,
        is_pk=is_pk,
        values=rendered,
        distinct=distinct,
        sampled=sampled,
    )
    sensitive = verdict.sensitivity != "none"

    minimum: str | None = None
    maximum: str | None = None
    if _comparable(present):
        # Ordering values of one unknown-but-uniform type is exactly what the
        # `_comparable` guard above established is safe; the type system cannot
        # carry that fact, so it is asserted here and nowhere else.
        ordered = sorted(cast("list[Any]", present))
        minimum, maximum = _render(ordered[0]), _render(ordered[-1])
    elif rendered:
        # Strings still have a useful range, and it is not a lie as long as it
        # is a string comparison.
        minimum, maximum = min(rendered), max(rendered)

    if sensitive:
        # The extremes of an email column are two email addresses. Masked with
        # everything else, on the way in.
        minimum = None if minimum is None else mask_value(minimum, verdict.kind)
        maximum = None if maximum is None else mask_value(maximum, verdict.kind)

    top_values: list[dict[str, object]] | None = None
    if 0 < distinct <= budget.top_k_max_distinct:
        counts: dict[str, int] = {}
        for value in rendered:
            counts[value] = counts.get(value, 0) + 1
        top = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        top_values = [
            {"value": mask_value(value, verdict.kind) if sensitive else value, "count": count}
            for value, count in top[: budget.max_top_values]
        ]

    return ColumnProfile(
        null_frac=null_frac,
        distinct=distinct,
        min_val=minimum,
        max_val=maximum,
        top_values=top_values,
        verdict=verdict,
        sample_rows=sampled,
    )


def _sample_query(
    caps: Caps, schema: str, table: str, columns: Sequence[str], limit: int
) -> ValidatedQuery:
    if caps.dialect == introspection.TSQL:
        return introspection.tsql_sample(schema, table, columns, limit)
    return introspection.pg_sample(schema, table, columns, limit)


async def profile(
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    data_source_id: uuid.UUID,
    budget: Budget | None = None,
) -> ProfileOutcome:
    """Profile the active snapshot of one data source.

    Never raises for a database's sake: a source that cannot be read, or has no
    catalog yet, is an answer rather than an exception.
    """
    limits = budget if budget is not None else Budget()
    view = await datasources.get_data_source(org_id, data_source_id)

    async with org_session(org_id) as session:
        snapshot = await _active_snapshot(session, data_source_id)
        if snapshot is None:
            return ProfileOutcome(
                status=STATUS_NONE,
                detail="This data source has no catalog yet. Refresh it before profiling.",
            )
        targets = await _targets(session, snapshot.id)
        snapshot_id = snapshot.id

    if not view.readonly_verified:
        return ProfileOutcome(
            status=STATUS_NONE,
            detail=(
                "This data source has not been proven read-only. Profiling reads "
                "rows, so it waits until the credentials have been checked."
            ),
        )

    try:
        connector = await datasources.connector_for_view(view)
    except Exception as error:
        return ProfileOutcome(status=STATUS_NONE, detail=str(error))

    try:
        results, skipped, errors = await _sample_everything(connector, targets, limits)
    finally:
        await connector.aclose()

    return await _store(
        org_id=org_id,
        actor_user_id=actor_user_id,
        data_source_id=data_source_id,
        snapshot_id=snapshot_id,
        results=results,
        skipped=skipped,
        errors=errors,
    )


@dataclass(frozen=True, slots=True)
class _Target:
    """One table to profile, and the columns it had when it was catalogued."""

    table_id: uuid.UUID
    schema_name: str
    table_name: str
    columns: tuple[tuple[uuid.UUID, str, str, bool], ...]


async def _active_snapshot(
    session: AsyncSession, data_source_id: uuid.UUID
) -> CatalogSnapshot | None:
    return (
        (
            await session.execute(
                select(CatalogSnapshot).where(
                    CatalogSnapshot.data_source_id == data_source_id,
                    CatalogSnapshot.status == "active",
                )
            )
        )
        .scalars()
        .one_or_none()
    )


async def _targets(session: AsyncSession, snapshot_id: uuid.UUID) -> tuple[_Target, ...]:
    tables = (
        (
            await session.execute(
                select(CatalogTable)
                .where(CatalogTable.snapshot_id == snapshot_id)
                .order_by(CatalogTable.schema_name, CatalogTable.table_name)
            )
        )
        .scalars()
        .all()
    )
    columns = (
        (
            await session.execute(
                select(CatalogColumn)
                .join(CatalogTable, CatalogTable.id == CatalogColumn.table_id)
                .where(CatalogTable.snapshot_id == snapshot_id)
                .order_by(CatalogColumn.ordinal)
            )
        )
        .scalars()
        .all()
    )
    by_table: dict[uuid.UUID, list[tuple[uuid.UUID, str, str, bool]]] = {}
    for column in columns:
        by_table.setdefault(column.table_id, []).append(
            (column.id, column.name, column.data_type, column.is_pk)
        )

    return tuple(
        _Target(
            table_id=table.id,
            schema_name=table.schema_name,
            table_name=table.table_name,
            columns=tuple(by_table.get(table.id, ())),
        )
        for table in tables
        if by_table.get(table.id)
    )


async def _sample_everything(
    connector: Connector, targets: Sequence[_Target], budget: Budget
) -> tuple[dict[uuid.UUID, ColumnProfile], int, tuple[str, ...]]:
    """Read every table, until the clock says stop."""
    started = time.monotonic()
    caps = connector.capabilities()
    profiles: dict[uuid.UUID, ColumnProfile] = {}
    errors: list[str] = []
    skipped = 0

    for index, target in enumerate(targets):
        if time.monotonic() - started >= budget.wall_clock_seconds:
            # Everything from here on is skipped, and the count is what makes
            # "partial" a number rather than a shrug.
            skipped = len(targets) - index
            break

        names = [name for _, name, _, _ in target.columns]
        query = _sample_query(caps, target.schema_name, target.table_name, names, budget.max_rows)
        try:
            frame: ResultFrame = await connector.execute(
                query,
                ExecLimits(
                    max_rows=budget.max_rows, timeout_seconds=budget.per_query_timeout_seconds
                ),
            )
        except ConnectorError as error:
            # One unreadable table does not end the pass: a view over a missing
            # object, or a permission the crawl could see but not read through,
            # is that table's problem and not the catalog's.
            errors.append(f"{target.schema_name}.{target.table_name}: {error}")
            skipped += 1
            continue

        position = {name: index for index, name in enumerate(frame.columns)}
        for column_id, name, data_type, is_pk in target.columns:
            at = position.get(name)
            values = [] if at is None else [row[at] for row in frame.rows]
            profiles[column_id] = profile_column(
                name=name,
                data_type=data_type,
                is_pk=is_pk,
                values=values,
                sampled=len(frame.rows),
                budget=budget,
            )

    return profiles, skipped, tuple(errors)


async def _store(
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    data_source_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    results: dict[uuid.UUID, ColumnProfile],
    skipped: int,
    errors: tuple[str, ...],
) -> ProfileOutcome:
    status = STATUS_COMPLETE if skipped == 0 and not errors else STATUS_PARTIAL
    sensitive = 0

    async with org_session(org_id) as session:
        # `in_` of an empty list is valid SQL that matches nothing, so there is
        # no branch here for "nothing was profiled".
        columns = (
            (
                await session.execute(
                    select(CatalogColumn).where(CatalogColumn.id.in_(list(results)))
                )
            )
            .scalars()
            .all()
        )

        tables = {
            table.id: table
            for table in (
                (
                    await session.execute(
                        select(CatalogTable).where(CatalogTable.snapshot_id == snapshot_id)
                    )
                )
                .scalars()
                .all()
            )
        }

        profiled_tables: set[uuid.UUID] = set()
        for column in columns:
            found = results[column.id]
            profiled_tables.add(column.table_id)
            column.null_frac = found.null_frac
            column.distinct_est = found.distinct
            column.min_val = found.min_val
            column.max_val = found.max_val
            column.top_values = found.top_values
            column.semantic_role = found.verdict.semantic_role
            column.sensitivity = found.verdict.sensitivity
            column.sample_rows = found.sample_rows

            if found.verdict.sensitivity == "none":
                continue
            sensitive += 1
            table = tables.get(column.table_id)
            if table is not None:
                await _default_to_mask(
                    session,
                    org_id=org_id,
                    data_source_id=data_source_id,
                    schema_name=table.schema_name,
                    table_name=table.table_name,
                    column_name=column.name,
                    kind=found.verdict.kind,
                )

        snapshot = await session.get(CatalogSnapshot, snapshot_id)
        if snapshot is not None:
            snapshot.profile_status = status
            snapshot.profiled_at = datetime.now(UTC)

        audit(
            session,
            org_id=org_id,
            actor_user_id=actor_user_id,
            action="catalog.profiled",
            object_type="data_source",
            object_id=str(data_source_id),
            # Counts and names of columns, never a value out of one.
            details={
                "status": status,
                "columns": len(results),
                "sensitive_columns": sensitive,
                "tables_skipped": skipped,
            },
        )

    detail = f"Profiled {len(results)} column(s); {sensitive} look sensitive and default to masked."
    if status == STATUS_PARTIAL:
        detail += f" Stopped early: {skipped} table(s) not profiled."

    return ProfileOutcome(
        status=status,
        detail=detail,
        tables_profiled=len(profiled_tables),
        columns_profiled=len(results),
        sensitive_columns=sensitive,
        tables_skipped=skipped,
        errors=errors,
    )


async def _default_to_mask(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    data_source_id: uuid.UUID,
    schema_name: str,
    table_name: str,
    column_name: str,
    kind: str | None,
) -> None:
    """Give a suspected column a masking policy, unless a person has decided.

    The check for an existing row is the important half: an Admin who set a
    column to ``allow`` has looked at it, and a later profile run must not
    quietly overrule them (DECISIONS D-013).
    """
    existing = (
        (
            await session.execute(
                select(ColumnPolicy).where(
                    ColumnPolicy.data_source_id == data_source_id,
                    ColumnPolicy.schema_name == schema_name,
                    ColumnPolicy.table_name == table_name,
                    ColumnPolicy.column_name == column_name,
                )
            )
        )
        .scalars()
        .one_or_none()
    )
    if existing is not None:
        return

    session.add(
        ColumnPolicy(
            org_id=org_id,
            data_source_id=data_source_id,
            schema_name=schema_name,
            table_name=table_name,
            column_name=column_name,
            policy="mask",
            mask_type=kind,
            reason="Detected automatically; nobody has reviewed it yet.",
            decided_by=None,
        )
    )
