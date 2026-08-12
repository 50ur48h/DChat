"""Discovery against a real customer database (plan WP4.1).

Two claims run through all of it. The catalog must describe the database
*accurately* — every table, every column in its own order, and the joins that
exist and only those. And a refresh that finds nothing must cost nothing, which
is asserted at the row level rather than asserted about: the whole design of
snapshots (DECISIONS D-012) turns on it being true.

The fixture is ``isolated_customer_database``, not the seeded pizza one:
catalog tests must not depend on ``make seed`` having been run, or they would
run only on a developer's machine. Isolated because a crawl enumerates *every*
table it can see, and the ordinary fixture shares a database with the platform's
own schema.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from customer_db import CustomerDatabase
from dataagent.catalog import browse, discovery
from dataagent.connectors.base import ColumnInfo, TableRef
from dataagent.datasources import service as datasources
from dataagent.db import engine as engine_module
from dataagent.secrets.local import LocalSecretsProvider
from dataagent.tenancy import session as session_module


@pytest.fixture
async def platform(
    app_database: URL,
    migrated_database: URL,
    secrets_provider: LocalSecretsProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[URL]:
    """The platform database, wired the way a request would find it."""
    owner = create_async_engine(migrated_database)
    app_engine = create_async_engine(app_database)
    monkeypatch.setattr(engine_module, "get_engine", lambda: owner)
    monkeypatch.setattr(
        session_module,
        "_session_factory",
        lambda: async_sessionmaker(app_engine, expire_on_commit=False, autoflush=False),
    )
    try:
        yield app_database
    finally:
        await owner.dispose()
        await app_engine.dispose()


async def _org(migrated_database: URL) -> tuple[uuid.UUID, uuid.UUID]:
    """An organization and the Admin who owns it, written straight in.

    A real user rather than a null actor: registering and testing a data source
    are things a person does, and the audit rows these tests provoke should name
    one. Discovery itself accepts no actor — a scheduled refresh in a later phase
    will have none — which is why only that call passes None.
    """
    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    engine = create_async_engine(migrated_database)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            await connection.execute(
                text("INSERT INTO organizations (id, name) VALUES (:id, 'Discovery')"),
                {"id": org_id},
            )
            await connection.execute(
                text("INSERT INTO users (id, external_subject, email) VALUES (:i, :s, :e)"),
                {"i": user_id, "s": f"sub-{user_id}", "e": "owner@example.com"},
            )
            await connection.execute(
                text(
                    "INSERT INTO org_memberships (org_id, user_id, role) VALUES (:o, :u, 'admin')"
                ),
                {"o": org_id, "u": user_id},
            )
    finally:
        await engine.dispose()
    return org_id, user_id


async def _registered(
    migrated_database: URL, customer: CustomerDatabase, *, verify: bool = True
) -> tuple[uuid.UUID, uuid.UUID]:
    """An organization with the customer database registered, and verified.

    Verification is not decoration here: discovery declines a data source whose
    credentials were never proven read-only, so most of these tests would
    otherwise be testing that refusal.
    """
    org_id, user_id = await _org(migrated_database)
    view = await datasources.create_data_source(
        org_id=org_id,
        actor_user_id=user_id,
        name="Customer",
        engine="pg",
        host=customer.host,
        port=customer.port,
        database=customer.database,
        username=customer.reader_username,
        password=customer.reader_password,
        tls_mode="prefer",
    )
    if verify:
        health = await datasources.test_data_source(
            org_id=org_id, actor_user_id=user_id, data_source_id=view.id
        )
        assert health.readonly_verified, "the fixture's reader should verify"
    return org_id, view.id


async def _counts(url: URL, org_id: uuid.UUID) -> dict[str, int]:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            counts: dict[str, int] = {}
            for table in (
                "catalog_snapshots",
                "catalog_tables",
                "catalog_columns",
                "catalog_relationships",
            ):
                result = await connection.execute(text(f"SELECT count(*) FROM {table}"))
                counts[table] = int(result.scalar_one())
            return counts
    finally:
        await engine.dispose()


async def _alter(customer: CustomerDatabase, statement: str) -> None:
    engine = create_async_engine(customer.url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(statement))
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# What a crawl finds
# ---------------------------------------------------------------------------


async def test_a_first_refresh_describes_the_whole_database(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, data_source_id = await _registered(migrated_database, isolated_customer_database)

    outcome = await discovery.discover(
        org_id=org_id, actor_user_id=None, data_source_id=data_source_id
    )

    assert outcome.changed is True
    assert outcome.snapshot is not None
    assert outcome.snapshot.version == 1
    assert outcome.snapshot.status == "active"
    assert outcome.snapshot.completed_at is not None

    catalog = await browse.active_catalog(org_id, data_source_id)
    names = {(table.schema_name, table.table_name): table for table in catalog.tables}
    assert set(names) == {
        ("public", "regions"),
        ("public", "shops"),
        ("public", "busy_shops"),
        ("public", "products"),
    }
    assert names[("public", "busy_shops")].kind == "view"
    assert names[("public", "regions")].kind == "table"
    assert names[("public", "regions")].description == "Sales regions."


async def test_columns_arrive_in_order_with_their_types_and_keys(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, data_source_id = await _registered(migrated_database, isolated_customer_database)
    await discovery.discover(org_id=org_id, actor_user_id=None, data_source_id=data_source_id)

    catalog = await browse.active_catalog(org_id, data_source_id)
    shops = next(table for table in catalog.tables if table.table_name == "shops")

    assert [column.name for column in shops.columns] == ["id", "region_id", "name", "opened_on"]
    assert [column.ordinal for column in shops.columns] == [1, 2, 3, 4]
    by_name = {column.name: column for column in shops.columns}
    assert by_name["id"].is_pk is True
    assert by_name["name"].is_pk is False
    assert by_name["name"].nullable is False
    assert by_name["opened_on"].nullable is True
    assert by_name["opened_on"].data_type == "date"
    assert by_name["name"].description == "Trading name."


async def test_the_declared_joins_are_recorded_and_the_absent_one_is_not(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """The assertion Phase 8 depends on, made at the catalog level.

    An agent asked "which products sell best" must be told the question cannot
    be answered here. That is only possible if the catalog records the absence
    of a join faithfully instead of offering a plausible one.
    """
    org_id, data_source_id = await _registered(migrated_database, isolated_customer_database)
    await discovery.discover(org_id=org_id, actor_user_id=None, data_source_id=data_source_id)

    catalog = await browse.active_catalog(org_id, data_source_id)

    edges = {
        (edge.from_table, edge.from_columns, edge.to_table, edge.to_columns)
        for edge in catalog.relationships
    }
    assert ("shops", ("region_id",), "regions", ("id",)) in edges
    assert all(edge.kind == "declared" for edge in catalog.relationships)
    assert all(edge.confidence == 1.0 for edge in catalog.relationships)

    touching_products = [
        edge for edge in catalog.relationships if "products" in (edge.from_table, edge.to_table)
    ]
    assert touching_products == [], "nothing links to products, and the catalog must say so"


# ---------------------------------------------------------------------------
# What a second crawl costs
# ---------------------------------------------------------------------------


async def test_a_refresh_that_finds_no_change_writes_nothing(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """The property the whole snapshot design turns on (DECISIONS D-012).

    Asserted by counting rows rather than by trusting the return value: "changed
    is false" is what the code says, and the row counts are what happened.
    """
    org_id, data_source_id = await _registered(migrated_database, isolated_customer_database)
    first = await discovery.discover(
        org_id=org_id, actor_user_id=None, data_source_id=data_source_id
    )
    before = await _counts(platform, org_id)

    second = await discovery.discover(
        org_id=org_id, actor_user_id=None, data_source_id=data_source_id
    )

    assert second.changed is False
    assert await _counts(platform, org_id) == before
    assert first.snapshot is not None and second.snapshot is not None
    assert second.snapshot.id == first.snapshot.id, "a new snapshot was built for no reason"
    assert second.snapshot.version == 1, "an unchanged database spent a version"


async def test_a_changed_table_builds_a_new_snapshot_and_retires_the_old_one(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, data_source_id = await _registered(migrated_database, isolated_customer_database)
    first = await discovery.discover(
        org_id=org_id, actor_user_id=None, data_source_id=data_source_id
    )

    await _alter(isolated_customer_database, "ALTER TABLE shops ADD COLUMN closed_on date")
    second = await discovery.discover(
        org_id=org_id, actor_user_id=None, data_source_id=data_source_id
    )

    assert second.changed is True
    assert second.snapshot is not None and first.snapshot is not None
    assert second.snapshot.version == 2
    assert second.snapshot.id != first.snapshot.id

    catalog = await browse.active_catalog(org_id, data_source_id)
    shops = next(table for table in catalog.tables if table.table_name == "shops")
    assert "closed_on" in {column.name for column in shops.columns}

    # The previous catalog is kept, not deleted: a run that started against it is
    # entitled to finish against it.
    engine = create_async_engine(platform)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            rows = (
                await connection.execute(
                    text("SELECT version, status FROM catalog_snapshots ORDER BY version")
                )
            ).all()
    finally:
        await engine.dispose()

    assert [(version, status) for version, status in rows] == [(1, "superseded"), (2, "active")]


async def test_a_dropped_table_counts_as_a_change(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """A hash comparison that only looked at tables it still finds would miss
    this, and the catalog would keep describing something that is gone."""
    org_id, data_source_id = await _registered(migrated_database, isolated_customer_database)
    await discovery.discover(org_id=org_id, actor_user_id=None, data_source_id=data_source_id)

    await _alter(isolated_customer_database, "DROP TABLE products")
    outcome = await discovery.discover(
        org_id=org_id, actor_user_id=None, data_source_id=data_source_id
    )

    assert outcome.changed is True
    catalog = await browse.active_catalog(org_id, data_source_id)
    assert "products" not in {table.table_name for table in catalog.tables}


# ---------------------------------------------------------------------------
# What it refuses, and what it remembers
# ---------------------------------------------------------------------------


async def test_an_unverified_data_source_is_declined_before_anything_is_opened(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, data_source_id = await _registered(
        migrated_database, isolated_customer_database, verify=False
    )

    outcome = await discovery.discover(
        org_id=org_id, actor_user_id=None, data_source_id=data_source_id
    )

    assert outcome.changed is False
    assert outcome.snapshot is None
    assert "not been proven read-only" in outcome.detail
    assert (await _counts(platform, org_id))["catalog_snapshots"] == 0


async def test_a_crawl_that_cannot_read_the_database_is_recorded_as_a_failure(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    """ "The last refresh failed, and here is why" is a thing a screen must be
    able to say — so a failure is a snapshot, not a lost exception."""
    org_id, data_source_id = await _registered(migrated_database, isolated_customer_database)
    await discovery.discover(org_id=org_id, actor_user_id=None, data_source_id=data_source_id)

    # Move it somewhere nothing answers, keeping the verification that lets
    # discovery run at all.
    engine = create_async_engine(migrated_database)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            await connection.execute(
                text(
                    "UPDATE data_sources SET settings = jsonb_set(settings, '{port}', '1') "
                    "WHERE id = :id"
                ),
                {"id": data_source_id},
            )
    finally:
        await engine.dispose()

    outcome = await discovery.discover(
        org_id=org_id, actor_user_id=None, data_source_id=data_source_id
    )

    assert outcome.changed is False
    assert outcome.snapshot is not None
    assert outcome.snapshot.status == "failed"
    assert outcome.snapshot.error
    assert isolated_customer_database.reader_password not in outcome.snapshot.error
    assert isolated_customer_database.reader_username not in outcome.snapshot.error

    # And the catalog that was already there still serves.
    catalog = await browse.active_catalog(org_id, data_source_id)
    assert catalog.snapshot.version == 1
    assert catalog.snapshot.status == "active"


async def test_browsing_before_any_refresh_says_what_to_do(
    platform: URL, migrated_database: URL, isolated_customer_database: CustomerDatabase
) -> None:
    org_id, data_source_id = await _registered(migrated_database, isolated_customer_database)

    with pytest.raises(browse.NoCatalogError, match="Refresh it"):
        await browse.active_catalog(org_id, data_source_id)


# ---------------------------------------------------------------------------
# The hash itself
# ---------------------------------------------------------------------------


def _table(name: str = "orders", kind: str = "table", comment: str | None = None) -> TableRef:
    return TableRef(schema="public", name=name, kind=kind, comment=comment)


def _column(
    name: str = "id",
    *,
    ordinal: int = 1,
    data_type: str = "integer",
    nullable: bool = False,
    is_primary_key: bool = True,
    comment: str | None = None,
) -> ColumnInfo:
    return ColumnInfo(
        schema="public",
        table="orders",
        name=name,
        data_type=data_type,
        nullable=nullable,
        ordinal=ordinal,
        is_primary_key=is_primary_key,
        comment=comment,
    )


def test_the_hash_does_not_depend_on_the_order_columns_arrive_in() -> None:
    """Two crawls of one unchanged table must agree, and a driver is not obliged
    to return rows in the same order twice."""
    columns = [_column("id", ordinal=1), _column("total", ordinal=2, is_primary_key=False)]

    assert discovery.structural_hash(_table(), columns) == discovery.structural_hash(
        _table(), list(reversed(columns))
    )


@pytest.mark.parametrize(
    "changed",
    [
        pytest.param([_column("id"), _column("total", ordinal=2)], id="a column was added"),
        pytest.param([_column("id", data_type="bigint")], id="a type changed"),
        pytest.param([_column("id", nullable=True)], id="nullability changed"),
        pytest.param([_column("id", is_primary_key=False)], id="a key changed"),
        pytest.param([_column("id", comment="the id")], id="a comment changed"),
        pytest.param([_column("identifier")], id="a column was renamed"),
    ],
)
def test_every_part_of_a_table_shape_is_in_the_hash(changed: list[ColumnInfo]) -> None:
    baseline = discovery.structural_hash(_table(), [_column("id")])

    assert discovery.structural_hash(_table(), changed) != baseline


def test_the_table_itself_is_in_the_hash() -> None:
    columns = [_column("id")]
    baseline = discovery.structural_hash(_table(), columns)

    assert discovery.structural_hash(_table(name="invoices"), columns) != baseline
    assert discovery.structural_hash(_table(kind="view"), columns) != baseline
    assert discovery.structural_hash(_table(comment="orders"), columns) != baseline
