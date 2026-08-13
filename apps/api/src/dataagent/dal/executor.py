"""Running an approved statement, and bounding what comes back (arch 7.1).

Steps 4 to 6 of the data access flow: resolve the credential, execute with a
deadline and a row cap, mask what the policies cover. Step 7 — the audit row and
the stored artifact — is WP5.2b, and this module is written so that adding it
means wrapping this, not editing it.

**Every bound is applied twice, in two different places, on purpose.**
The row cap is written into the SQL, so the *engine* stops early and a query
that would have scanned a hundred million rows is cheap; and the connector
fetches one row past the cap, so a result is truncated even if the limit was
somehow not applied. The deadline is a statement timeout the engine enforces,
and the connector holds the driver-level one. Neither layer is trusted to be the
only one, because the failure they guard against — an unbounded query against a
customer's production database — is one this service cannot take back.

**The credential is read here and nowhere else in the DAL.** It comes from the
``SecretsProvider`` through ``connector_for_view``, lives as long as the
connector does, and never reaches a return value, a log line or an exception:
connectors raise ``ConnectorError``, which is already sanitized.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from dataagent.connectors.base import Connector
from dataagent.dal.masking import MaskedFrame, mask_frame, styles_for
from dataagent.dal.policy import source_policy
from dataagent.dal.validator import Validated, validate
from dataagent.datasources import service as datasources

__all__ = ["Execution", "run"]


@dataclass(frozen=True, slots=True)
class Execution:
    """What one statement did. The object the agent's tool layer will hand on.

    Carries the validation as well as the result, because the questions asked of
    it afterwards — which tables were read, was anything sensitive touched, what
    exactly ran — are answered by the validation, and an audit row assembled
    from a remembered copy is an audit row that can be wrong.
    """

    validated: Validated
    frame: MaskedFrame

    @property
    def sql(self) -> str:
        """The canonical statement, as sent. Safe to store: it is our own text."""
        return self.validated.sql

    @property
    def sql_hash(self) -> str:
        return self.validated.query.sql_hash

    @property
    def row_count(self) -> int:
        return self.frame.row_count

    @property
    def truncated(self) -> bool:
        return self.frame.truncated

    @property
    def duration_ms(self) -> int:
        return self.frame.duration_ms

    @property
    def sensitive_accessed(self) -> bool:
        """True when the statement read a column under a mask policy — whether
        or not the value survived into the result. The audit question is what
        was *reached for*, not what was returned."""
        return self.validated.touches_sensitive


async def run(
    *,
    org_id: uuid.UUID,
    data_source_id: uuid.UUID,
    sql: str,
    max_rows: int | None = None,
    connector: Connector | None = None,
) -> Execution:
    """Validate, execute, mask. The one path from a statement to rows.

    Raises ``PolicyViolation`` when the statement is refused — before anything
    is connected to, so a rejected query costs a customer's database nothing —
    and ``ConnectorError`` when the far end fails. Nothing else.

    ``connector`` is for tests that supply their own; in every other case one is
    opened here and closed in the ``finally``, because a session left open is
    left open on somebody else's server.
    """
    policy = await source_policy(org_id, data_source_id)
    limits = policy.limits_for(max_rows)

    # Validation first, and the row cap is part of it: the SQL that carries the
    # limit may only be emitted by the module holding the grant.
    validated = validate(sql, source=policy, max_rows=limits.max_rows)

    supplied = connector is not None
    if connector is None:
        view = await datasources.get_data_source(org_id, data_source_id)
        connector = await datasources.connector_for_view(view)

    try:
        frame = await connector.execute(validated.query, limits)
    finally:
        if not supplied:
            await connector.aclose()

    return Execution(
        validated=validated,
        frame=mask_frame(frame, validated.projections, styles_for(policy.catalog)),
    )
