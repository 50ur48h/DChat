"""The data access layer's front door (architecture Part 7.1).

``run`` is the whole of it: the agent's ``run_sql`` tool will call this and
nothing else, and every phase after this one inherits the properties it holds.
There is exactly one path from a piece of text to a customer's rows, and it
passes through the validator, the executor, the masker and the recorder in that
order, with no argument that turns any of them off.

The layering is worth stating plainly, because the temptation later will be to
call one layer down:

* ``validator.validate`` decides. It emits SQL, because it holds the grant.
* ``executor.execute`` runs and masks. It knows nothing about the platform
  database.
* ``audit_hook`` writes the record. It knows nothing about connectors.
* ``service.run`` is the only one that knows all three exist, and it records on
  **every** path — the query that worked, the one the engine refused, and the
  one this service refused before the engine was asked.

A refusal is recorded and then re-raised. The caller gets the same
``PolicyViolation`` it would have got without any of this, and the trail has the
row either way.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

from dataagent.connectors.base import Connector, ConnectorError
from dataagent.dal import audit_hook
from dataagent.dal.artifacts import ArtifactStore, artifact_store
from dataagent.dal.errors import PolicyViolation
from dataagent.dal.executor import Execution, execute

__all__ = ["run"]


async def run(
    *,
    org_id: uuid.UUID,
    data_source_id: uuid.UUID,
    sql: str,
    actor_user_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    max_rows: int | None = None,
    store: ArtifactStore | None = None,
    connector: Connector | None = None,
) -> Execution:
    """Validate, execute, mask, record. Raises ``PolicyViolation`` or
    ``ConnectorError`` — and has written a row before it does either.
    """
    resolved_store = store if store is not None else artifact_store()

    try:
        execution = await execute(
            org_id=org_id,
            data_source_id=data_source_id,
            sql=sql,
            max_rows=max_rows,
            # Supplied by the run's lease where there is one (**B-176**).
            # `executor` closes only what it opened, so a supplied connector
            # outlives the query and is closed with the run.
            connector=connector,
        )
    except PolicyViolation as violation:
        await audit_hook.record_refusal(
            org_id=org_id,
            data_source_id=data_source_id,
            actor_user_id=actor_user_id,
            sql=sql,
            violation=violation,
            run_id=run_id,
        )
        raise
    except ConnectorError as error:
        await audit_hook.record_failure(
            org_id=org_id,
            data_source_id=data_source_id,
            actor_user_id=actor_user_id,
            sql=sql,
            sql_hash=audit_hook.hash_sql(sql),
            error=str(error),
            run_id=run_id,
        )
        raise

    recorded = await audit_hook.record_success(
        org_id=org_id,
        data_source_id=data_source_id,
        actor_user_id=actor_user_id,
        execution=execution,
        store=resolved_store,
        run_id=run_id,
    )
    # The id goes back to the caller because a citation must name a row somebody
    # can look up (architecture 4.2). The executor cannot fill it in — it does
    # not write the row — so the front door is the only place that can, which is
    # also the only place callers are supposed to be.
    return replace(execution, execution_id=recorded.execution_id)
