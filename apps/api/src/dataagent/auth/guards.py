"""Route guards — the first of the three authorization layers (arch Part 6.2).

Layer 1 is here: a FastAPI dependency that refuses the request before the handler
runs. Layer 2 is the org-scoped repository, layer 3 is row-level security. This
one is the only layer that can produce a *helpful* refusal, so it is also the one
that records why.

The role matrix, straight from architecture Part 6.2:

| Action                                              | Admin | Contributor | Reader |
|-----------------------------------------------------|-------|-------------|--------|
| Ask questions, view own conversations and traces     |   ✓   |      ✓      |   ✓    |
| Upload knowledge, semantic defs, verified queries    |   ✓   |      ✓      |   -    |
| Manage data sources, members, config, column policy  |   ✓   |      -      |   -    |
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Path, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from dataagent.auth import audit
from dataagent.auth.context import AuthorizationError, RequestContext, resolve_context
from dataagent.auth.jwks import JwksCache
from dataagent.auth.jwt_validator import TokenValidator
from dataagent.auth.principal import Principal, TokenError
from dataagent.config import Settings

#: Roles that may do each class of thing. Named rather than inlined so the matrix
#: above and the code cannot drift, and so WP2.3's snapshot test has something to
#: assert against.
ANY_MEMBER: tuple[str, ...] = ("admin", "contributor", "reader")
CONTRIBUTOR_OR_ADMIN: tuple[str, ...] = ("admin", "contributor")
ADMIN_ONLY: tuple[str, ...] = ("admin",)

_bearer = HTTPBearer(auto_error=False, description="OIDC access token")


def _unauthorized() -> HTTPException:
    """One undifferentiated 401.

    Never says *why*: distinguishing "expired" from "wrong signature" from "no
    such user" tells an attacker which half of the forgery worked.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _forbidden(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)


def _validator(request: Request) -> TokenValidator:
    """The application's one validator, built on first use.

    Lazy because an unconfigured identity provider must not stop the process
    booting — /healthz has to answer on a bare checkout. It does mean a
    misconfigured deployment discovers the problem on its first protected
    request; that direction is safe, because the failure is a refusal.
    """
    validator = getattr(request.app.state, "token_validator", None)
    if isinstance(validator, TokenValidator):
        return validator

    settings = getattr(request.app.state, "settings", None)
    if not isinstance(settings, Settings):  # pragma: no cover - wiring error
        raise RuntimeError("No settings are configured on this application")

    issuer = settings.resolve_issuer()
    built = TokenValidator(
        issuer=issuer, audience=settings.oidc_audience, jwks=JwksCache(issuer=issuer)
    )
    request.app.state.token_validator = built
    return built


async def current_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    """Authenticate. Nothing here knows or cares about organizations."""
    route = request.url.path
    method = request.method

    if credentials is None or not credentials.credentials:
        audit.log_unauthenticated(reason="missing_bearer", route=route, method=method)
        raise _unauthorized()

    try:
        return await _validator(request).validate(credentials.credentials)
    except TokenError as error:
        audit.log_unauthenticated(reason=error.code, route=route, method=method)
        raise _unauthorized() from error


async def current_context(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    org_id: Annotated[uuid.UUID, Path(description="Organization the request acts within")],
) -> RequestContext:
    """Authenticate, then resolve membership of the organization in the path."""
    try:
        return await resolve_context(principal, org_id)
    except AuthorizationError as error:
        # No resolvable organization, so there is no tenant audit log this could
        # honestly belong to. It goes to the platform security log instead of
        # being dropped — see DECISIONS D-008.
        await audit.record_denial(
            context=None,
            principal=principal,
            reason=error.reason,
            attempted_org_id=org_id,
            route=request.url.path,
            method=request.method,
        )
        raise _forbidden("You do not have access to this organization") from error


def require_role(*allowed: str) -> Callable[..., Awaitable[RequestContext]]:
    """Build a dependency that admits only the given roles.

    Every refusal past this point has a resolved membership, so it is recorded in
    that organization's own audit log where its admins will find it.
    """
    if not allowed:  # pragma: no cover - programming error, not a runtime path
        raise ValueError("require_role() needs at least one role")

    async def dependency(
        request: Request,
        context: Annotated[RequestContext, Depends(current_context)],
    ) -> RequestContext:
        if context.role not in allowed:
            await audit.record_denial(
                context=context,
                principal=context.principal,
                reason="insufficient_role",
                attempted_org_id=context.org_id,
                route=request.url.path,
                method=request.method,
                details={"required": list(allowed)},
            )
            raise _forbidden("Your role does not permit this action")
        return context

    return dependency


require_member = require_role(*ANY_MEMBER)
require_contributor = require_role(*CONTRIBUTOR_OR_ADMIN)
require_admin = require_role(*ADMIN_ONLY)
