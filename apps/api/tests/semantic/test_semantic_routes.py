"""The semantic layer over HTTP (plan WP10.2d, **B-059**).

Who may call these is proved by the role matrix, which records that all five are
Admin. What is left here is the contract, and one property that is not a shape at
all: **an import must not be able to make anything bind.**

That is the test worth having. `propose_from_table` is proved against the service
in `tests/agent/test_definition_import.py`; what this file adds is the route, and
the route is where a product surface could quietly get it wrong — by returning
proposals a screen then treats as definitions, or by activating on import because
that is one fewer click. So the import test asserts what the *definitions* list
says afterwards, not only what the import returned.

The other half is D-033 made visible on the wire. A definition that carries
required filters **binds**; one that does not is prose, informs the model and is
checked by nothing. `binds` is a field rather than something a screen infers from
an empty array, because the difference between "this constrains the SQL" and
"this is a suggestion" is the one thing an Admin accepting a proposal has to
understand.
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

BOOK = "metric_book"


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
    # These tests refresh a catalog, which embeds its cards since B-018. Without
    # the seam every run would embed at the owner's expense and the B-040 guard
    # would turn a route test into a 500 — a report about configuration rather
    # than about the routes.
    monkeypatch.setattr(catalog_routes, "card_embedder", lambda: None)
    app = create_app(settings=Settings(auth_mode="dev", env="ci", build_env="dev"))
    app.state.token_validator = _SubjectAsToken()
    try:
        yield Api(app)
    finally:
        await owner.dispose()
        await app_engine.dispose()


async def _metric_table(customer: CustomerDatabase) -> None:
    """The customer's own metric book, standing in for the F&B `meta_metric`."""
    engine = create_async_engine(customer.url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {BOOK} ("
                    "  metric_key text PRIMARY KEY,"
                    "  definition_text text,"
                    "  formula text,"
                    "  also_called text,"
                    "  caution text)"
                )
            )
            await connection.execute(text(f"DELETE FROM {BOOK}"))
            await connection.execute(
                text(
                    f"INSERT INTO {BOOK} "
                    "(metric_key, definition_text, formula, also_called, caution) "
                    "VALUES ('stock_value', "
                    "'Total price of everything we list, excluding samples.', "
                    "'sum(products.price)', 'stock value, listed value', "
                    "'Samples are excluded, so this understates what is on the shelf.')"
                )
            )
            await connection.execute(text(f"GRANT SELECT ON {BOOK} TO {customer.reader_username}"))
    finally:
        await engine.dispose()


async def _org_with_catalog(api: Api, customer: CustomerDatabase) -> tuple[str, str]:
    """An organization, a verified data source, and a catalog to validate against.

    The catalog is not optional here: a required filter is checked against it at
    the door, which is the behaviour half these tests are about.
    """
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
    source_id = str(created["id"])
    await api.call("POST", f"/v1/orgs/{org_id}/data-sources/{source_id}/test", "alice")
    await api.call("POST", f"/v1/orgs/{org_id}/data-sources/{source_id}/refresh", "alice")
    return org_id, source_id


def _import_body() -> dict[str, Any]:
    return {
        "table": BOOK,
        "name_column": "metric_key",
        "description_column": "definition_text",
        "expression_column": "formula",
        "synonyms_column": "also_called",
    }


# ---------------------------------------------------------------------------
# Writing one by hand
# ---------------------------------------------------------------------------


async def test_an_admin_writes_a_definition_and_it_binds(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)

    status, created = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions",
        "alice",
        {
            "name": "Listed value",
            "description": "What everything we list is worth.",
            "synonyms": ["stock value"],
            "required_filters": [
                {"table": "products", "column": "price", "op": "gt", "values": ["0"]}
            ],
        },
    )

    assert status == 201
    # Lowercased, because "Listed value" and "listed_value" are one metric and a
    # catalog of near-duplicates is worse than no catalog.
    assert created["name"] == "listed value"
    assert created["binds"] is True
    assert created["required_filters"][0]["column"] == "price"

    status, listed = await api.call(
        "GET", f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions", "alice"
    )
    assert status == 200
    assert [item["name"] for item in listed] == ["listed value"]


async def test_a_filter_on_a_column_that_does_not_exist_is_refused_at_the_door(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """And the 400 names the column.

    A filter that matches nothing would surface during somebody's run as a critic
    finding they cannot act on, which is both too late and unactionable. This is
    the moment it is cheap to fix, so this is where it is refused.
    """
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)

    status, body = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions",
        "alice",
        {
            "name": "nonsense",
            "description": "Requires something this database has never had.",
            "required_filters": [
                {"table": "products", "column": "no_such_column", "op": "eq", "values": ["1"]}
            ],
        },
    )

    assert status == 400
    assert "no_such_column" in body["detail"]

    _, listed = await api.call(
        "GET", f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions", "alice"
    )
    assert listed == []


