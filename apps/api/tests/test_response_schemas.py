"""No response model may carry a credential.

Plan WP3.1 asks for a "grep-style test asserting no response schema exposes
credential fields". This is that test, done through the OpenAPI document rather
than through grep, so it sees what the API actually promises to return — every
response of every route, following ``$ref`` through nested models.

Request models are deliberately out of scope: ``CreateDataSourceIn`` *must* have a
password field. The asymmetry is the whole point — credentials go in and never
come back out.
"""

from __future__ import annotations

from typing import Any, cast

from dataagent.config import Settings
from dataagent.main import create_app

#: Substrings that make a field name suspicious. Deliberately broad: the cost of
#: a false positive is one line in the allowlist below, reviewed once.
CREDENTIAL_WORDS = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "credential",
    "token",
    "apikey",
    "api_key",
    "dsn",
    "connection_string",
    "conn_str",
)

#: Fields that look like credentials and are not, each with the reason it is
#: allowed to exist. Adding a line here is a decision someone must argue for in
#: review — which is exactly the friction this test is for.
ALLOWED: dict[tuple[str, str], str] = {
    ("InvitationOut", "token"): (
        "The invitation token, shown once at creation and stored only as a hash. "
        "Withholding it would make invitations unusable."
    ),
    ("ArmedRecoveryOut", "token"): (
        "The recovery token (B-017), shown once when an Admin arms a grant and "
        "stored only as a SHA-256 hash — the same trade as an invitation, and "
        "withholding it would make the grant unusable, which is the whole "
        "feature. It appears on this model alone: `RecoveryGrantOut`, which is "
        "what the listing and the revoke route return, deliberately has no such "
        "field, so the credential exists in exactly one response in the API and "
        "only at the moment of creation."
    ),
    ("DataSourceOut", "secret_ref"): (
        "A pointer to the credential, not the credential. Useless without the "
        "secrets backend it names (architecture Part 7.3)."
    ),
}


def _referenced(schema: Any, spec: Any, seen: set[str]) -> set[str]:
    """Every component schema reachable from ``schema``, transitively."""
    names: set[str] = set()

    if isinstance(schema, list):
        for item in cast("list[Any]", schema):
            names |= _referenced(item, spec, seen)
        return names

    if not isinstance(schema, dict):
        return names

    node = cast("dict[str, Any]", schema)
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        name = ref.rsplit("/", 1)[-1]
        if name not in seen:
            seen.add(name)
            names.add(name)
            names |= _referenced(spec["components"]["schemas"].get(name, {}), spec, seen)
        return names

    for value in node.values():
        names |= _referenced(value, spec, seen)
    return names


def _response_schemas(spec: Any) -> set[str]:
    seen: set[str] = set()
    names: set[str] = set()
    for operations in spec["paths"].values():
        for operation in operations.values():
            for response in operation.get("responses", {}).values():
                names |= _referenced(response.get("content", {}), spec, seen)
    return names


def test_no_response_model_exposes_a_credential() -> None:
    spec = create_app(settings=Settings(auth_mode="dev", env="ci", build_env="dev")).openapi()
    schemas = spec["components"]["schemas"]

    offenders: list[str] = []
    for name in sorted(_response_schemas(spec)):
        for field in schemas[name].get("properties", {}):
            if (name, field) in ALLOWED:
                continue
            if any(word in field.lower() for word in CREDENTIAL_WORDS):
                offenders.append(f"{name}.{field}")

    assert offenders == [], (
        f"these response fields look like credentials: {offenders}. Responses must "
        "never carry one (architecture Part 7.3). If a field is genuinely safe, "
        "add it to ALLOWED here with the reason."
    )


def test_the_data_source_response_has_no_field_a_credential_could_hide_in() -> None:
    """Named explicitly, because this is the model the M3 gate is about."""
    spec = create_app(settings=Settings(auth_mode="dev", env="ci", build_env="dev")).openapi()

    fields = set(spec["components"]["schemas"]["DataSourceOut"]["properties"])

    assert "password" not in fields
    assert "username" not in fields, "the full username is not ours to hand back"
    assert {"secret_ref", "username_last4"} <= fields


def test_the_guard_would_actually_catch_one() -> None:
    """A guard that has never failed is a guard nobody has tested."""
    spec: dict[str, Any] = {
        "paths": {
            "/leak": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Leaky"}
                                }
                            }
                        }
                    }
                }
            }
        },
        "components": {"schemas": {"Leaky": {"properties": {"password": {"type": "string"}}}}},
    }

    reachable = _response_schemas(spec)

    assert reachable == {"Leaky"}
    assert any(
        word in field
        for field in spec["components"]["schemas"]["Leaky"]["properties"]
        for word in CREDENTIAL_WORDS
    )
