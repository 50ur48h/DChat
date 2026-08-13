"""The role matrix, asserted route by route and snapshotted.

Architecture Part 6.2 states the matrix in prose. This asserts it against the
real routes, and then writes what it observed to a committed snapshot — so a
change to who can do what shows up as a diff in review rather than as a surprise
in production. Widening access accidentally is the failure this exists to catch.

Every route the API exposes must appear here. A new org-scoped route with no
entry fails ``test_every_org_route_is_covered``, which is what stops the matrix
quietly falling behind the surface it describes.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dataagent.auth.jwt_validator import TokenValidator
from dataagent.auth.principal import Principal, TokenError
from dataagent.config import Settings
from dataagent.db import engine as engine_module
from dataagent.main import create_app
from dataagent.secrets.local import LocalSecretsProvider
from dataagent.tenancy import session as session_module

SNAPSHOT = Path(__file__).with_name("role_matrix.json")

ROLES = ("admin", "contributor", "reader")

#: Routes that are not org-scoped, and so are not part of this matrix. Listed
#: explicitly rather than pattern-matched: an unlisted route is a route nobody
#: thought about.
NOT_ORG_SCOPED = {
    ("GET", "/healthz"),
    ("GET", "/v1/me"),
    ("POST", "/v1/orgs"),
    ("POST", "/v1/invitations/accept"),
    ("GET", "/dev/token"),
    ("GET", "/dev/jwks.json"),
    ("GET", "/dev/.well-known/openid-configuration"),
}

#: One representative request per org-scoped route. Bodies are valid, so a 4xx
#: can only be an authorization decision and never a validation error.
#:
#: Order matters for the data-source probes: each probe runs as admin first, so a
#: DELETE placed before them would leave the later ones probing a row that no
#: longer exists, and a 404 would be recorded where the matrix means "allow".
PROBES: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("GET", "/v1/orgs/{org_id}/members", None),
    ("PATCH", "/v1/orgs/{org_id}/members/{user_id}", {"role": "reader"}),
    ("DELETE", "/v1/orgs/{org_id}/members/{user_id}", None),
    (
        "POST",
        "/v1/orgs/{org_id}/invitations",
        {"email": "invitee@example.com", "role": "reader"},
    ),
    ("GET", "/v1/orgs/{org_id}/data-sources", None),
    (
        "POST",
        "/v1/orgs/{org_id}/data-sources",
        {
            "name": "Probe",
            "engine": "pg",
            "host": "127.0.0.1",
            "port": 5432,
            "database": "probe",
            "username": "probe_user",
            "password": "probe-password",
        },
    ),
    ("GET", "/v1/orgs/{org_id}/data-sources/{data_source_id}", None),
    ("PATCH", "/v1/orgs/{org_id}/data-sources/{data_source_id}", {"name": "Probed"}),
    # Port 1 on loopback: refused immediately on every platform, so the probe
    # answers "not reachable" in microseconds. The matrix cares that the route
    # answered at all, not what it found.
    ("POST", "/v1/orgs/{org_id}/data-sources/{data_source_id}/test", None),
    # Reading a catalog is member work; building one reaches out to a customer's
    # database, so it is Contributor-or-Admin. The probe's data source is never
    # verified read-only, so a refresh declines before it opens a socket — which
    # is a 200 with `changed: false`, and exactly what the matrix wants to see:
    # the route answered, and the decision was about the role.
    ("GET", "/v1/orgs/{org_id}/data-sources/{data_source_id}/catalog", None),
    ("POST", "/v1/orgs/{org_id}/data-sources/{data_source_id}/refresh", None),
    # Profiling reads a customer's rows, so it sits with refresh: Contributor or
    # Admin. Deciding what may be seen is Admin alone.
    ("POST", "/v1/orgs/{org_id}/data-sources/{data_source_id}/profile", None),
    (
        "PATCH",
        "/v1/orgs/{org_id}/data-sources/{data_source_id}/columns/{column_id}/policy",
        {"policy": "mask", "reason": "probe"},
    ),
    # Searching reads cards, whose examples were masked before they were stored,
    # so it is member work like browsing.
    ("GET", "/v1/orgs/{org_id}/catalog/search", None),
    ("DELETE", "/v1/orgs/{org_id}/data-sources/{data_source_id}", None),
)


#: Query strings for routes that require one. Without it the probe earns a 422
#: from validation, and the matrix would record a validation error as though it
#: were an authorization decision — which is the one thing this file is for.
QUERIES: dict[str, str] = {"/v1/orgs/{org_id}/catalog/search": "?q=orders"}


class _SubjectAsToken(TokenValidator):
    def __init__(self) -> None:
        pass

    async def validate(self, token: str) -> Principal:
        if not token:
            raise TokenError("malformed", "nope")
        return Principal(subject=token, email=f"{token}@example.com")


@dataclass(frozen=True)
class Matrix:
    """One organization, a member of every role, and something to act upon."""

    app: FastAPI
    org_id: uuid.UUID
    users: dict[str, uuid.UUID]
    data_source_id: uuid.UUID
    column_id: uuid.UUID


@pytest.fixture
async def matrix_app(
    app_database: URL,
    migrated_database: URL,
    secrets_provider: LocalSecretsProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Matrix]:
    """One organization holding a member of every role, plus a spare target."""
    owner = create_async_engine(migrated_database)
    app_engine = create_async_engine(app_database)
    monkeypatch.setattr(engine_module, "get_engine", lambda: owner)
    monkeypatch.setattr(
        session_module,
        "_session_factory",
        lambda: async_sessionmaker(app_engine, expire_on_commit=False, autoflush=False),
    )

    org_id = uuid.uuid4()
    data_source_id = uuid.uuid4()
    column_id = uuid.uuid4()
    users: dict[str, uuid.UUID] = {}
    async with owner.begin() as connection:
        await connection.execute(
            text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
        )
        await connection.execute(
            text("INSERT INTO organizations (id, name) VALUES (:id, 'Matrix')"), {"id": org_id}
        )
        # Two admins, so that a PATCH or DELETE aimed at one is refused on role
        # grounds only — never by the last-admin rule, which would look identical.
        for subject, role in (
            ("admin", "admin"),
            ("spare-admin", "admin"),
            ("contributor", "contributor"),
            ("reader", "reader"),
        ):
            user_id = uuid.uuid4()
            users[subject] = user_id
            await connection.execute(
                text("INSERT INTO users (id, external_subject, email) VALUES (:i, :s, :e)"),
                {"i": user_id, "s": subject, "e": f"{subject}@example.com"},
            )
            await connection.execute(
                text("INSERT INTO org_memberships (org_id, user_id, role) VALUES (:o, :u, :r)"),
                {"o": org_id, "u": user_id, "r": role},
            )
        # Port 1 is refused instantly, so the /test probe never waits on a socket.
        await connection.execute(
            text(
                "INSERT INTO data_sources (id, org_id, name, engine, host_display, "
                "settings, secret_ref) VALUES (:i, :o, 'Matrix source', 'pg', "
                "'127.0.0.1:1/probe', :s, :r)"
            ),
            {
                "i": data_source_id,
                "o": org_id,
                "s": '{"host": "127.0.0.1", "port": 1, "database": "probe", '
                '"username_last4": "obe"}',
                "r": f"ds/{org_id}/{data_source_id}/credentials",
            },
        )
        # A catalog with one column in it, written directly rather than
        # discovered: without one the browse probe answers 404 for every role and
        # the matrix would record "deny(404)" three times — which reads like
        # nobody may see a catalog, when what it means is that there was not one
        # to see. The column is what the policy probe patches.
        snapshot_id = uuid.uuid4()
        table_id = uuid.uuid4()
        await connection.execute(
            text(
                "INSERT INTO catalog_snapshots (id, org_id, data_source_id, version, status) "
                "VALUES (:i, :o, :d, 1, 'active')"
            ),
            {"i": snapshot_id, "o": org_id, "d": data_source_id},
        )
        await connection.execute(
            text(
                "INSERT INTO catalog_tables (id, org_id, snapshot_id, schema_name, table_name, "
                "kind, structural_hash) VALUES (:i, :o, :s, 'public', 'probe', 'table', :h)"
            ),
            {"i": table_id, "o": org_id, "s": snapshot_id, "h": uuid.uuid4().hex},
        )
        await connection.execute(
            text(
                "INSERT INTO catalog_columns (id, org_id, table_id, name, ordinal, data_type, "
                "nullable) VALUES (:i, :o, :t, 'probe_column', 1, 'text', true)"
            ),
            {"i": column_id, "o": org_id, "t": table_id},
        )

    app = create_app(settings=Settings(auth_mode="dev", env="ci", build_env="dev"))
    app.state.token_validator = _SubjectAsToken()

    try:
        yield Matrix(
            app=app,
            org_id=org_id,
            users=users,
            data_source_id=data_source_id,
            column_id=column_id,
        )
    finally:
        await owner.dispose()
        await app_engine.dispose()


async def _probe(
    app: FastAPI, method: str, path: str, who: str, body: dict[str, Any] | None
) -> int:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.request(
            method, path, headers={"Authorization": f"Bearer {who}"}, json=body
        )
    return response.status_code


async def test_the_role_matrix_matches_its_snapshot(matrix_app: Matrix) -> None:
    """Observe who may do what, then compare with the committed record."""
    observed: dict[str, dict[str, str]] = {}
    for method, template, body in PROBES:
        path = template.format(
            org_id=matrix_app.org_id,
            user_id=matrix_app.users["spare-admin"],
            data_source_id=matrix_app.data_source_id,
            column_id=matrix_app.column_id,
        ) + QUERIES.get(template, "")
        key = f"{method} {template}"
        observed[key] = {}
        for role in ROLES:
            status = await _probe(matrix_app.app, method, path, role, body)
            observed[key][role] = "allow" if status < 400 else f"deny({status})"

    rendered = json.dumps(observed, indent=2, sort_keys=True) + "\n"

    if not SNAPSHOT.exists():  # pragma: no cover - first run only
        SNAPSHOT.write_text(rendered, encoding="utf-8", newline="")
        pytest.fail(f"wrote a new snapshot to {SNAPSHOT.name}; review it and re-run")

    expected = SNAPSHOT.read_text(encoding="utf-8")
    assert rendered == expected, (
        "the role matrix changed. If that was intended, update "
        f"{SNAPSHOT.name} in this PR so the change is reviewed."
    )


async def test_the_snapshot_says_what_the_architecture_says(matrix_app: Matrix) -> None:
    """A snapshot only proves stability. This proves it is the *right* matrix.

    Architecture Part 6.2: managing members and data sources is Admin-only;
    everyone may read.
    """
    recorded = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    for readable in (
        "GET /v1/orgs/{org_id}/members",
        "GET /v1/orgs/{org_id}/data-sources",
        "GET /v1/orgs/{org_id}/data-sources/{data_source_id}",
    ):
        assert recorded[readable] == {
            "admin": "allow",
            "contributor": "allow",
            "reader": "allow",
        }
    for admin_only in (
        "PATCH /v1/orgs/{org_id}/members/{user_id}",
        "DELETE /v1/orgs/{org_id}/members/{user_id}",
        "POST /v1/orgs/{org_id}/invitations",
        "POST /v1/orgs/{org_id}/data-sources",
        "PATCH /v1/orgs/{org_id}/data-sources/{data_source_id}",
        "DELETE /v1/orgs/{org_id}/data-sources/{data_source_id}",
        "POST /v1/orgs/{org_id}/data-sources/{data_source_id}/test",
    ):
        assert recorded[admin_only]["admin"] == "allow"
        assert recorded[admin_only]["contributor"].startswith("deny")
        assert recorded[admin_only]["reader"].startswith("deny")


def test_every_org_route_is_covered() -> None:
    """A new org-scoped route with no probe is a role nobody decided on."""
    app = create_app(settings=Settings(auth_mode="dev", env="ci", build_env="dev"))

    # Read the OpenAPI schema rather than walking app.routes: this FastAPI nests
    # included routers instead of flattening them, and the schema is the stable
    # public description of what the API actually exposes.
    paths = app.openapi()["paths"]
    exposed = {
        (method.upper(), path)
        for path, operations in paths.items()
        for method in operations
        if method.upper() != "HEAD"
    }
    probed = {(method, template) for method, template, _ in PROBES}
    org_scoped = {
        (method, path)
        for method, path in exposed
        if "{org_id}" in path and (method, path) not in NOT_ORG_SCOPED
    }

    assert org_scoped == probed, (
        "org-scoped routes and the role matrix disagree. Unprobed routes: "
        f"{sorted(org_scoped - probed)}; probes for routes that no longer exist: "
        f"{sorted(probed - org_scoped)}"
    )
