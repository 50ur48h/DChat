"""The catalog over HTTP.

Authorization for these two routes is proved by the role matrix, which records
who may refresh and who may browse. What is left to check here is the contract:
the shapes a screen and, from Phase 5, the DAL will read.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from customer_db import CustomerDatabase
from dataagent.auth.jwt_validator import TokenValidator
from dataagent.auth.principal import Principal, TokenError
from dataagent.catalog import routes as catalog_routes
from dataagent.config import Settings
from dataagent.db import engine as engine_module
from dataagent.main import create_app
from dataagent.secrets.local import LocalSecretsProvider
from dataagent.tenancy import session as session_module


class _SubjectAsToken(TokenValidator):
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
        self, method: str, path: str, who: str, body: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        async with AsyncClient(
            transport=ASGITransport(app=self._app), base_url="http://testserver"
        ) as client:
            response = await client.request(
                method, path, headers={"Authorization": f"Bearer {who}"}, json=body
            )
        return response.status_code, (None if response.status_code == 204 else response.json())


@pytest.fixture
async def api(
    app_database: URL,
    migrated_database: URL,
    secrets_provider: LocalSecretsProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Api]:
    owner = create_async_engine(migrated_database)
    app_engine = create_async_engine(app_database)
    monkeypatch.setattr(engine_module, "get_engine", lambda: owner)
    monkeypatch.setattr(
        session_module,
        "_session_factory",
        lambda: async_sessionmaker(app_engine, expire_on_commit=False, autoflush=False),
    )
    # Refreshing a catalog embeds its cards since B-018, and this app resolves
    # settings from the developer's `.env` — so without this seam every run of
    # this file would embed a whole catalog at the owner's expense, and the
    # B-040 guard would refuse and turn a route test into a 500. What the cards
    # look like with vectors is proved in `tests/catalog/test_card_embeddings.py`
    # against a stub; this file is about the routes.
    monkeypatch.setattr(catalog_routes, "card_embedder", lambda: None)
    app = create_app(settings=Settings(auth_mode="dev", env="ci", build_env="dev"))
    app.state.token_validator = _SubjectAsToken()
    try:
        yield Api(app)
    finally:
        await owner.dispose()
        await app_engine.dispose()


async def _org_with_source(api: Api, customer: CustomerDatabase) -> tuple[str, str]:
    _, org = await api.call("POST", "/v1/orgs", "alice", {"name": "Acme"})
    org_id = str(org["org_id"])
    _, created = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/data-sources",
        "alice",
        {
            "name": "Customer",
            "engine": "pg",
            "host": customer.host,
            "port": customer.port,
            "database": customer.database,
            "username": customer.reader_username,
            "password": customer.reader_password,
            "tls_mode": "prefer",
        },
    )
    await api.call("POST", f"/v1/orgs/{org_id}/data-sources/{created['id']}/test", "alice")
    return org_id, str(created["id"])


async def test_refresh_then_browse(api: Api, isolated_customer_database: CustomerDatabase) -> None:
    org_id, source_id = await _org_with_source(api, isolated_customer_database)

    status, refreshed = await api.call(
        "POST", f"/v1/orgs/{org_id}/data-sources/{source_id}/refresh", "alice"
    )

    assert status == 200
    assert refreshed["changed"] is True
    assert refreshed["tables"] == 5
    assert refreshed["snapshot"]["version"] == 1
    assert refreshed["snapshot"]["status"] == "active"

    status, catalog = await api.call(
        "GET", f"/v1/orgs/{org_id}/data-sources/{source_id}/catalog", "alice"
    )

    assert status == 200
    tables = {table["table_name"]: table for table in catalog["tables"]}
    assert set(tables) == {"regions", "shops", "busy_shops", "products", "people"}
    assert [column["name"] for column in tables["shops"]["columns"]] == [
        "id",
        "region_id",
        "name",
        "opened_on",
    ]
    assert catalog["relationships"][0]["from_table"] == "shops"
    assert catalog["relationships"][0]["to_table"] == "regions"


async def test_a_second_refresh_reports_that_nothing_changed(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, source_id = await _org_with_source(api, isolated_customer_database)
    await api.call("POST", f"/v1/orgs/{org_id}/data-sources/{source_id}/refresh", "alice")

    _, again = await api.call(
        "POST", f"/v1/orgs/{org_id}/data-sources/{source_id}/refresh", "alice"
    )

    assert again["changed"] is False
    assert "No change" in again["detail"]
    assert again["snapshot"]["version"] == 1


async def test_browsing_a_source_with_no_catalog_is_a_404(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, source_id = await _org_with_source(api, isolated_customer_database)

    status, body = await api.call(
        "GET", f"/v1/orgs/{org_id}/data-sources/{source_id}/catalog", "alice"
    )

    assert status == 404
    assert "Refresh it" in body["detail"]


async def test_a_data_source_from_another_organization_is_a_404(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """Not a filtered view of somebody else's catalog — an absent one."""
    org_id, source_id = await _org_with_source(api, isolated_customer_database)
    await api.call("POST", f"/v1/orgs/{org_id}/data-sources/{source_id}/refresh", "alice")
    _, other = await api.call("POST", "/v1/orgs", "dave", {"name": "Globex"})

    refreshed, _ = await api.call(
        "POST", f"/v1/orgs/{other['org_id']}/data-sources/{source_id}/refresh", "dave"
    )
    browsed, _ = await api.call(
        "GET", f"/v1/orgs/{other['org_id']}/data-sources/{source_id}/catalog", "dave"
    )

    assert (refreshed, browsed) == (404, 404)


async def test_the_refresh_is_audited(
    api: Api, app_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, source_id = await _org_with_source(api, isolated_customer_database)
    await api.call("POST", f"/v1/orgs/{org_id}/data-sources/{source_id}/refresh", "alice")

    engine = create_async_engine(app_database)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": org_id}
            )
            rows = (
                await connection.execute(
                    text(
                        "SELECT action, details::text FROM audit_log "
                        "WHERE action LIKE 'catalog.%' ORDER BY id"
                    )
                )
            ).all()
    finally:
        await engine.dispose()

    assert [action for action, _ in rows] == ["catalog.refreshed"]
    assert '"changed": true' in rows[0][1]
    assert isolated_customer_database.reader_password not in rows[0][1]


def test_the_catalog_response_carries_no_credential_field() -> None:
    """The same guard the data-source schema has, on the shapes added here."""
    spec = create_app(settings=Settings(auth_mode="dev", env="ci", build_env="dev")).openapi()
    schemas = spec["components"]["schemas"]

    for name in ("CatalogOut", "TableOut", "ColumnOut", "SnapshotOut", "RelationshipOut"):
        fields = set(schemas[name]["properties"])
        assert not {"password", "username", "secret_ref", "token"} & fields, (
            f"{name} carries something it should not: {sorted(fields)}"
        )
