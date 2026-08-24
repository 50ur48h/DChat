"""Liveness probe.

Unauthenticated by design — compose, Container Apps and the web app's health
widget all poll it — so it may report build identity and nothing else.

**It also reports `degraded`, and that is not scope creep.** A deployment once
answered `{"status":"ok"}` on this route while *every authenticated route*
returned 500, because `AUTH_MODE=entra` was set and `OIDC_AUTHORITY` was not. The
probe was telling the truth about the process and nothing useful about the
service, which is the same failure as a smoke that reports a working deployment
as broken and a test that cannot fail: a check whose green means less than a
reader assumes.

So a configuration that promises a mode it cannot serve is `degraded` here. It
names the missing variables — they are variable *names*, never values — because
the reader's next question is which one to set. It deliberately does **not**
check reachability: a database that is briefly down is not a misconfiguration,
and a liveness probe that fails on someone else's outage causes an outage of its
own. What is reported is what this process was configured with and cannot honour.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from dataagent import __version__
from dataagent.config import Settings, get_settings

router = APIRouter(tags=["meta"])


class HealthResponse(BaseModel):
    """Payload of ``GET /healthz``.

    Carries no configuration, no environment name and no secret.
    """

    status: Literal["ok", "degraded"] = "ok"
    version: str = Field(description="Application version from package metadata.")
    git_sha: str = Field(description="Commit the image was built from, or 'unknown'.")
    missing_settings: list[str] = Field(
        default_factory=list,
        description=(
            "Environment variables this configuration requires and does not have. "
            "Names only — never values. Empty when status is 'ok'."
        ),
    )


def resolve_version() -> str:
    """Installed package version, falling back to the source constant.

    Running straight from ``src`` without an install is normal in tooling
    contexts, so a missing distribution is not an error.
    """
    try:
        return package_version("dataagent")
    except PackageNotFoundError:
        return __version__


@router.get("/healthz", summary="Liveness probe")
async def healthz(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    missing = settings.missing_for_mode()
    return HealthResponse(
        status="degraded" if missing else "ok",
        version=resolve_version(),
        git_sha=settings.git_sha,
        missing_settings=missing,
    )
