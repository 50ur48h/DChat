"""What a mode requires, declared once and checked in both directions.

**The guard was a record, not a detector.** `scripts/check_env.sh` compared the
deployment templates against a list of couplings — `SECRETS_BACKEND=keyvault`
needs `KEY_VAULT_URL`, `ARTIFACTS_BACKEND=blob` needs an account URL,
`AUTH_MODE=entra` needs an authority — and every one of those three entries was
added *after* a deployment had already failed on it. A list of past failures
cannot catch the next omission, which is the only thing worth catching.

`MODE_REQUIREMENTS` in `config.py` inverts it: the declaration lives beside the
settings it constrains, derived from what the application refuses to run without.
`check_env.sh` keeps a copy because it is POSIX shell and cannot import Python,
and this file is what makes the copy true — the same arrangement `TENANT_TABLES`
has with revision 0002, and for the same reason: two lists that must agree, in
different languages, with something checking.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import SecretStr

from dataagent.config import MODE_REQUIREMENTS, RUN_REQUIRED, Settings

CHECK_ENV = Path(__file__).resolve().parents[3] / "scripts" / "check_env.sh"


def _shell_table() -> set[tuple[str, str, str]]:
    """`KEY | VALUE | COMPANION` rows out of BACKEND_REQUIRES."""
    text = CHECK_ENV.read_text(encoding="utf-8")
    block = re.search(r"readonly BACKEND_REQUIRES='\n(.*?)\n'", text, re.S)
    assert block, "BACKEND_REQUIRES not found in check_env.sh"
    rows: set[tuple[str, str, str]] = set()
    for line in block.group(1).splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split("|")]
        assert len(parts) == 3, f"malformed row: {line!r}"
        rows.add((parts[0], parts[1], parts[2]))
    return rows


def _python_table() -> set[tuple[str, str, str]]:
    return {
        (field.upper(), value, required.upper())
        for (field, value), names in MODE_REQUIREMENTS.items()
        for required in names
    }


def test_the_shell_copy_matches_the_declaration() -> None:
    """Drift here means the templates stop being checked for a mode, silently.

    Only the entries a *deployment template* can set are compared:
    `SECRETS_BACKEND=local` requires `LOCAL_SECRETS_KEY`, and no template sets
    that mode — production refuses it outright — so the shell has no row for it.
    """
    deployable = {row for row in _python_table() if row[1] != "local"}

    assert deployable == _shell_table(), (
        "MODE_REQUIREMENTS and check_env.sh's BACKEND_REQUIRES disagree. Add the "
        "row to the shell table so the deployment templates are checked for it — "
        "otherwise a mode can be selected in Bicep with nothing to satisfy it, "
        "which is exactly how AUTH_MODE=entra shipped without OIDC_AUTHORITY."
    )


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"auth_mode": "entra", "oidc_authority": None}, ["OIDC_AUTHORITY"]),
        ({"auth_mode": "entra", "oidc_authority": "  "}, ["OIDC_AUTHORITY"]),
        ({"auth_mode": "entra", "oidc_authority": "https://x/v2.0"}, []),
        ({"secrets_backend": "keyvault", "key_vault_url": None}, ["KEY_VAULT_URL"]),
        (
            {"secrets_backend": "keyvault", "key_vault_url": "https://v.vault.azure.net/"},
            [],
        ),
        (
            {"artifacts_backend": "blob", "artifacts_account_url": None},
            ["ARTIFACTS_ACCOUNT_URL"],
        ),
    ],
)
def test_missing_for_mode_names_what_is_missing(
    overrides: dict[str, object], expected: list[str]
) -> None:
    """A blank string counts as missing: `OIDC_AUTHORITY=` in a template is not a
    value, and treating it as one is how an empty variable reaches production."""
    base: dict[str, object] = {
        # Pinned off the repository's `.env`: pydantic-settings deep-merges dict
        # fields, so `llm_models={}` cannot override a populated one from the env
        # file — it adds nothing and the developer's map survives (B-032).
        "_env_file": None,
        "auth_mode": "dev",
        "secrets_backend": "local",
        "local_secrets_key": SecretStr("x"),
        "artifacts_backend": "local",
        "oidc_authority": None,
        "key_vault_url": None,
        "artifacts_account_url": None,
        # Servable, so each case below isolates the one thing it varies. Without
        # this every expectation would also carry LLM_MODELS (B-126).
        "llm_models": {"openai": {"small": "s", "mid": "m", "strong": "l"}},
    }
    settings = Settings(**(base | overrides))  # pyright: ignore[reportArgumentType]

    assert settings.missing_for_mode() == expected


def test_a_complete_configuration_is_not_degraded() -> None:
    """The control. Without it every test above passes against a method that
    always reports something missing."""
    settings = Settings(  # pyright: ignore[reportArgumentType]
        _env_file=None,  # pyright: ignore[reportCallIssue]
        auth_mode="entra",
        oidc_authority="https://x.ciamlogin.com/t/v2.0",
        secrets_backend="keyvault",
        key_vault_url="https://v.vault.azure.net/",
        artifacts_backend="blob",
        artifacts_account_url="https://s.blob.core.windows.net/",
        llm_models={"openai": {"small": "s", "mid": "m", "strong": "l"}},
    )

    assert settings.missing_for_mode() == []


# --- RUN_REQUIRED: what no mode makes optional -------------------------------


def _shell_run_required() -> set[str]:
    """`KEY | why` rows out of check_env.sh's RUN_REQUIRED."""
    text = CHECK_ENV.read_text(encoding="utf-8")
    block = re.search(r"readonly RUN_REQUIRED='\n(.*?)\n'", text, re.S)
    assert block, "RUN_REQUIRED not found in check_env.sh"
    keys: set[str] = set()
    for line in block.group(1).splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split("|")]
        assert len(parts) == 2, f"malformed row: {line!r}"
        assert parts[1], f"{parts[0]} has no reason beside it"
        keys.add(parts[0])
    return keys


