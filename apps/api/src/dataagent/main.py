"""FastAPI application factory.

Kept deliberately thin: it wires configuration, middleware and routers, and
nothing else. Everything with behaviour lives in a package that can be tested
without an HTTP server.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dataagent import health
from dataagent.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application.

    Accepting settings makes the factory testable without touching the process
    environment; production callers pass nothing and get the cached settings.
    """
    resolved = settings if settings is not None else get_settings()

    # Before anything is mounted: a process that boots and then fails open is
    # indistinguishable from one that works until somebody looks.
    resolved.assert_auth_is_production_safe()

    app = FastAPI(
        title="data-agent API",
        version=health.resolve_version(),
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

    if resolved.auth_mode == "dev":
        # Imported here, not at module scope: the prod image does not contain
        # this module at all, so a top-level import would break that image even
        # though the branch could never be taken.
        from dataagent.auth import dev_issuer

        app.state.dev_issuer = dev_issuer.build_dev_issuer(resolved)
        app.include_router(dev_issuer.router)

    return app


app = create_app()
