"""Recording access-control decisions (DECISIONS D-008).

Three destinations, chosen by how much we actually know:

* **401, no trustworthy identity** — the application log only. There is no
  tenant to attribute it to, and taking the organization from the URL would let
  an unauthenticated caller write rows into any tenant's audit log at will.
* **403 with a resolved membership** — ``audit_log``, scoped to that
  organization. This is the row an admin expects to find.
* **403 without a resolvable organization** — ``security_events``, the
  platform-level log. Not lost, and queryable: "which accounts have been probing
  for tenants they do not belong to" is one indexed query.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from dataagent.auth.context import RequestContext
from dataagent.auth.principal import Principal
from dataagent.db.models import AuditLog
from dataagent.db.security_events import record_security_event
from dataagent.tenancy.session import org_session

logger = logging.getLogger(__name__)

AUTH_DENIED = "auth.denied"


def log_unauthenticated(*, reason: str, route: str | None, method: str | None) -> None:
    """A 401. Application log only, and deliberately so.

    Writing this to a tenant's audit_log would mean an unauthenticated request
    could choose whose audit log to fill.
    """
    # In the message, not in `extra`: the default formatter drops extras, so a
    # reason recorded there is invisible exactly when it is needed. It names the
    # failed check, never anything from the token.
    logger.warning(
        "rejected an unauthenticated request: reason=%s %s %s", reason, method or "-", route or "-"
    )


async def record_denial(
    *,
    context: RequestContext | None,
    principal: Principal | None,
    reason: str,
    attempted_org_id: uuid.UUID | None,
    route: str | None,
    method: str | None,
    details: dict[str, Any] | None = None,
) -> None:
    """A 403, recorded wherever it can honestly be attributed."""
    if context is not None:
        await _record_org_denial(
            context=context, reason=reason, route=route, method=method, details=details
        )
        return

    await record_security_event(
        action=AUTH_DENIED,
        reason=reason,
        actor_subject=principal.subject if principal else None,
        attempted_org_id=attempted_org_id,
        route=route,
        method=method,
        details=details,
    )


async def _record_org_denial(
    *,
    context: RequestContext,
    reason: str,
    route: str | None,
    method: str | None,
    details: dict[str, Any] | None,
) -> None:
    payload: dict[str, object] = {"reason": reason, "role": context.role, **(details or {})}
    try:
        async with org_session(context.org_id) as session:
            session.add(
                AuditLog(
                    org_id=context.org_id,
                    actor_user_id=context.user_id,
                    action=AUTH_DENIED,
                    object_type="route",
                    object_id=f"{method} {route}" if route else None,
                    details=payload,
                    sensitive=False,
                )
            )
    except Exception:
        # A failure to audit must not turn a 403 into a 500: the caller is being
        # refused either way. Fall back to the platform log so it is not lost.
        logger.exception("could not write an audit row for a denial")
        await record_security_event(
            action=AUTH_DENIED,
            reason=reason,
            outcome="error",
            actor_subject=context.principal.subject,
            actor_user_id=context.user_id,
            attempted_org_id=context.org_id,
            route=route,
            method=method,
            details={"audit_write_failed": True, **payload},
        )