def test_the_shell_copy_of_run_required_matches_the_declaration() -> None:
    """The second copy, kept true the same way the first one is.

    Drift here is silent in the direction that matters: the shell forgetting a
    name means the templates stop being checked for it, and B-126 is what that
    costs — a deployment that passes every check, boots, and fails every question.
    """
    assert {name.upper() for name in RUN_REQUIRED} == _shell_run_required(), (
        "RUN_REQUIRED and check_env.sh's copy disagree. Add the row to the shell "
        "table with the reason beside it, so the deployment templates are checked "
        "for it."
    )


def test_no_name_is_required_both_always_and_conditionally() -> None:
    """The control on the split between the two tables.

    They answer different questions — "this mode needs that" versus "everything
    needs this" — and a name in both would mean one of the two is wrong about
    when it applies.
    """
    conditional = {name.upper() for names in MODE_REQUIREMENTS.values() for name in names}

    assert not (conditional & {name.upper() for name in RUN_REQUIRED})


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        # The configuration that shipped: a credential and no model map.
        ({"llm_providers": ("openai",), "llm_models": {}}, ["LLM_MODELS"]),
        # Set, non-empty, and about a provider nobody configured. A presence
        # check passes this; `registry.resolve` does not.
        (
            {"llm_providers": ("openai",), "llm_models": {"anthropic": {"small": "s"}}},
            ["LLM_MODELS"],
        ),
        # The fallback chain, which only matters during an incident.
        (
            {
                "llm_providers": ("openai", "anthropic"),
                "llm_models": {"openai": {"small": "s"}},
            },
            ["LLM_MODELS"],
        ),
        # Empty chain: nothing can serve any role, and the name to go and set is
        # the chain rather than the map.
        ({"llm_providers": (), "llm_models": {}}, ["LLM_PROVIDERS"]),
        # The control. Every provider named has models, so nothing is reported.
        (
            {
                "llm_providers": ("openai",),
                "llm_models": {"openai": {"small": "s", "mid": "m", "strong": "l"}},
            },
            [],
        ),
    ],
)
def test_missing_for_mode_covers_the_model_configuration(
    overrides: dict[str, object], expected: list[str]
) -> None:
    """What `/healthz` reports `degraded` from, at the level below the route."""
    base: dict[str, object] = {
        # Pinned off the repository's `.env`: pydantic-settings deep-merges dict
        # fields, so `llm_models={}` cannot override a populated one from the env
        # file — it adds nothing and the developer's map survives (B-032).
        "_env_file": None,
        "auth_mode": "dev",
        "secrets_backend": "local",
        "local_secrets_key": SecretStr("x"),
        "artifacts_backend": "local",
    }
    settings = Settings(**(base | overrides))  # pyright: ignore[reportArgumentType]

    assert settings.missing_for_mode() == expected
