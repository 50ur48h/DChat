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
                    "  also_called text)"
                )
            )
            await connection.execute(text(f"DELETE FROM {BOOK}"))
            await connection.execute(
                text(
                    f"INSERT INTO {BOOK} (metric_key, definition_text, formula, also_called) "
                    "VALUES ('stock_value', "
                    "'Total price of everything we list, excluding samples.', "
                    "'sum(products.price)', 'stock value, listed value')"
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
