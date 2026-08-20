"""The scripted provider, and the two guards that keep it out of production.

**This is the highest-consequence file in WP11.2b.** The work package ships a
provider that answers questions without a model behind it, inside the ordinary
image, selectable by an environment variable. `ProviderCaps.is_stub` states the
hazard plainly: a stub reaching production would not fail, it would fabricate —
confidently, in a product whose entire claim is that its answers are evidenced.

So the guards are tested by **exercising them**, not by describing them. The
central test boots a real application with production settings and the scripted
provider selected, and requires the boot to fail. An assertion written into a
docstring is not a guard; a test that constructs a `Settings` and calls the
checker by hand is only slightly better, because it proves the function works
and never that anything calls it.
"""

from __future__ import annotations

import uuid

import pytest

from dataagent.config import Settings
from dataagent.llm import registry
from dataagent.llm.base import LLMRequest, Message, Tags
from dataagent.llm.registry import STUB_PROVIDERS, ProviderNotConfiguredError, get_provider
from dataagent.llm.scripted import PROVIDER_NAME, SCRIPTED_SQL, ScriptedProvider
from dataagent.main import create_app


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "auth_mode": "entra",
        "secrets_backend": "keyvault",
        "oidc_authority": "https://login.example.com/tenant/v2.0",
        "oidc_audience": "api://dataagent",
        "llm_providers": ("scripted",),
    }
    base.update(overrides)
    return Settings(**base)  # pyright: ignore[reportArgumentType]


def _request(role: str = "compose", prompt: str = "") -> LLMRequest:
    return LLMRequest(
        model="scripted-1",
        messages=[Message(role="user", content=prompt)],
        tags=Tags(org_id=uuid.uuid4(), role=role),  # pyright: ignore[reportArgumentType]
    )


# ---------------------------------------------------------------------------
# The guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "build_env"),
    [("prod", "dev"), ("local", "prod"), ("prod", "prod")],
)
def test_a_production_app_will_not_boot_with_the_scripted_provider(
    env: str, build_env: str
) -> None:
    """**The test the whole work package turns on.**

    A real `create_app` with production settings and `LLM_PROVIDERS=scripted`,
    required to raise. Not a hand-rolled call to the checker: that would prove
    the function refuses and say nothing about whether the boot path asks it,
    which is the half that actually protects anybody.

    Both halves of `is_production` are covered, because a dev image deployed to
    prod and a prod image with a careless ENV are the same mistake seen from
    opposite ends — `Settings.is_production` says so, and a guard that honoured
    only one of them would be a guard with a documented way around it.
    """
    settings = _settings(env=env, build_env=build_env)

    with pytest.raises(RuntimeError, match="answers without a model"):
        create_app(settings=settings)


def test_the_refusal_names_the_variable_and_why() -> None:
    """A refusal an operator cannot act on gets worked around at 3am."""
    settings = _settings(env="prod", build_env="prod")

    with pytest.raises(RuntimeError) as raised:
        create_app(settings=settings)

    message = str(raised.value)
    assert "LLM_PROVIDERS" in message, "it names the variable to change"
    assert "scripted" in message, "and which value is the problem"
    assert "evidenced" in message, "and why this is worse than an outage"


def test_a_non_production_app_boots_with_it() -> None:
    """The control. A guard that refused everywhere would be indistinguishable
    from one that works, and CI could not run the smoke this exists for."""
    app = create_app(settings=_settings(env="ci", build_env="dev", auth_mode="dev"))

    assert app is not None


def test_the_registry_still_refuses_a_stub_it_was_never_configured_for() -> None:
    """**The second guard, catching what the first cannot.**

    The boot check reads configuration, so it cannot see a provider handed to
    `register_provider` at runtime — which is how the test suite substitutes a
    fake, and how anything else could. `get_provider` reads the built instance's
    own capabilities instead, so a stub arriving by a route no configuration
    mentions is still refused.
    """
    # Registered under a name nothing else uses, and deliberately **not** cleaned
    # up afterwards. The obvious cleanup — `clear_provider_cache()` — evicts
    # providers other fixtures have already built, which is not hypothetical: it
    # made `test_front_door` fail in the same run and pass alone. What is left
    # behind is one unused factory in a dict, and `get_provider` raises here
    # before it caches anything, so no instance survives either.
    registry.register_provider("smuggled", ScriptedProvider)

    with pytest.raises(ProviderNotConfiguredError, match="answers without a model"):
        get_provider("smuggled", _settings(env="prod", build_env="prod"))


def test_the_scripted_provider_is_named_as_a_stub_in_both_places() -> None:
    """The two guards read different sources of truth, so they can disagree —
    and a `scripted` that the boot check did not know about would be a stub with
    a clear path into a production image."""
    assert PROVIDER_NAME in STUB_PROVIDERS
    assert ScriptedProvider().capabilities().is_stub is True


# ---------------------------------------------------------------------------
# What it actually answers
# ---------------------------------------------------------------------------


async def test_it_answers_every_role_the_loop_asks_of_it() -> None:
    """A role the smoke reaches and this does not answer is a smoke that fails
    in CI with a parse error rather than a useful sentence."""
    provider = ScriptedProvider()

    plan = await provider.complete(_request("sql"))
    reflect = await provider.complete(_request("plan"))
    critic = await provider.complete(_request("critic"))

    assert SCRIPTED_SQL in plan.text
    assert '"done": true' in reflect.text
    assert '"verdict": "pass"' in critic.text


async def test_composing_cites_the_execution_it_was_shown() -> None:
    """**The one piece of state it cannot know in advance.** An invented id is
    dropped by `state.add_finding`, so a provider that fabricated one would give
    a green smoke over a broken citation path — the exact class of false
    assurance this whole module has to avoid."""
    execution_id = str(uuid.uuid4())
    provider = ScriptedProvider()

    completion = await provider.complete(
        _request("compose", prompt=f"- {execution_id}: 1 row over columns order_count")
    )

    assert execution_id in completion.text


async def test_it_reports_no_usage_rather_than_inventing_a_price() -> None:
    """The meter writes every call to `usage_ledger`, so the row still shows a
    call happened. Pricing a fabricated one would put invented money in the table
    whose only job is to answer "what did this cost"."""
    completion = await ScriptedProvider().complete(_request("compose"))

    assert completion.usage.total_tokens == 0
