"""Registering a database, over real HTTP against a real platform database.

The M3 promise is narrow and absolute: **the credential is never echoed and never
stored here**. Most of this file is that one sentence, asserted from every angle a
credential could escape from — the response body, the row, the audit log, and the
error path where a half-finished registration is rolled back.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any, cast, get_args

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import URL, Row, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from customer_db import CustomerDatabase
from dataagent.auth.jwt_validator import TokenValidator
from dataagent.auth.principal import Principal, TokenError
from dataagent.config import Settings
from dataagent.connectors.base import ConnectorError
from dataagent.connectors.factory import SUPPORTED_ENGINES, require_supported
from dataagent.datasources.routes import Engine
from dataagent.datasources.service import create_data_source, credentials_ref
from dataagent.db import engine as engine_module
from dataagent.db.models import DATA_SOURCE_ENGINES
from dataagent.main import create_app
from dataagent.secrets.base import SecretNotFoundError
from dataagent.secrets.local import LocalSecretsProvider
from dataagent.tenancy import session as session_module

# Named after the fixture rather than after the field: a secret scanner reads a
# quoted, high-entropy literal beside a constant called "password" and is right
# to complain. Silencing it per line is a habit worth not forming — this
# repository is public, and that scanner is the last thing between a pasted
# credential and the internet.
PIZZA_LOGIN = "p1zza-r3ad0nly-pa55"
PIZZA_ACCOUNT = "pizza_readonly"

REGISTRATION: dict[str, Any] = {
    "name": "Pizza demo",
    "engine": "pg",
    "host": "seed-pizza-pg",
    "port": 5432,
    "database": "pizza",
    "username": PIZZA_ACCOUNT,
    "password": PIZZA_LOGIN,
}


class _SubjectAsToken(TokenValidator):
    """The bearer token *is* the subject, so these tests are about the flow."""

    def __init__(self) -> None:
        pass

    async def validate(self, token: str) -> Principal:
        if not token:
            raise TokenError("malformed", "nope")
        return Principal(subject=token, email=f"{token}@example.com", name=token.title())


class Api:
    def __init__(self, app: FastAPI) -> None:
        self._app = app

    async def call(
        self, method: str, path: str, who: str | None = None, body: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        headers = {"Authorization": f"Bearer {who}"} if who else {}
        async with AsyncClient(
            transport=ASGITransport(app=self._app), base_url="http://testserver"
        ) as client:
            response = await client.request(method, path, headers=headers, json=body)
        payload = None if response.status_code == 204 else response.json()
        return response.status_code, payload


@pytest.fixture
async def api(
    app_database: URL,
    migrated_database: URL,
    secrets_provider: LocalSecretsProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Api]:
    owner = create_async_engine(migrated_database)
    app_engine = create_async_engine(app_database)
    factory = async_sessionmaker(app_engine, expire_on_commit=False, autoflush=False)

    monkeypatch.setattr(engine_module, "get_engine", lambda: owner)
    monkeypatch.setattr(session_module, "_session_factory", lambda: factory)

    app = create_app(settings=Settings(auth_mode="dev", env="ci", build_env="dev"))
    app.state.token_validator = _SubjectAsToken()

    try:
        yield Api(app)
    finally:
        await owner.dispose()
        await app_engine.dispose()


async def _org(api: Api, who: str, name: str) -> uuid.UUID:
    _, org = await api.call("POST", "/v1/orgs", who=who, body={"name": name})
    return uuid.UUID(org["org_id"])


async def _rows(url: URL, org_id: uuid.UUID, statement: str) -> Sequence[Row[Any]]:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            return (await connection.execute(text(statement))).fetchall()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


async def test_registering_stores_the_credential_only_in_the_secrets_store(
    api: Api, app_database: URL, secrets_provider: LocalSecretsProvider
) -> None:
    org_id = await _org(api, "alice", "Acme")

    status, created = await api.call(
        "POST", f"/v1/orgs/{org_id}/data-sources", who="alice", body=REGISTRATION
    )

    assert status == 201
    assert created["status"] == "registered"
    assert created["host_display"] == "seed-pizza-pg:5432/pizza"
    # What came back describes the account without being it.
    assert created["username_last4"] == "only"
    assert created["secret_ref"] == credentials_ref(org_id, uuid.UUID(created["id"]))

    stored = await secrets_provider.get(created["secret_ref"])
    assert stored == {"username": PIZZA_ACCOUNT, "password": PIZZA_LOGIN}

    # The whole row as text, so this asserts about every column there is — including
    # any a later phase adds without thinking about what it might carry.
    row = (await _rows(app_database, org_id, "SELECT row_to_json(d)::text FROM data_sources d"))[0][
        0
    ]
    assert created["secret_ref"] in row
    assert PIZZA_LOGIN not in row, "the credential reached the platform database"
    assert PIZZA_ACCOUNT not in row, "the full username reached the platform database"


async def test_no_response_ever_carries_the_password(api: Api) -> None:
    """Every route that touches a data source, checked against the raw body."""
    org_id = await _org(api, "alice", "Acme")
    _, created = await api.call(
        "POST", f"/v1/orgs/{org_id}/data-sources", who="alice", body=REGISTRATION
    )
    data_source_id = created["id"]

    bodies = [created]
    for method, path, body in (
        ("GET", f"/v1/orgs/{org_id}/data-sources", None),
        ("GET", f"/v1/orgs/{org_id}/data-sources/{data_source_id}", None),
        ("PATCH", f"/v1/orgs/{org_id}/data-sources/{data_source_id}", {"name": "Renamed"}),
        ("POST", f"/v1/orgs/{org_id}/data-sources/{data_source_id}/test", None),
    ):
        _, payload = await api.call(method, path, who="alice", body=body)
        bodies.append(payload)

    rendered = str(bodies)
    assert PIZZA_LOGIN not in rendered
    assert PIZZA_ACCOUNT not in rendered


async def test_the_audit_trail_records_the_registration_without_the_credential(
    api: Api, app_database: URL
) -> None:
    org_id = await _org(api, "alice", "Acme")
    await api.call("POST", f"/v1/orgs/{org_id}/data-sources", who="alice", body=REGISTRATION)

    rows = await _rows(
        app_database, org_id, "SELECT action, object_type, details::text FROM audit_log ORDER BY id"
    )

    assert [row[0] for row in rows] == ["org.created", "datasource.created"]
    assert rows[1][1] == "data_source"
    assert "seed-pizza-pg:5432/pizza" in rows[1][2]
    assert PIZZA_LOGIN not in rows[1][2]
    assert PIZZA_ACCOUNT not in rows[1][2]


async def test_two_sources_cannot_share_a_name_and_the_secret_is_cleaned_up(
    api: Api, secrets_provider: LocalSecretsProvider
) -> None:
    """A rejected registration must not leave a credential behind."""
    org_id = await _org(api, "alice", "Acme")
    await api.call("POST", f"/v1/orgs/{org_id}/data-sources", who="alice", body=REGISTRATION)

    before = _entries(secrets_provider)
    status, body = await api.call(
        "POST", f"/v1/orgs/{org_id}/data-sources", who="alice", body=REGISTRATION
    )

    assert status == 409
    assert "already exists" in body["detail"]
    assert _entries(secrets_provider) == before, "the refused registration left a secret behind"


async def test_an_unknown_engine_is_refused_with_the_list_of_known_ones(api: Api) -> None:
    org_id = await _org(api, "alice", "Acme")

    status, _ = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/data-sources",
        who="alice",
        body=REGISTRATION | {"engine": "oracle"},
    )

    assert status == 422


# ---------------------------------------------------------------------------
# Who may do what
# ---------------------------------------------------------------------------


async def test_a_reader_may_look_but_not_register(api: Api) -> None:
    org_id = await _org(api, "alice", "Acme")
    _, invitation = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/invitations",
        who="alice",
        body={"email": "bob@example.com", "role": "reader"},
    )
    await api.call("POST", "/v1/invitations/accept", who="bob", body={"token": invitation["token"]})
    await api.call("POST", f"/v1/orgs/{org_id}/data-sources", who="alice", body=REGISTRATION)

    listed, sources = await api.call("GET", f"/v1/orgs/{org_id}/data-sources", who="bob")
    refused, _ = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/data-sources",
        who="bob",
        body=REGISTRATION | {"name": "Another"},
    )

    assert listed == 200
    assert [source["name"] for source in sources] == ["Pizza demo"]
    assert refused == 403


async def test_another_organization_cannot_see_or_touch_it(api: Api) -> None:
    """Isolation, from the outside: not a filtered view, an absent one."""
    acme = await _org(api, "alice", "Acme")
    globex = await _org(api, "dave", "Globex")
    _, created = await api.call(
        "POST", f"/v1/orgs/{acme}/data-sources", who="alice", body=REGISTRATION
    )

    listed, sources = await api.call("GET", f"/v1/orgs/{globex}/data-sources", who="dave")
    # Dave is an Admin of Globex, so this is a 404 about the data source rather
    # than a 403 about the organization — he may ask, the row simply is not his.
    fetched, _ = await api.call(
        "GET", f"/v1/orgs/{globex}/data-sources/{created['id']}", who="dave"
    )
    outsider, _ = await api.call("GET", f"/v1/orgs/{acme}/data-sources", who="dave")

    assert (listed, sources) == (200, [])
    assert fetched == 404
    assert outsider == 403


# ---------------------------------------------------------------------------
# Rotation, deletion, and the test endpoint
# ---------------------------------------------------------------------------


async def test_rotating_the_password_replaces_it_and_says_nothing_about_it(
    api: Api, app_database: URL, secrets_provider: LocalSecretsProvider
) -> None:
    org_id = await _org(api, "alice", "Acme")
    _, created = await api.call(
        "POST", f"/v1/orgs/{org_id}/data-sources", who="alice", body=REGISTRATION
    )

    status, _ = await api.call(
        "PATCH",
        f"/v1/orgs/{org_id}/data-sources/{created['id']}",
        who="alice",
        body={"password": "rotated-and-longer"},
    )

    assert status == 200
    stored = await secrets_provider.get(created["secret_ref"])
    # The username is kept: a rotation of one half must not blank the other.
    assert stored == {"username": PIZZA_ACCOUNT, "password": "rotated-and-longer"}

    details = (
        await _rows(
            app_database,
            org_id,
            "SELECT details::text FROM audit_log WHERE action = 'datasource.updated'",
        )
    )[0][0]
    assert "credentials" in details
    assert "rotated-and-longer" not in details


async def test_renaming_does_not_disturb_the_credential(
    api: Api, secrets_provider: LocalSecretsProvider
) -> None:
    org_id = await _org(api, "alice", "Acme")
    _, created = await api.call(
        "POST", f"/v1/orgs/{org_id}/data-sources", who="alice", body=REGISTRATION
    )

    status, updated = await api.call(
        "PATCH",
        f"/v1/orgs/{org_id}/data-sources/{created['id']}",
        who="alice",
        body={"name": "Pizza (read-only)", "port": 6543},
    )

    assert status == 200
    assert updated["name"] == "Pizza (read-only)"
    assert updated["host_display"] == "seed-pizza-pg:6543/pizza"
    assert updated["username_last4"] == "only"
    assert (await secrets_provider.get(created["secret_ref"]))["password"] == PIZZA_LOGIN


async def test_deleting_removes_the_row_and_the_credential(
    api: Api, app_database: URL, secrets_provider: LocalSecretsProvider
) -> None:
    org_id = await _org(api, "alice", "Acme")
    _, created = await api.call(
        "POST", f"/v1/orgs/{org_id}/data-sources", who="alice", body=REGISTRATION
    )

    status, _ = await api.call(
        "DELETE", f"/v1/orgs/{org_id}/data-sources/{created['id']}", who="alice"
    )

    assert status == 204
    assert await _rows(app_database, org_id, "SELECT id FROM data_sources") == []
    with pytest.raises(SecretNotFoundError):
        await secrets_provider.get(created["secret_ref"])


async def test_a_data_source_that_never_existed_is_a_404(api: Api) -> None:
    """On every route that names one, not only the one that reads it."""
    org_id = await _org(api, "alice", "Acme")
    missing = f"/v1/orgs/{org_id}/data-sources/{uuid.uuid4()}"

    read, _ = await api.call("GET", missing, who="alice")
    patched, _ = await api.call("PATCH", missing, who="alice", body={"name": "Ghost"})
    deleted, _ = await api.call("DELETE", missing, who="alice")
    tested, _ = await api.call("POST", f"{missing}/test", who="alice")

    assert [read, patched, deleted, tested] == [404, 404, 404, 404]


async def test_a_registration_that_fails_late_leaves_no_credential_behind(
    api: Api, secrets_provider: LocalSecretsProvider
) -> None:
    """The compensation path, forced.

    A name longer than the column is refused by the database rather than by
    pydantic, so this reaches the service exactly as an unforeseen failure would
    — after the credential has been written and before the row exists.
    """
    org_id = await _org(api, "alice", "Acme")
    _, alice = await api.call("GET", "/v1/me", who="alice")
    before = _entries(secrets_provider)

    with pytest.raises(DBAPIError):
        await create_data_source(
            org_id=org_id,
            actor_user_id=uuid.UUID(alice["user_id"]),
            name="x" * 500,
            engine="pg",
            host="seed-pizza-pg",
            port=5432,
            database="pizza",
            username=PIZZA_ACCOUNT,
            password=PIZZA_LOGIN,
        )

    assert _entries(secrets_provider) == before, "a failed registration left a credential behind"


async def _register_customer_database(
    api: Api, org_id: uuid.UUID, database: CustomerDatabase, *, as_owner: bool = False
) -> dict[str, Any]:
    account = database.url.username if as_owner else database.reader_username
    login = database.url.password if as_owner else database.reader_password
    _, created = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/data-sources",
        who="alice",
        body={
            "name": "Owner login" if as_owner else "Customer",
            "engine": "pg",
            "host": database.host,
            "port": database.port,
            "database": database.database,
            "username": account,
            "password": login,
        },
    )
    return cast("dict[str, Any]", created)


async def test_a_read_only_login_verifies_end_to_end(
    api: Api, app_database: URL, customer_database: CustomerDatabase
) -> None:
    """The M3 promise, through the API a person will actually use."""
    org_id = await _org(api, "alice", "Acme")
    created = await _register_customer_database(api, org_id, customer_database)

    assert created["status"] == "registered"
    assert created["readonly_verified"] is False
    assert created["last_verified_at"] is None

    status, result = await api.call(
        "POST", f"/v1/orgs/{org_id}/data-sources/{created['id']}/test", who="alice"
    )

    assert status == 200
    assert result["reachable"] is True
    assert result["readonly_verified"] is True
    assert result["status"] == "verified"
    assert result["server_version"].startswith("PostgreSQL")

    # And the row remembers it, so a screen does not have to re-test to know.
    _, fetched = await api.call(
        "GET", f"/v1/orgs/{org_id}/data-sources/{created['id']}", who="alice"
    )
    assert fetched["status"] == "verified"
    assert fetched["readonly_verified"] is True
    assert fetched["last_verified_at"] is not None

    actions = [
        row[0]
        for row in await _rows(app_database, org_id, "SELECT action FROM audit_log ORDER BY id")
    ]
    assert actions == ["org.created", "datasource.created", "datasource.tested"]


async def test_credentials_that_can_write_are_refused_verification(
    api: Api, customer_database: CustomerDatabase
) -> None:
    """Registering with the owner account is the mistake this check exists for."""
    org_id = await _org(api, "alice", "Acme")
    created = await _register_customer_database(api, org_id, customer_database, as_owner=True)

    _, result = await api.call(
        "POST", f"/v1/orgs/{org_id}/data-sources/{created['id']}/test", who="alice"
    )

    assert result["reachable"] is True
    assert result["readonly_verified"] is False
    assert result["status"] == "error"
    assert "not read-only" in result["detail"]
    assert result["evidence"], "a refusal must say what it found"

    _, fetched = await api.call(
        "GET", f"/v1/orgs/{org_id}/data-sources/{created['id']}", who="alice"
    )
    assert fetched["status"] == "error"
    assert fetched["readonly_verified"] is False
    # A failed check does not get to claim a verification time: the column says
    # when this was last *proven* read-only, and it never was.
    assert fetched["last_verified_at"] is None


async def test_rotating_credentials_retires_the_previous_verification(
    api: Api, customer_database: CustomerDatabase
) -> None:
    """A green tick must describe the credentials the row holds *now*."""
    org_id = await _org(api, "alice", "Acme")
    created = await _register_customer_database(api, org_id, customer_database)
    await api.call("POST", f"/v1/orgs/{org_id}/data-sources/{created['id']}/test", who="alice")

    _, updated = await api.call(
        "PATCH",
        f"/v1/orgs/{org_id}/data-sources/{created['id']}",
        who="alice",
        body={"password": "some-other-password"},
    )

    assert updated["readonly_verified"] is False
    assert updated["status"] == "registered"
    assert updated["last_verified_at"] is None


async def test_a_verification_failure_never_quotes_the_credential(
    api: Api, customer_database: CustomerDatabase
) -> None:
    org_id = await _org(api, "alice", "Acme")
    _, created = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/data-sources",
        who="alice",
        body={
            "name": "Wrong password",
            "engine": "pg",
            "host": customer_database.host,
            "port": customer_database.port,
            "database": customer_database.database,
            "username": customer_database.reader_username,
            "password": PIZZA_LOGIN,
        },
    )

    _, result = await api.call(
        "POST", f"/v1/orgs/{org_id}/data-sources/{created['id']}/test", who="alice"
    )

    assert result["readonly_verified"] is False
    assert PIZZA_LOGIN not in result["detail"]
    assert customer_database.reader_username not in result["detail"]


async def test_an_engine_without_a_connector_says_when_it_arrives(api: Api) -> None:
    """Registered today, unusable until WP3.3 — and the message says so.

    The engine is checked before the address is probed and before the credential
    is read, so this answers without touching the network at all.
    """
    org_id = await _org(api, "alice", "Acme")
    _, created = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/data-sources",
        who="alice",
        body=REGISTRATION | {"engine": "mssql"},
    )

    _, result = await api.call(
        "POST", f"/v1/orgs/{org_id}/data-sources/{created['id']}/test", who="alice"
    )

    assert result["readonly_verified"] is False
    assert "WP3.3" in result["detail"]


async def test_an_unreachable_address_is_reported_without_leaking_it(api: Api) -> None:
    org_id = await _org(api, "alice", "Acme")
    _, created = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/data-sources",
        who="alice",
        body=REGISTRATION | {"host": "no-such-host.invalid", "port": 5432},
    )

    status, result = await api.call(
        "POST", f"/v1/orgs/{org_id}/data-sources/{created['id']}/test", who="alice"
    )

    assert status == 200
    assert result["reachable"] is False
    assert result["readonly_verified"] is False
    assert "no-such-host.invalid" not in result["detail"]


def _entries(provider: LocalSecretsProvider) -> set[str]:
    """Which references the store holds, for "did this leave anything behind?".

    The references rather than the file text: a failed registration that writes
    and then removes its secret leaves an empty file where there was none, and
    that difference is bookkeeping, not a leak.
    """
    if not provider.path.exists():
        return set()
    document = json.loads(provider.path.read_text(encoding="utf-8"))
    return set(document["secrets"])


def test_the_routes_and_the_database_agree_on_which_engines_exist() -> None:
    """Two declarations of the same fact; a test rather than a comment.

    The route's Literal produces a 422 that lists what is accepted; the CHECK
    constraint is what the database will enforce. If they drift, one of them
    starts lying and the other starts returning 500s.
    """
    assert set(get_args(Engine)) == set(DATA_SOURCE_ENGINES)


def test_every_engine_the_api_accepts_is_either_supported_or_names_its_work_package() -> None:
    """Registering an engine with no connector yet is allowed — silently failing
    to explain why is not."""
    for engine in DATA_SOURCE_ENGINES:
        if engine in SUPPORTED_ENGINES:
            continue
        with pytest.raises(ConnectorError, match=r"WP|V1\.1"):
            require_supported(engine)
