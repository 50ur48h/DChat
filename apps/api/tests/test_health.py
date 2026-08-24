"""Tests for ``GET /healthz``.

Two of these changed when the probe learned to report `degraded`. A deployment
answered `{"status":"ok"}` here while every authenticated route returned 500,
because `AUTH_MODE=entra` was set and `OIDC_AUTHORITY` was not — so the probe was
telling the truth about the process and nothing useful about the service.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from dataagent.config import Settings
from dataagent.main import create_app


async def test_healthz_returns_ok_with_build_identity(client: AsyncClient) -> None:
    response = await client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["git_sha"] == "testsha"
    assert body["version"]
    assert body["missing_settings"] == []


async def test_healthz_is_degraded_when_the_mode_cannot_be_served() -> None:
    """**The case that shipped.** `AUTH_MODE=entra` with no authority is a
    configuration that boots, passes a liveness probe, and refuses every
    authenticated request. The probe now says so, and names the variable."""
    app = create_app(
        settings=Settings(  # pyright: ignore[reportArgumentType]
            auth_mode="entra",
            oidc_authority=None,
            secrets_backend="local",
            local_secrets_key=SecretStr("x"),
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        response = await ac.get("/healthz")

    # 200, deliberately: the process is alive and an orchestrator restarting it
    # would fix nothing. The body is what carries the bad news.
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["missing_settings"] == ["OIDC_AUTHORITY"]


async def test_healthz_reports_the_configured_git_sha() -> None:
    """The probe identifies the build, so a stale revision is visible in seconds."""
    app = create_app(settings=Settings(git_sha="deadbeef"))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        body = (await ac.get("/healthz")).json()

    assert body["git_sha"] == "deadbeef"


async def test_healthz_leaks_no_configuration(client: AsyncClient) -> None:
    """An unauthenticated endpoint may expose build identity and nothing else.

    `missing_settings` is new and is the reason this test is worth re-reading: it
    carries variable **names**, never values, and the assertion below is what
    keeps that true. A probe that helpfully printed the authority it was missing
    would be publishing the tenant to anyone who asked.
    """
    body = (await client.get("/healthz")).json()

    assert set(body) == {"status", "version", "git_sha", "missing_settings"}
    assert all(name.isupper() for name in body["missing_settings"])


async def test_a_degraded_probe_names_variables_and_never_values() -> None:
    """The leak this could have been. Everything reported is a name."""
    app = create_app(
        settings=Settings(  # pyright: ignore[reportArgumentType]
            auth_mode="entra",
            oidc_authority=None,
            secrets_backend="keyvault",
            key_vault_url=None,
            artifacts_backend="blob",
            artifacts_account_url=None,
            git_sha="testsha",
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        body = (await ac.get("/healthz")).json()

    assert body["missing_settings"] == [
        "ARTIFACTS_ACCOUNT_URL",
        "KEY_VAULT_URL",
        "OIDC_AUTHORITY",
    ]
    # Nothing in the payload is a URL, a key, or anything but a name and a sha.
    rendered = str(body)
    assert "://" not in rendered
