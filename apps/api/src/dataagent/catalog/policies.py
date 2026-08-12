"""What may be done with a column's values, and who decided (arch Part 5.3).

Kept apart from the catalog on purpose (DECISIONS D-013). A snapshot describes
what a database looked like; a policy is a judgement about a column *by name*,
and it has to outlive every re-discovery. A refresh that reset somebody's
masking decision would be a leak caused by a routine operation — the kind of
failure nobody thinks to look for because nothing failed.

The resolution order is: an Admin's decision if there is one, otherwise ``mask``
when the classifier suspects the column, otherwise ``allow``. Only the first of
those three is a person's opinion, and ``decided_by`` says which rows are.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from dataagent.db.models import CatalogColumn, CatalogSnapshot, CatalogTable, ColumnPolicy
from dataagent.orgs.service import audit
from dataagent.tenancy.session import org_session

__all__ = ["ColumnNotFoundError", "PolicyView", "effective_policy", "set_policy"]

POLICY_ALLOW = "allow"
POLICY_MASK = "mask"


class ColumnNotFoundError(Exception):
    """No such column in this organization's active catalog."""


@dataclass(frozen=True, slots=True)
class PolicyView:
    schema_name: str
    table_name: str
    column_name: str
    policy: str
    mask_type: str | None
    reason: str | None
    #: None when nothing but the classifier has ever had an opinion.
    decided_by: uuid.UUID | None
    decided_at: datetime


def effective_policy(*, stored: str | None, sensitivity: str) -> str:
    """What actually applies to a column.

    A person's decision wins. Failing that, suspicion means ``mask`` — the
    default is chosen in the safe direction because it is chosen before anybody
    has looked.
    """
    if stored is not None:
        return stored
    return POLICY_MASK if sensitivity != "none" else POLICY_ALLOW


async def set_policy(
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    data_source_id: uuid.UUID,
    column_id: uuid.UUID,
    policy: str,
    reason: str | None = None,
    mask_type: str | None = None,
) -> PolicyView:
    """Record an Admin's decision about one column of the active catalog.

    Addressed by the catalog column an Admin was looking at, and stored by the
    name that column has — so the decision survives the next refresh, which will
    give that column a new id.
    """
    async with org_session(org_id) as session:
        found = (
            await session.execute(
                select(CatalogTable.schema_name, CatalogTable.table_name, CatalogColumn.name)
                .join(CatalogColumn, CatalogColumn.table_id == CatalogTable.id)
                .join(CatalogSnapshot, CatalogSnapshot.id == CatalogTable.snapshot_id)
                .where(
                    CatalogColumn.id == column_id,
                    CatalogSnapshot.data_source_id == data_source_id,
                )
            )
        ).one_or_none()
        if found is None:
            raise ColumnNotFoundError("No such column in this data source's catalog")

        schema_name, table_name, column_name = found

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

        if existing is None:
            existing = ColumnPolicy(
                org_id=org_id,
                data_source_id=data_source_id,
                schema_name=schema_name,
                table_name=table_name,
                column_name=column_name,
                policy=policy,
            )
            session.add(existing)
        existing.policy = policy
        existing.mask_type = mask_type
        existing.reason = reason
        existing.decided_by = actor_user_id
        existing.decided_at = datetime.now(UTC)

        audit(
            session,
            org_id=org_id,
            actor_user_id=actor_user_id,
            action="catalog.column_policy_set",
            object_type="catalog_column",
            object_id=f"{schema_name}.{table_name}.{column_name}",
            # The decision and its reason. Never a value from the column — that
            # is the thing the policy exists to protect.
            details={"policy": policy, "reason": reason, "mask_type": mask_type},
        )

        return PolicyView(
            schema_name=schema_name,
            table_name=table_name,
            column_name=column_name,
            policy=existing.policy,
            mask_type=existing.mask_type,
            reason=existing.reason,
            decided_by=existing.decided_by,
            decided_at=existing.decided_at,
        )
