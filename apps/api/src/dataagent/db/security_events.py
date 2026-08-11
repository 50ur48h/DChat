"""The platform-level security log: denials that belong to no tenant.

See DECISIONS D-008. Writes go through the owner session because this table is
deliberately outside tenant isolation — it exists precisely for the events that
have no trustworthy organization to be scoped to.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, Identity, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from dataagent.db.base import Base, CreatedAt
from dataagent.db.engine import system_session

logger = logging.getLogger(__name__)

OUTCOMES = ("denied", "error")


class SecurityEvent(Base):
    """Not tenant-scoped, and named to say so.

    The organization column is ``attempted_org_id``: it records what was asked
    for, not what this row belongs to. The RLS proof suite treats any ``org_id``
    column as a tenant scope that must be declared and protected, so calling this
    one ``org_id`` would either break that guard or quietly weaken it.
    """

    __tablename__ = "security_events"
    __table_args__ = (
        CheckConstraint("outcome IN ('denied', 'error')", name="outcome_valid"),
        Index("ix_security_events_ts", text("ts DESC")),
        Index("ix_security_events_actor_subject", "actor_subject"),
        Index("ix_security_events_attempted_org_id", "attempted_org_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_subject: Mapped[str | None] = mapped_column(String(255))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column()
    attempted_org_id: Mapped[uuid.UUID | None] = mapped_column()
    route: Mapped[str | None] = mapped_column(String(200))
    method: Mapped[str | None] = mapped_column(String(10))
    details: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    ts: Mapped[CreatedAt]


async def record_security_event(
    *,
    action: str,
    reason: str,
    outcome: str = "denied",
    actor_subject: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    attempted_org_id: uuid.UUID | None = None,
    route: str | None = None,
    method: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Record a denial that has no tenant to belong to.

    Never raises. A failure to write the security log must not turn a clean 403
    into a 500 — the caller is being refused either way, and an exception here
    would convert an access-control decision into an outage. The failure is
    logged loudly instead.
    """
    try:
        async with system_session() as session:
            session.add(
                SecurityEvent(
                    action=action,
                    outcome=outcome,
                    reason=reason,
                    actor_subject=actor_subject,
                    actor_user_id=actor_user_id,
                    attempted_org_id=attempted_org_id,
                    route=route,
                    method=method,
                    details=details or {},
                )
            )
            await session.commit()
    except Exception:
        logger.exception(
            "could not record security event",
            extra={"action": action, "reason": reason, "attempted_org_id": str(attempted_org_id)},
        )