async def test_an_operator_the_critic_cannot_check_is_refused(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """The operator set is closed on purpose: one the critic cannot check is one
    the product would claim to enforce and would not."""
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)

    status, body = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions",
        "alice",
        {
            "name": "roughly",
            "description": "Approximately something.",
            "required_filters": [
                {"table": "products", "column": "price", "op": "like", "values": ["%a%"]}
            ],
        },
    )

    assert status == 400
    assert "filter operator" in body["detail"]


# ---------------------------------------------------------------------------
# Importing what the database already carries
# ---------------------------------------------------------------------------


async def test_an_import_proposes_and_binds_nothing(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """**The property the whole design turns on**, asserted at the route.

    A crawler that could write an active definition would let a customer's own
    metadata table decide what the platform enforces. So the assertion that
    matters is not what the import returned — it is that the *definitions* list
    is still empty afterwards.
    """
    await _metric_table(isolated_customer_database)
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)

    status, proposed = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions/import",
        "alice",
        _import_body(),
    )

    assert status == 201
    assert [item["name"] for item in proposed] == ["stock_value"]
    assert "excluding samples" in proposed[0]["description"]
    assert proposed[0]["provenance"]["table"] == f"public.{BOOK}"

    _, waiting = await api.call(
        "GET", f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions/proposals", "alice"
    )
    assert [item["name"] for item in waiting] == ["stock_value"]

    _, active = await api.call(
        "GET", f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions", "alice"
    )
    assert active == []


async def test_importing_a_table_the_catalog_does_not_know_is_a_400(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """Refused by the DAL, which grounds every read against the catalog — the
    same protection any other query gets, reported in words an Admin can act on
    rather than as a 500."""
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)

    status, body = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions/import",
        "alice",
        {**_import_body(), "table": "not_a_table"},
    )

    assert status == 400
    assert "not_a_table" in body["detail"]


async def test_a_table_name_that_is_not_a_bare_identifier_is_refused_before_any_sql(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """The cheap half of 5.10's two independent layers. The DAL would catch this
    too; building SQL by concatenation and relying only on a downstream check is
    how the downstream check eventually gets moved."""
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)

    status, body = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions/import",
        "alice",
        {**_import_body(), "table": "metric_book; DROP TABLE shops"},
    )

    assert status == 400
    assert "table name" in body["detail"]


