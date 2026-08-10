"""Tests for ``GET /healthz`` — the walking skeleton's single endpoint."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from dataagent.config import Settings
from dataagent.main import create_app


async def test_healthz_returns_ok_with_build_identity(client: AsyncClient) -> None:
    response = await client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["git_sha"] == "testsha"
    assert body["version"]


async def test_healthz_reports_the_configured_git_sha() -> None:
    """The probe identifies the build, so a stale revision is visible in seconds."""
    app = create_app(settings=Settings(git_sha="deadbeef"))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        body = (await ac.get("/healthz")).json()

    assert body["git_sha"] == "deadbeef"


async def test_healthz_leaks_no_configuration(client: AsyncClient) -> None:
    """An unauthenticated endpoint may expose build identity and nothing else."""
    body = (await client.get("/healthz")).json()

    assert set(body) == {"status", "version", "git_sha"}
