"""Tests for ``GET /healthz``.

Two of these changed when the probe learned to report `degraded`. A deployment
answered `{"status":"ok"}` here while every authenticated route returned 500,
because `AUTH_MODE=entra` was set and `OIDC_AUTHORITY` was not — so the probe was
telling the truth about the process and nothing useful about the service.
"""

from __future__ import annotations

from typing import cast

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
            # **Pinned off the repository's `.env`, and this test is why**
            # (B-032's family). `llm_models={}` cannot override a populated
            # value from the env file: pydantic-settings *deep-merges* dicts,
            # so an empty one adds nothing and the developer's real model map
            # survives. The test then passes in CI, where there is no `.env`,
            # and fails on the machine that wrote it.
            _env_file=None,  # pyright: ignore[reportCallIssue]
            auth_mode="entra",
            oidc_authority=None,
            secrets_backend="local",
            local_secrets_key=SecretStr("x"),
            llm_models={"openai": {"small": "s", "mid": "m", "strong": "l"}},
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

    assert set(body) == {
        "status",
        "version",
        "git_sha",
        "missing_settings",
        "unservable_roles",
    }
    assert all(name.isupper() for name in body["missing_settings"])


async def test_a_degraded_probe_names_variables_and_never_values() -> None:
    """The leak this could have been. Everything reported is a name."""
    app = create_app(
        settings=Settings(  # pyright: ignore[reportArgumentType]
            # **Pinned off the repository's `.env`, and this test is why**
            # (B-032's family). `llm_models={}` cannot override a populated
            # value from the env file: pydantic-settings *deep-merges* dicts,
            # so an empty one adds nothing and the developer's real model map
            # survives. The test then passes in CI, where there is no `.env`,
            # and fails on the machine that wrote it.
            _env_file=None,  # pyright: ignore[reportCallIssue]
            auth_mode="entra",
            oidc_authority=None,
            secrets_backend="keyvault",
            key_vault_url=None,
            artifacts_backend="blob",
            artifacts_account_url=None,
            git_sha="testsha",
            llm_models={"openai": {"small": "s", "mid": "m", "strong": "l"}},
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


async def test_healthz_is_degraded_when_no_model_can_serve_a_run() -> None:
    """**The case that shipped after the one above** (**B-126**).

    `apps.bicep` gave the deployed API `OPENAI_API_KEY` and no `LLM_MODELS`, so
    `llm_providers` fell to its default and `llm_models` to `{}`. Every mode was
    satisfied, this probe reported `ok`, and every question a person asked ended
    as `failed`. The probe was correct about what it covered and that was the
    whole problem.

    Note what makes it degraded: not that `LLM_MODELS` is unset — it is `{}`
    here, which is set — but that it names no models for a provider
    `LLM_PROVIDERS` does name. A presence check would pass this configuration.
    """
    app = create_app(
        settings=Settings(  # pyright: ignore[reportArgumentType]
            # **Pinned off the repository's `.env`, and this test is why**
            # (B-032's family). `llm_models={}` cannot override a populated
            # value from the env file: pydantic-settings *deep-merges* dicts,
            # so an empty one adds nothing and the developer's real model map
            # survives. The test then passes in CI, where there is no `.env`,
            # and fails on the machine that wrote it.
            _env_file=None,  # pyright: ignore[reportCallIssue]
            auth_mode="entra",
            oidc_authority="https://x.ciamlogin.com/t/v2.0",
            secrets_backend="local",
            local_secrets_key=SecretStr("x"),
            llm_providers=("openai",),
            llm_models={},
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        response = await ac.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["missing_settings"] == ["LLM_MODELS"]


async def test_healthz_is_degraded_when_one_provider_of_several_has_no_models() -> None:
    """The fallback chain is the half a presence check cannot see at all.

    `LLM_MODELS` is populated, non-empty and satisfies the primary provider. The
    second is the one WP6.2 walks to during an incident — the worst possible
    moment to discover it resolves to nothing.
    """
    app = create_app(
        settings=Settings(  # pyright: ignore[reportArgumentType]
            # **Pinned off the repository's `.env`, and this test is why**
            # (B-032's family). `llm_models={}` cannot override a populated
            # value from the env file: pydantic-settings *deep-merges* dicts,
            # so an empty one adds nothing and the developer's real model map
            # survives. The test then passes in CI, where there is no `.env`,
            # and fails on the machine that wrote it.
            _env_file=None,  # pyright: ignore[reportCallIssue]
            auth_mode="entra",
            oidc_authority="https://x.ciamlogin.com/t/v2.0",
            secrets_backend="local",
            local_secrets_key=SecretStr("x"),
            llm_providers=("openai", "anthropic"),
            llm_models={"openai": {"small": "s", "mid": "m", "strong": "l"}},
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        body = (await ac.get("/healthz")).json()

    assert body["status"] == "degraded"
    assert body["missing_settings"] == ["LLM_MODELS"]


# ---------------------------------------------------------------------------
# A role that resolves to no model (B-154)
# ---------------------------------------------------------------------------


def _settings(**overrides: object) -> Settings:
    """A servable configuration, pinned off the repository's `.env`.

    `_env_file=None` for the reason the degraded test above records: pydantic
    deep-merges dicts, so a model map written here would be *added to* the
    developer's real one and the test would pass in CI and fail on the machine
    that wrote it.
    """
    base: dict[str, object] = {
        "_env_file": None,
        # `dev`, so `missing_for_mode` is empty and the probe reaches the role
        # check at all: it skips the second question when the first has already
        # failed, and a fixture that left `OIDC_AUTHORITY` missing would test the
        # short-circuit rather than the thing these cases are about.
        "auth_mode": "dev",
        "secrets_backend": "local",
        "local_secrets_key": SecretStr("x"),
        "llm_providers": ("openai",),
        "llm_models": {"openai": {"small": "s", "strong": "l"}},
    }
    base.update(overrides)
    return Settings(**base)  # pyright: ignore[reportArgumentType, reportCallIssue]


async def _probe(settings: Settings) -> dict[str, object]:
    app = create_app(settings=settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        body: dict[str, object] = (await ac.get("/healthz")).json()
        return body


def _unservable(body: dict[str, object]) -> list[str]:
    """The probe's role failures, typed once rather than cast in every case."""
    return cast(list[str], body["unservable_roles"])


async def test_a_role_mapped_to_a_tier_no_model_fills_is_degraded() -> None:
    """**The hole that made removing an unused model unsafe.**

    `missing_for_mode` asks whether `LLM_MODELS` names *any* model per provider —
    `registry.resolve`'s first two refusals, not its third. So a deployment can
    name `small` and `strong`, satisfy the probe, map a role to `mid`, and die at
    the first model call of the first question while reporting `ok`.
    """
    body = await _probe(_settings(llm_role_map={"compose": "mid"}))

    assert body["status"] == "degraded"
    unservable = _unservable(body)
    assert len(unservable) == 1
    assert unservable[0].startswith("compose:")
    # It says which tier is missing, because that is the fix.
    assert "mid" in unservable[0]


async def test_dropping_a_model_no_role_calls_leaves_the_probe_healthy() -> None:
    """**And this is what makes the removal a tidy rather than a trap** (B-154).

    `gpt-5.6-terra` sat in dev's `LLM_MODELS` and `LLM_PRICES` called by no role:
    `mid` is the default tier for `compose` alone, and dev overrides `compose` to
    `small`. Removing it must not quietly break a deployment, and the way to know
    is that every role still resolves — which is the assertion, rather than a
    reading of the role map by eye.
    """
    body = await _probe(_settings(llm_role_map={"compose": "small"}))

    assert body["status"] == "ok"
    assert body["unservable_roles"] == []


async def test_dropping_mid_is_safe_only_while_compose_is_mapped_off_it() -> None:
    """**The coupling the removal rests on, asserted rather than assumed.**

    `compose` is the one role whose *default* tier is `mid`, so a deployment with
    no `mid` model is broken unless `LLM_ROLE_MAP` moves it. dev does move it —
    to `small`, the owner's cost decision of 2026-08-24 — and that, not the
    absence of a caller, is what makes dropping `gpt-5.6-terra` safe.

    Both halves are here because the second is the one that bites: delete the
    override in `infra/modules/apps.bicep` and the deployment stops being able to
    compose an answer. The probe now says so at boot instead of the first
    question of the day failing at its last step.
    """
    with_override = await _probe(_settings(llm_role_map={"compose": "small"}))
    assert with_override["unservable_roles"] == []
    assert with_override["status"] == "ok"

    without_override = await _probe(_settings())
    assert [entry.split(":")[0] for entry in _unservable(without_override)] == ["compose"], (
        "compose is the only role defaulting to `mid`; if another one now does, "
        "the deployment that dropped `mid` needs to know before it ships"
    )


async def test_the_probe_names_roles_and_tiers_but_never_a_credential() -> None:
    """The rule `missing_settings` follows, extended to the new field.

    A probe is unauthenticated. It may say *which* role cannot be served and
    *which* tier is missing, because both are configuration a reader can act on;
    it may never carry a key, a host or a tenant.
    """
    body = await _probe(
        _settings(llm_role_map={"sql": "mid"}, local_secrets_key=SecretStr("super-secret-value"))
    )

    assert "super-secret-value" not in str(body)
    assert _unservable(body)[0].startswith("sql:")
