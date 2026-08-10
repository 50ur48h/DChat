"""Liveness probe.

Unauthenticated by design — compose, Container Apps and the web app's health
widget all poll it — so it may report build identity and nothing else.
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

    status: Literal["ok"] = "ok"
    version: str = Field(description="Application version from package metadata.")
    git_sha: str = Field(description="Commit the image was built from, or 'unknown'.")


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
    return HealthResponse(version=resolve_version(), git_sha=settings.git_sha)