async def test_an_imported_caveat_survives_acceptance_and_reaches_the_wire(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """The whole travel, because every stop on it could drop the sentence.

    `caveat` exists to be *read by a person*, so a test that stops at the
    service proves the half that was never in doubt. This drives the route the
    Admin screen calls: the customer's own column becomes a `ColumnMapping`,
    becomes a row, becomes a `ProposalOut` the reviewer reads *before* blessing
    it, survives `accept` — which built its returned `Definition` field by field
    and silently omitted this one — and is still there on the definitions list.

    Break any link and this goes red. A test on `Definition.caveat` alone would
    have stayed green through the `accept` bug (B-133's lesson).
    """
    await _metric_table(isolated_customer_database)
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    sentence = "Samples are excluded, so this understates what is on the shelf."

    _, proposed = await api.call(
        "POST", f"{base}/import", "alice", {**_import_body(), "caveat_column": "caution"}
    )
    # The reviewer has to see what they are about to bless.
    assert proposed[0]["caveat"] == sentence

    _, accepted = await api.call("POST", f"{base}/{proposed[0]['id']}/accept", "alice", {})
    assert accepted["caveat"] == sentence

    _, listed = await api.call("GET", base, "alice")
    assert [row["caveat"] for row in listed if row["name"] == "stock_value"] == [sentence]


async def test_an_unmapped_caveat_column_leaves_the_definition_without_one(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """Most metrics are just a formula and should not be made to sound uncertain."""
    await _metric_table(isolated_customer_database)
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"

    _, proposed = await api.call("POST", f"{base}/import", "alice", _import_body())

    assert proposed[0]["caveat"] is None


async def test_a_second_import_does_not_re_propose_what_is_already_known(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    await _metric_table(isolated_customer_database)
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    path = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions/import"

    await api.call("POST", path, "alice", _import_body())
    status, again = await api.call("POST", path, "alice", _import_body())

    # 201 with nothing in it: the request succeeded and proposed nothing, which
    # is a different thing from the mapping being wrong.
    assert status == 201
    assert again == []


# ---------------------------------------------------------------------------
# Accepting is where prose becomes a constraint
# ---------------------------------------------------------------------------


async def test_accepting_with_filters_is_what_makes_a_definition_bind(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """D-033, at the moment it happens."""
    await _metric_table(isolated_customer_database)
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    _, proposed = await api.call("POST", f"{base}/import", "alice", _import_body())
    definition_id = proposed[0]["id"]

    status, accepted = await api.call(
        "POST",
        f"{base}/{definition_id}/accept",
        "alice",
        {
            "required_filters": [
                {"table": "products", "column": "price", "op": "gt", "values": ["0"]}
            ]
        },
    )

    assert status == 200
    assert accepted["binds"] is True

    _, active = await api.call("GET", base, "alice")
    assert [item["name"] for item in active] == ["stock_value"]

    _, waiting = await api.call("GET", f"{base}/proposals", "alice")
    assert waiting == []


async def test_accepting_without_filters_is_prose_and_says_so(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """An Admin may bless a definition as prose. It reaches the model and binds
    nothing, and `binds` is false so no screen has to infer that from an empty
    array — which is the inference that would eventually be drawn wrongly."""
    await _metric_table(isolated_customer_database)
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    _, proposed = await api.call("POST", f"{base}/import", "alice", _import_body())

    status, accepted = await api.call(
        "POST", f"{base}/{proposed[0]['id']}/accept", "alice", {"required_filters": []}
    )

    assert status == 200
    assert accepted["binds"] is False
    assert accepted["required_filters"] == []


async def test_accepting_with_a_filter_this_database_cannot_support_leaves_it_proposed(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """Validated **before** the row is activated. A definition that half-accepted
    — active, with a filter that matches nothing — is the worst of both."""
    await _metric_table(isolated_customer_database)
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    _, proposed = await api.call("POST", f"{base}/import", "alice", _import_body())

    status, body = await api.call(
        "POST",
        f"{base}/{proposed[0]['id']}/accept",
        "alice",
        {
            "required_filters": [
                {"table": "products", "column": "invented", "op": "eq", "values": ["1"]}
            ]
        },
    )

    assert status == 400
    assert "invented" in body["detail"]

    _, active = await api.call("GET", base, "alice")
    assert active == []
    _, waiting = await api.call("GET", f"{base}/proposals", "alice")
    assert [item["name"] for item in waiting] == ["stock_value"]


async def test_accepting_the_same_proposal_twice_is_a_404(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """It is no longer a proposal. A second accept must not look like a way to
    change an active definition's filters, because that is a different act with
    different consequences and it does not exist yet."""
    await _metric_table(isolated_customer_database)
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    _, proposed = await api.call("POST", f"{base}/import", "alice", _import_body())
    accept = f"{base}/{proposed[0]['id']}/accept"

    await api.call("POST", accept, "alice", {"required_filters": []})
    status, _ = await api.call("POST", accept, "alice", {"required_filters": []})

    assert status == 404


async def test_rejecting_retires_a_proposal_rather_than_deleting_it(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """So that *"we looked at this and said no"* is answerable, and so a second
    import does not silently re-propose what an Admin turned down."""
    await _metric_table(isolated_customer_database)
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    _, proposed = await api.call("POST", f"{base}/import", "alice", _import_body())

    status, _ = await api.call("POST", f"{base}/{proposed[0]['id']}/reject", "alice")

    assert status == 204
    _, waiting = await api.call("GET", f"{base}/proposals", "alice")
    assert waiting == []
    _, active = await api.call("GET", base, "alice")
    assert active == []

    # The name is still taken, which is what "retired rather than deleted" buys.
    _, again = await api.call("POST", f"{base}/import", "alice", _import_body())
    assert again == []


async def test_a_definition_from_another_organization_is_a_404(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """Not a filtered view of somebody else's proposal — an absent one. Row-level
    security is what makes the read return nothing; the route turns that into an
    answer."""
    await _metric_table(isolated_customer_database)
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    _, proposed = await api.call("POST", f"{base}/import", "alice", _import_body())

    _, other = await api.call("POST", "/v1/orgs", "dave", {"name": "Globex"})
    other_org = str(other["org_id"])

    status, _ = await api.call(
        "POST",
        f"/v1/orgs/{other_org}/data-sources/{source_id}/definitions/{proposed[0]['id']}/accept",
        "dave",
        {"required_filters": []},
    )

    assert status == 404


# ---------------------------------------------------------------------------
# Correcting one, which used to require psql (B-088)
# ---------------------------------------------------------------------------


async def _active_definition(api: Api, org_id: str, source_id: str) -> dict[str, Any]:
    """One definition in force, written by hand and binding nothing yet."""
    _, created = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions",
        "alice",
        {
            "name": "listed value",
            "description": "What everything we list is worth.",
            "synonyms": ["stock value"],
        },
    )
    return dict(created)


async def test_an_admin_gives_an_active_definition_the_filter_it_should_have_had(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """**B-088, the whole of it.** The likeliest moment to get a filter wrong is
    the first time you write one, which is exactly when this product used to lock
    you out: no edit, no un-accept, and re-accepting a 404. The only way back was
    deleting the row in psql."""
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    definition = await _active_definition(api, org_id, source_id)
    assert definition["binds"] is False

    status, edited = await api.call(
        "PATCH",
        f"{base}/{definition['id']}",
        "alice",
        {
            "required_filters": [
                {"table": "products", "column": "price", "op": "gt", "values": ["0"]}
            ]
        },
    )

    assert status == 200
    assert edited["binds"] is True
    assert edited["version"] == 2

    _, active = await api.call("GET", base, "alice")
    assert active[0]["required_filters"][0]["column"] == "price"
    # The rest of the definition is untouched: an Admin fixing a filter did not
    # have to resend a description, which is where a description loses a sentence.
    assert active[0]["description"] == definition["description"]
    assert active[0]["synonyms"] == definition["synonyms"]


async def test_an_edit_is_validated_exactly_as_acceptance_is(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """And the definition is left exactly as it was.

    A correction is not more trustworthy than the original, so it meets the same
    catalog check — and a refused edit must not half-apply, because a definition
    that is active with a filter matching nothing is the worst of both states.
    """
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    definition = await _active_definition(api, org_id, source_id)

    status, body = await api.call(
        "PATCH",
        f"{base}/{definition['id']}",
        "alice",
        {
            "description": "Corrected.",
            "required_filters": [
                {"table": "products", "column": "invented", "op": "eq", "values": ["1"]}
            ],
        },
    )

    assert status == 400
    assert "invented" in body["detail"]

    _, active = await api.call("GET", base, "alice")
    assert active[0]["description"] == definition["description"]
    assert active[0]["version"] == 1


async def test_an_empty_filter_list_stops_the_enforcement_and_keeps_the_prose(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """The other direction, and the one that matters most in practice: a filter
    that turned out to be wrong has to be removable through the product, or the
    answer to a false block is once again the database."""
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    definition = await _active_definition(api, org_id, source_id)
    await api.call(
        "PATCH",
        f"{base}/{definition['id']}",
        "alice",
        {
            "required_filters": [
                {"table": "products", "column": "price", "op": "gt", "values": ["0"]}
            ]
        },
    )

    status, unbound = await api.call(
        "PATCH", f"{base}/{definition['id']}", "alice", {"required_filters": []}
    )

    assert status == 200
    assert unbound["binds"] is False
    assert unbound["required_filters"] == []
    assert unbound["description"] == definition["description"]


async def test_omitting_a_field_leaves_it_alone_and_null_clears_the_formula(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """The one place where absent and null differ, asserted because it is the
    one place a partial update can silently lose something."""
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    _, created = await api.call(
        "POST",
        base,
        "alice",
        {
            "name": "listed value",
            "description": "What everything we list is worth.",
            "expression": "sum(products.price)",
            "synonyms": ["stock value"],
        },
    )

    _, kept = await api.call(
        "PATCH", f"{base}/{created['id']}", "alice", {"description": "Worth, at list price."}
    )
    assert kept["expression"] == "sum(products.price)"
    assert kept["synonyms"] == ["stock value"]

    _, cleared = await api.call("PATCH", f"{base}/{created['id']}", "alice", {"expression": None})
    assert cleared["expression"] is None
    assert cleared["description"] == "Worth, at list price."


async def test_editing_a_proposal_is_a_404(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """A proposal is accepted, not edited — and `accept` already takes the
    filters and synonyms. Two routes into the same act would eventually disagree
    about which one validates."""
    await _metric_table(isolated_customer_database)
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    _, proposed = await api.call("POST", f"{base}/import", "alice", _import_body())

    status, _ = await api.call(
        "PATCH", f"{base}/{proposed[0]['id']}", "alice", {"description": "Rewritten."}
    )

    assert status == 404


async def test_retiring_a_definition_stops_it_binding_without_forgetting_it(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    definition = await _active_definition(api, org_id, source_id)

    status, _ = await api.call("DELETE", f"{base}/{definition['id']}", "alice")

    assert status == 204
    _, active = await api.call("GET", base, "alice")
    assert active == []

    # Retiring it twice is a 404: it is no longer in force, and a second call
    # must not read as success. Editing it afterwards is a 404 for the same
    # reason — history is not edited.
    status, _ = await api.call("DELETE", f"{base}/{definition['id']}", "alice")
    assert status == 404
    status, _ = await api.call(
        "PATCH", f"{base}/{definition['id']}", "alice", {"description": "Rewritten."}
    )
    assert status == 404


async def test_a_definitions_history_says_what_it_required_at_each_version(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """**Why versioning is part of B-088 rather than a later nicety.** A
    definition binds, so *"what did this metric require when that answer was
    written"* is a question about whether an answer was right. An overwrite makes
    it unanswerable, and the moment editing ships is the moment unrecorded edits
    start accumulating (D-036)."""
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    definition = await _active_definition(api, org_id, source_id)
    await api.call(
        "PATCH",
        f"{base}/{definition['id']}",
        "alice",
        {
            "required_filters": [
                {"table": "products", "column": "price", "op": "gt", "values": ["0"]}
            ]
        },
    )
    await api.call("DELETE", f"{base}/{definition['id']}", "alice")

    status, history = await api.call("GET", f"{base}/{definition['id']}/versions", "alice")

    assert status == 200
    assert [item["version"] for item in history] == [1, 2, 3]
    assert [item["change"] for item in history] == ["created", "updated", "retired"]
    # Version 1 bound nothing; version 2 is where it started to.
    assert history[0]["required_filters"] == []
    assert history[1]["required_filters"][0]["column"] == "price"
    assert history[2]["status"] == "retired"
    assert all(item["changed_by"] for item in history), "a version says who made it"


async def test_an_edit_that_changes_nothing_writes_no_version(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """A version that says the same as the one before it is noise in the only
    history anybody consults under suspicion."""
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    definition = await _active_definition(api, org_id, source_id)

    status, unchanged = await api.call(
        "PATCH", f"{base}/{definition['id']}", "alice", {"description": definition["description"]}
    )

    assert status == 200
    assert unchanged["version"] == 1
    _, history = await api.call("GET", f"{base}/{definition['id']}/versions", "alice")
    assert [item["version"] for item in history] == [1]


async def test_an_accepted_proposal_starts_its_history_where_it_took_effect(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """A proposal is not a version: it binds nothing while it waits, and
    numbering sentences an Admin has not agreed to would make version 1 mean two
    different things."""
    await _metric_table(isolated_customer_database)
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    _, proposed = await api.call("POST", f"{base}/import", "alice", _import_body())
    definition_id = proposed[0]["id"]

    _, before = await api.call("GET", f"{base}/{definition_id}/versions", "alice")
    assert before == []

    await api.call("POST", f"{base}/{definition_id}/accept", "alice", {"required_filters": []})

    _, history = await api.call("GET", f"{base}/{definition_id}/versions", "alice")
    assert [(item["version"], item["change"]) for item in history] == [(1, "accepted")]


async def test_every_decision_about_a_definition_lands_in_the_audit_log(
    api: Api,
    app_database: URL,
    isolated_customer_database: CustomerDatabase,
) -> None:
    """An edit changes what the platform enforces on generated SQL, so it is
    Admin work that has to be answerable for — and a trail recording edits but
    not the acceptance that preceded them is half a trail.

    Asserted on ``audit_log`` rather than on a mock, because that row is what an
    auditor would look for."""
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    definition = await _active_definition(api, org_id, source_id)
    await api.call(
        "PATCH", f"{base}/{definition['id']}", "alice", {"description": "Worth, at list price."}
    )
    await api.call("DELETE", f"{base}/{definition['id']}", "alice")

    engine = create_async_engine(app_database)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, false)"), {"org": org_id}
            )
            rows = (
                await connection.execute(
                    text("SELECT action, details FROM audit_log WHERE object_id = :id ORDER BY id"),
                    {"id": definition["id"]},
                )
            ).fetchall()
    finally:
        await engine.dispose()

    assert [row[0] for row in rows] == [
        "semantic.definition_created",
        "semantic.definition_updated",
        "semantic.definition_retired",
    ]
    updated = next(row[1] for row in rows if row[0] == "semantic.definition_updated")
    # Which fields moved and which version to read — not the values. The version
    # row holds the content, and copying a customer's own literals into a second
    # table would widen where they live for no gain.
    assert updated["changed"] == ["description"]
    assert updated["version"] == 2


async def test_a_definition_in_another_organization_cannot_be_edited(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """Not a filtered view of somebody else's definition — an absent one."""
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    definition = await _active_definition(api, org_id, source_id)

    _, other = await api.call("POST", "/v1/orgs", "dave", {"name": "Globex"})
    other_org = str(other["org_id"])

    status, _ = await api.call(
        "PATCH",
        f"/v1/orgs/{other_org}/data-sources/{source_id}/definitions/{definition['id']}",
        "dave",
        {"description": "Ours now."},
    )

    assert status == 404


# ---------------------------------------------------------------------------
# Bringing one back, which used to require psql (B-094)
# ---------------------------------------------------------------------------


async def test_a_retired_definition_can_be_brought_back_and_the_history_says_so(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """**B-094, and the acceptance the owner wrote.** `DELETE` had no opposite,
    and three correct rules made that a dead end: accept takes only proposals,
    edit takes only active ones, and an import skips a name any row already
    holds. A mis-clicked retire was recoverable only in the database.

    The history must read as a life — retired, then reinstated — because a
    reader of it is asking what was in force when an answer was written, and a
    gap that looked like an ordinary edit would answer that wrongly.
    """
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    definition = await _active_definition(api, org_id, source_id)
    await api.call("DELETE", f"{base}/{definition['id']}", "alice")

    status, back = await api.call("POST", f"{base}/{definition['id']}/reinstate", "alice")

    assert status == 200
    # v1 created, v2 retired, v3 reinstated: one more than it went out on.
    assert back["version"] == 3
    _, active = await api.call("GET", base, "alice")
    assert [item["name"] for item in active] == [definition["name"]]

    _, history = await api.call("GET", f"{base}/{definition['id']}/versions", "alice")
    assert [item["change"] for item in history] == ["created", "retired", "reinstated"]


async def test_reinstating_keeps_the_filters_it_was_retired_holding(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """The ordinary case: back as it was, still binding what it bound."""
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    definition = await _active_definition(api, org_id, source_id)
    await api.call(
        "PATCH",
        f"{base}/{definition['id']}",
        "alice",
        {
            "required_filters": [
                {"table": "products", "column": "price", "op": "gt", "values": ["0"]}
            ]
        },
    )
    await api.call("DELETE", f"{base}/{definition['id']}", "alice")

    _, back = await api.call("POST", f"{base}/{definition['id']}/reinstate", "alice")

    assert back["binds"] is True
    assert back["required_filters"][0]["column"] == "price"


async def test_a_definition_whose_catalog_moved_on_is_repaired_as_it_comes_back(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """**The second dead end, closed in the same act.** A retired definition
    cannot be edited — `PATCH` takes only active ones — so one whose filters no
    longer resolve would be permanently unreinstatable, which is B-094's own
    shape one layer down."""
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    definition = await _active_definition(api, org_id, source_id)
    await api.call("DELETE", f"{base}/{definition['id']}", "alice")

    status, body = await api.call(
        "POST",
        f"{base}/{definition['id']}/reinstate",
        "alice",
        {
            "required_filters": [
                {"table": "products", "column": "invented", "op": "eq", "values": ["1"]}
            ]
        },
    )

    assert status == 400
    assert "invented" in body["detail"]
    # Refused means still retired, not half-back.
    _, active = await api.call("GET", base, "alice")
    assert active == []

    status, back = await api.call(
        "POST",
        f"{base}/{definition['id']}/reinstate",
        "alice",
        {
            "required_filters": [
                {"table": "products", "column": "price", "op": "gt", "values": ["0"]}
            ]
        },
    )

    assert status == 200
    assert back["required_filters"][0]["column"] == "price"


async def test_reinstating_something_that_is_not_retired_is_a_404(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """An active definition has nothing to come back from, and a route that
    quietly succeeded on one would be a second way to write its filters with
    none of PATCH's framing. A proposal is not reinstatable either: it never
    took effect, so there is no state to return it to."""
    await _metric_table(isolated_customer_database)
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    definition = await _active_definition(api, org_id, source_id)

    status, _ = await api.call("POST", f"{base}/{definition['id']}/reinstate", "alice")
    assert status == 404

    _, proposed = await api.call("POST", f"{base}/import", "alice", _import_body())
    status, _ = await api.call("POST", f"{base}/{proposed[0]['id']}/reinstate", "alice")
    assert status == 404


async def test_a_retired_definition_can_be_found_before_it_can_be_reinstated(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """**Half of B-094 is discoverability.** They vanished from every view, so an
    Admin could not see there was anything to bring back — the reason a
    mis-click became a database job. Listing one puts it nowhere near force:
    the default list still holds only what binds."""
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    definition = await _active_definition(api, org_id, source_id)
    await api.call("DELETE", f"{base}/{definition['id']}", "alice")

    status, retired = await api.call("GET", f"{base}?status=retired", "alice")

    assert status == 200
    assert [item["name"] for item in retired] == [definition["name"]]

    _, active = await api.call("GET", base, "alice")
    assert active == []


async def test_reinstating_lands_in_the_audit_log_as_its_own_act(
    api: Api,
    app_database: URL,
    isolated_customer_database: CustomerDatabase,
) -> None:
    """Recorded as a reinstatement rather than as an edit, in the audit trail as
    in the history: a trail that called it an edit would read as though somebody
    had changed the wording."""
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    definition = await _active_definition(api, org_id, source_id)
    await api.call("DELETE", f"{base}/{definition['id']}", "alice")
    await api.call("POST", f"{base}/{definition['id']}/reinstate", "alice")

    engine = create_async_engine(app_database)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, false)"), {"org": org_id}
            )
            rows = (
                await connection.execute(
                    text("SELECT action FROM audit_log WHERE object_id = :id ORDER BY id"),
                    {"id": definition["id"]},
                )
            ).fetchall()
    finally:
        await engine.dispose()

    assert [row[0] for row in rows] == [
        "semantic.definition_created",
        "semantic.definition_retired",
        "semantic.definition_reinstated",
    ]


# ---------------------------------------------------------------------------
# Verified queries: approved examples, judged but never run
# ---------------------------------------------------------------------------


async def test_an_admin_approves_an_example_and_the_planner_can_be_shown_it(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/verified-queries"

    status, created = await api.call(
        "POST",
        base,
        "alice",
        {
            "question": "how many products do we list?",
            "sql": "SELECT count(*) FROM products",
            "notes": "products is unrelated to shops; do not try to join them.",
        },
    )

    assert status == 201
    assert created["question"] == "how many products do we list?"

    status, listed = await api.call("GET", base, "alice")
    assert status == 200
    assert [item["sql"] for item in listed] == ["SELECT count(*) FROM products"]


async def test_an_example_naming_a_table_that_does_not_exist_is_refused(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """**The property this feature most needs.** An approved statement naming a
    table this database has never had is not merely broken — it is a worked
    demonstration of hallucination sitting in the prompt, teaching the exact
    habit catalog grounding exists to prevent. The same validator that guards
    execution refuses it here, and the message names the identifier."""
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)

    status, body = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/data-sources/{source_id}/verified-queries",
        "alice",
        {"question": "how much revenue?", "sql": "SELECT sum(total) FROM invoices"},
    )

    assert status == 400
    assert "invoices" in body["detail"]


async def test_an_example_that_is_not_read_only_is_refused(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """An Admin cannot bless a statement the platform would refuse to run. The
    approval path and the execution path answer to one validator, deliberately:
    two would eventually disagree, and this is the direction that matters —
    an approved example is SQL the planner is being told to imitate."""
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)

    status, body = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/data-sources/{source_id}/verified-queries",
        "alice",
        {"question": "clear the products", "sql": "DELETE FROM products"},
    )

    assert status == 400
    assert "would run" in body["detail"]


async def test_approving_an_example_reads_no_customer_rows(
    api: Api,
    app_database: URL,
    isolated_customer_database: CustomerDatabase,
) -> None:
    """The statement is judged, not executed. Approving an example is not a
    reason to read somebody's data, and asserted on `query_executions` rather
    than on a mock because that row is what an auditor would look for."""
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)

    await api.call(
        "POST",
        f"/v1/orgs/{org_id}/data-sources/{source_id}/verified-queries",
        "alice",
        {"question": "how many products?", "sql": "SELECT count(*) FROM products"},
    )

    engine = create_async_engine(app_database)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, false)"), {"org": org_id}
            )
            reads = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM query_executions "
                        "WHERE sql_text ILIKE '%count(*) FROM products%'"
                    )
                )
            ).scalar_one()
    finally:
        await engine.dispose()

    assert reads == 0


async def test_retiring_an_example_stops_showing_it_without_forgetting_it(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """Retired rather than deleted, so a run grounded in it last month is still
    explainable this month — the reason D-016 keeps an audit row past its
    subject."""
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/verified-queries"
    _, created = await api.call(
        "POST",
        base,
        "alice",
        {"question": "how many products?", "sql": "SELECT count(*) FROM products"},
    )

    status, _ = await api.call("DELETE", f"{base}/{created['id']}", "alice")

    assert status == 204
    _, listed = await api.call("GET", base, "alice")
    assert listed == []

    # Retiring it twice is a 404: it is no longer an active example, and a
    # second delete must not read as success on a row nothing would show.
    status, _ = await api.call("DELETE", f"{base}/{created['id']}", "alice")
    assert status == 404


async def test_accepting_is_where_an_imported_metric_becomes_reachable(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """**B-085.** A definition is matched to a question by name and synonym, and an
    imported one answers only to its key and to the label its own table carried.
    Nobody asks a question in those words, so an import that cannot be reached
    binds nothing however carefully its filters were written — the whole feature
    is inert. Acceptance is where an Admin says what people actually call it."""
    await _metric_table(isolated_customer_database)
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    _, proposed = await api.call("POST", f"{base}/import", "alice", _import_body())

    status, accepted = await api.call(
        "POST",
        f"{base}/{proposed[0]['id']}/accept",
        "alice",
        {"required_filters": [], "synonyms": ["what we have on the shelves", "listed value"]},
    )

    assert status == 200
    assert accepted["synonyms"] == ["what we have on the shelves", "listed value"]

    _, active = await api.call("GET", base, "alice")
    assert active[0]["synonyms"] == ["what we have on the shelves", "listed value"]


async def test_accepting_without_saying_keeps_the_words_the_import_found(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """Omitting the field is not the same as sending an empty list. An Admin who
    only wanted to add a filter must not silently strip the metric's own label
    and make it unreachable."""
    await _metric_table(isolated_customer_database)
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"
    _, proposed = await api.call("POST", f"{base}/import", "alice", _import_body())
    imported = proposed[0]["synonyms"]
    assert imported, "the fixture's metric table carries synonyms"

    _, accepted = await api.call(
        "POST", f"{base}/{proposed[0]['id']}/accept", "alice", {"required_filters": []}
    )

    assert accepted["synonyms"] == imported


# ---------------------------------------------------------------------------
# A filter that is checked and cannot exclude anything (B-171)
# ---------------------------------------------------------------------------


async def _constant_column_table(customer: CustomerDatabase) -> None:
    """A table with one column that never varies, and one that does.

    `edible_flag` in MiseQ v6.7, in miniature: profiling records
    `distinct_est = 1`, and a filter over such a column is checked by the critic
    and cannot separate one row from another.
    """
    engine = create_async_engine(customer.url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS shipments ("
                    "  id integer PRIMARY KEY, active integer, status text)"
                )
            )
            await connection.execute(text("DELETE FROM shipments"))
            await connection.execute(
                text(
                    "INSERT INTO shipments (id, active, status) VALUES "
                    "(1, 1, 'sent'), (2, 1, 'held'), (3, 1, 'sent'), (4, 1, 'held')"
                )
            )
            await connection.execute(
                text(f"GRANT SELECT ON shipments TO {customer.reader_username}")
            )
    finally:
        await engine.dispose()


async def test_a_filter_that_excludes_nothing_is_reported_as_such(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """**B-171, on the route the screen calls.**

    A definition whose filters cannot separate rows still badges `enforced`, and
    that badge is a claim: prose says the critic checks nothing, `enforced` says
    it checks something. `edible_waste` made exactly that claim through a MiseQ
    version change — `edible_flag` split 3,005.75 kg from 2,522.76 in v6.4 and
    is `1` on every row in v6.7 — with no edit and no signal.

    Driven end to end rather than against the helper: the answer comes from
    profiling data, through the live catalog, into the response the screen
    reads. A test on `non_constraining` alone would pass over a route that never
    loads a policy.
    """
    await _constant_column_table(isolated_customer_database)
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    # Discovery alone leaves `distinct_est` null; the profiler is what knows.
    await api.call("POST", f"/v1/orgs/{org_id}/data-sources/{source_id}/profile", "alice")
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"

    status, hollow = await api.call(
        "POST",
        base,
        "alice",
        {
            "name": "active_shipments",
            "description": "Shipments still in play.",
            "required_filters": [
                {"table": "shipments", "column": "active", "op": "eq", "values": ["1"]}
            ],
        },
    )
    assert status == 201, hollow
    # Still enforced — the critic really does check it. That is the distinction.
    assert hollow["binds"] is True

    status, real = await api.call(
        "POST",
        base,
        "alice",
        {
            "name": "sent_shipments",
            "description": "Shipments that went out.",
            "required_filters": [
                {"table": "shipments", "column": "status", "op": "eq", "values": ["sent"]}
            ],
        },
    )
    assert status == 201, real

    status, listed = await api.call("GET", base, "alice")
    assert status == 200
    by_name = {row["name"]: row for row in listed}
    assert by_name["active_shipments"]["excluding_nothing"] == ["shipments.active"]
    # `status` holds two values, so its filter does separate rows.
    assert by_name["sent_shipments"]["excluding_nothing"] == []


async def test_a_filter_on_an_unprofiled_column_is_not_called_hollow(
    api: Api, isolated_customer_database: CustomerDatabase
) -> None:
    """Silence where the profiler has not run.

    An unprofiled column is unknown, and reporting unknown as *excludes nothing*
    would be the same overstatement pointing the other way — a definition
    labelled hollow on the strength of a measurement nobody took.
    """
    await _constant_column_table(isolated_customer_database)
    org_id, source_id = await _org_with_catalog(api, isolated_customer_database)
    base = f"/v1/orgs/{org_id}/data-sources/{source_id}/definitions"

    await api.call(
        "POST",
        base,
        "alice",
        {
            "name": "active_shipments",
            "description": "Shipments still in play.",
            "required_filters": [
                {"table": "shipments", "column": "active", "op": "eq", "values": ["1"]}
            ],
        },
    )

    _, listed = await api.call("GET", base, "alice")
    assert [row["excluding_nothing"] for row in listed] == [[]]
