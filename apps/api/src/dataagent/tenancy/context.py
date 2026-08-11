"""Who the current request belongs to.

Built once per request from the validated JWT (Phase 2) and read by the tenancy
session, so no code path can reach data without first saying which organization
it is acting for.

The honest limit of this design, stated plainly: RLS keys off a session variable
that this process sets. It therefore protects against a repository that forgets a
``WHERE`` clause — the common, quiet bug — and not against code that deliberately
sets the wrong organization. Guarding *that* is the job of the auth context and
the route guards above it (architecture Part 6.2).
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass


class MissingTenantContextError(RuntimeError):
    """Raised when code asks for tenant-scoped data outside a request context."""

    def __init__(self) -> None:
        super().__init__(
            "No tenant context is set. Feature code must run inside a request "
            "whose organization is known; background and bootstrap work uses "
            "system_session() and is audited separately."
        )


@dataclass(frozen=True, slots=True)
class TenantContext:
    org_id: uuid.UUID
    user_id: uuid.UUID | None = None
    role: str | None = None


_current: ContextVar[TenantContext | None] = ContextVar("dataagent_tenant_context", default=None)


def current_context() -> TenantContext:
    context = _current.get()
    if context is None:
        raise MissingTenantContextError
    return context


def current_context_or_none() -> TenantContext | None:
    return _current.get()


def set_tenant_context(context: TenantContext) -> Token[TenantContext | None]:
    return _current.set(context)


def reset_tenant_context(token: Token[TenantContext | None]) -> None:
    _current.reset(token)


@contextmanager
def tenant_context(context: TenantContext) -> Generator[TenantContext]:
    """Scope a block of work to one organization."""
    token = set_tenant_context(context)
    try:
        yield context
    finally:
        reset_tenant_context(token)
