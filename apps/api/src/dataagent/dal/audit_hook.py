"""Recording what the DAL did (architecture Part 8.2, 10.1).

Three kinds of thing happen when a statement is submitted, and all three are
written down:

* it **ran** — a row with what it read, how much came back, and how long;
* it **failed** — the engine or the connection said no, and the sanitized reason;
* it **was refused** — this service would not send it, with the violation code.

The third is the one this module exists for. A query that never reached the
database is invisible everywhere else: no engine log, no connection, no latency
graph. If the only record of "something tried to read a denied column" is a log
line that scrolled past, then the question architecture 8.2 promises to answer —
*who accessed what, when, and was it sensitive* — has a hole in exactly the
shape of an attack.

Two rows are written per attempt, into two tables that answer different
questions. ``query_executions`` is the operational record, read by an admin
screen and by the agent's own trace. ``audit_log`` is the append-only trail the
application role cannot rewrite, read when someone needs to be sure. They are
written in one transaction: an action without a record, and a record of
something that did not happen, are both worse than either alone.

**Failing to record is not allowed to fail the query silently, or loudly.** If
the platform database is unreachable, the answer the customer already has is
still their answer — so this raises, and the caller decides. What the caller
does not get is a way to skip it: ``dal.run`` is the entry point and it calls
this on every path, including the ones that raise.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from dataagent.dal.artifacts import ArtifactStore, encodable, encode, expires_at, summarize
from dataagent.dal.errors import PolicyViolation
from dataagent.dal.executor import Execution
from dataagent.db.models import QueryExecution, ResultArtifact
from dataagent.orgs.service import audit
from dataagent.tenancy.session import org_session

__all__ = ["Recorded", "hash_sql", "record_failure", "record_refusal", "record_success"]

STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_REFUSED = "refused"

#: How many rows of a result are kept inline on the artifact row. The rest live
#: in the store; this is what a screen or a critic reads without fetching a file.
SAMPLE_ROWS = 50

#: What the audit trail calls each outcome. Prefixed like every other action, so
#: `dal.*` is one filter.
ACTION_EXECUTED = "dal.query_executed"
ACTION_FAILED = "dal.query_failed"
ACTION_REFUSED = "dal.query_refused"


@dataclass(frozen=True, slots=True)
class Recorded:
    """The identifiers of what was just written down."""

    execution_id: uuid.UUID
    artifact_id: uuid.UUID | None = None
    storage_ref: str | None = None


async def record_success(
    *,
    org_id: uuid.UUID,
    data_source_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    execution: Execution,
    store: ArtifactStore | None = None,
    run_id: uuid.UUID | None = None,
) -> Recorded:
    """One query that ran: its record, its artifact, and its audit row."""
    async with org_session(org_id) as session:
        row = QueryExecution(
            org_id=org_id,
            data_source_id=data_source_id,
            actor_user_id=actor_user_id,
            run_id=run_id,
            sql_text=execution.sql,
            sql_hash=execution.sql_hash,
            tables=[str(table) for table in execution.validated.tables],
            columns=[str(column) for column in execution.validated.columns],
            status=STATUS_OK,
            row_count=execution.row_count,
            duration_ms=execution.duration_ms,
            sensitive_accessed=execution.sensitive_accessed,
        )
        session.add(row)
        # Flushed rather than committed: the artifact and the audit row need this
        # row's id, and all three still land or fail together.
        await session.flush()

        artifact = ResultArtifact(
            org_id=org_id,
            query_execution_id=row.id,
            summary=summarize(execution.frame),
            # Already masked, and capped: the whole result is in the store, and
            # a row in the platform database is not the place for a thousand of
            # anything.
            sample_rows=[
                [encodable(value) for value in sample]
                for sample in execution.frame.rows[:SAMPLE_ROWS]
            ],
            truncated=execution.truncated,
            expires_at=expires_at(),
        )
        session.add(artifact)
        await session.flush()

        _audit(
            session,
            org_id=org_id,
            actor_user_id=actor_user_id,
            action=ACTION_EXECUTED,
            execution=row,
            details={
                "rows": execution.row_count,
                "duration_ms": execution.duration_ms,
                "truncated": execution.truncated,
                "masked_columns": list(execution.frame.masked_columns),
            },
        )
        recorded = Recorded(execution_id=row.id, artifact_id=artifact.id)

    # Outside the transaction on purpose. A store that is slow or unreachable
    # must not hold a database transaction open, and a result whose file failed
    # to write is still a result that ran — the row says so, and `storage_ref`
    # staying null says the copy is missing rather than pretending it is there.
    if store is not None:
        reference = await store.put(
            org_id=org_id, execution_id=recorded.execution_id, payload=encode(execution.frame)
        )
        await _attach_storage(org_id, recorded.execution_id, reference)
        return Recorded(
            execution_id=recorded.execution_id,
            artifact_id=recorded.artifact_id,
            storage_ref=reference,
        )
    return recorded


async def record_failure(
    *,
    org_id: uuid.UUID,
    data_source_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    sql: str,
    sql_hash: str,
    error: str,
    run_id: uuid.UUID | None = None,
) -> Recorded:
    """A statement that was sent and did not come back.

    ``error`` has been through the connector's sanitizer already — connectors
    raise nothing else — so what is stored names what failed, never an address
    or a credential.
    """
    async with org_session(org_id) as session:
        row = QueryExecution(
            org_id=org_id,
            data_source_id=data_source_id,
            actor_user_id=actor_user_id,
            run_id=run_id,
            sql_text=sql,
            sql_hash=sql_hash,
            status=STATUS_ERROR,
            error=error,
        )
        session.add(row)
        await session.flush()
        _audit(
            session,
            org_id=org_id,
            actor_user_id=actor_user_id,
            action=ACTION_FAILED,
            execution=row,
            details={"error": error},
        )
        return Recorded(execution_id=row.id)


async def record_refusal(
    *,
    org_id: uuid.UUID,
    data_source_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    sql: str,
    violation: PolicyViolation,
    run_id: uuid.UUID | None = None,
) -> Recorded:
    """A statement this service would not send.

    The submitted SQL is stored rather than a canonical form, because there is
    no canonical form — canonicalising is what it did not get to. It is safe to
    store for the same reason the violation message is safe to show: it is the
    model's own text, and the thing that made it dangerous is that it was never
    run (architecture 7.4).
    """
    async with org_session(org_id) as session:
        row = QueryExecution(
            org_id=org_id,
            data_source_id=data_source_id,
            actor_user_id=actor_user_id,
            run_id=run_id,
            sql_text=sql,
            sql_hash=hash_sql(sql),
            status=STATUS_REFUSED,
            violation_code=str(violation.code),
            error=violation.message,
        )
        session.add(row)
        await session.flush()
        _audit(
            session,
            org_id=org_id,
            actor_user_id=actor_user_id,
            action=ACTION_REFUSED,
            execution=row,
            details={
                "code": str(violation.code),
                # The identifier that was refused — a table, a column, a
                # function. The attempted thing, never a value (plan §1.4).
                "subject": violation.subject,
            },
        )
        return Recorded(execution_id=row.id)


def _audit(
    session: object,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    action: str,
    execution: QueryExecution,
    details: dict[str, object],
) -> None:
    audit(
        session,  # pyright: ignore[reportArgumentType]
        org_id=org_id,
        actor_user_id=actor_user_id,
        action=action,
        object_type="query_execution",
        object_id=str(execution.id),
        details={**details, "sql_hash": execution.sql_hash, "tables": execution.tables},
    )


async def _attach_storage(org_id: uuid.UUID, execution_id: uuid.UUID, reference: str) -> None:
    from sqlalchemy import select

    async with org_session(org_id) as session:
        artifact = (
            (
                await session.execute(
                    select(ResultArtifact).where(ResultArtifact.query_execution_id == execution_id)
                )
            )
            .scalars()
            .one_or_none()
        )
        if artifact is not None:
            artifact.storage_ref = reference


def hash_sql(sql: str) -> str:
    """The same short digest ``ValidatedQuery`` uses, for text that never became
    one — a refused statement has no ValidatedQuery to ask."""
    return hashlib.sha256(sql.encode()).hexdigest()[:12]
