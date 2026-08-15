"""FastAPI application factory.

Kept deliberately thin: it wires configuration, middleware and routers, and
nothing else. Everything with behaviour lives in a package that can be tested
without an HTTP server.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dataagent import health
from dataagent.catalog import routes as catalog_routes
from dataagent.config import Settings, get_settings
from dataagent.datasources import routes as datasource_routes
from dataagent.invitations import routes as invitation_routes
from dataagent.orgs import routes as orgs_routes
from dataagent.runs import routes as runs_routes

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Reconcile runs a restart stopped, then serve.

    Runs are in-process async tasks (architecture 0.2.4), so a redeploy kills
    whatever was in flight and leaves rows claiming to be `running` with nothing
    running them. 0.2.4 names this as the honest weakness of the model and asks
    for exactly this mitigation.

    **A failure here must not stop the process booting.** ``/healthz`` has to
    answer on a bare checkout with no database — that is what WP0.2 promised and
    what CI relies on — so an unreachable database is logged and the app serves.
    The cost of skipping the sweep is stale statuses, not a broken deployment.
    """
    from dataagent.agent.scheduler import sweep_orphaned_runs

    try:
        await sweep_orphaned_runs()
    except Exception:
        logger.exception("could not reconcile interrupted runs at startup")
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application.

    Accepting settings makes the factory testable without touching the process
    environment; production callers pass nothing and get the cached settings.
    """
    resolved = settings if settings is not None else get_settings()

    # Before anything is mounted: a process that boots and then fails open is
    # indistinguishable from one that works until somebody looks.
    resolved.assert_auth_is_production_safe()
    resolved.assert_secrets_backend_is_production_safe()

    app = FastAPI(
        title="data-agent API",
        version=health.resolve_version(),
        lifespan=_lifespan,
    )

    if settings is not None:
        # Routes read settings through the dependency, so overriding it here is
        # what makes an injected Settings object reach them.
        app.dependency_overrides[get_settings] = lambda: resolved

    # The web app is the only first-party client (arch 7.2 — no APIM in V1).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Settings, not a validator. Building one here would mean an application
    # with no identity provider configured could not boot at all — and
    # /healthz must answer on a bare checkout, which is what WP0.2 promised
    # and what CI relies on. The validator is built on first use instead, so a
    # misconfigured deployment fails closed on protected routes rather than
    # taking down the liveness probe with it.
    app.state.settings = resolved
    app.state.token_validator = None

    app.include_router(health.router)
    app.include_router(orgs_routes.router)
    app.include_router(invitation_routes.router)
    app.include_router(datasource_routes.router)
    app.include_router(catalog_routes.router)
    app.include_router(runs_routes.router)

    if resolved.auth_mode == "dev":
        # Imported here, not at module scope: the prod image does not contain
        # this module at all, so a top-level import would break that image even
        # though the branch could never be taken.
        from dataagent.auth import dev_issuer

        app.state.dev_issuer = dev_issuer.build_dev_issuer(resolved)
        app.include_router(dev_issuer.router)

    return app


app = create_app()
