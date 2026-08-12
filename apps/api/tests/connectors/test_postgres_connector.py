"""The Postgres connector against a real database.

Two questions run through all of it: does it describe the database accurately,
and can it be made to write? The second one is the reason this work package
exists — `readonly_verified` is a claim the product makes to an administrator,
and it has to be earned.
"""

from __future__ import annotations

import pytest

from customer_db import CustomerDatabase
from dataagent.connectors import introspection
from dataagent.connectors.base import (
    ConnectorError,
    ExecLimits,
    PolicyGrant,
    ValidatedQuery,
)
from dataagent.connectors.postgres import PostgresConnector

# Bound to names rather than written inline at the call site: a literal beside
# a `password=` argument is what a secret scanner and ruff's S106 are for, and
# neither should have to learn to ignore this file.
UNUSED_LOGIN = "unused-capabilities-never-connect"
WRONG_LOGIN = "definitely-not-the-password"


def _forge(sql: str) -> ValidatedQuery:
    """Build a query the way only the SQL policy module may.

    A test is allowed to do this and product code is not, which is exactly the
    line the gate draws: ``test_only_sanctioned_modules_build_queries`` scans
    ``src`` and would fail on this call if it lived there.
    """
    return ValidatedQuery(
        PolicyGrant("dataagent.connectors.introspection"), sql=sql, dialect="postgres"
    )


# ---------------------------------------------------------------------------
# Describing the database
# ---------------------------------------------------------------------------


async def test_capabilities_state_engine_truth_rather_than_guessing_it() -> None:
    caps = PostgresConnector(
        host="h", port=5432, database="d", username="u", password=UNUSED_LOGIN
    ).capabilities()

    assert caps.dialect == "postgres"
    assert caps.limit_syntax == "limit"
    assert caps.max_identifier_length == 63


async def test_it_lists_the_schemas_a_reader_may_see(customer_database: CustomerDatabase) -> None:
    async with customer_database.reader() as connector:
        schemas = await connector.list_schemas()

    assert "public" in schemas
    assert not [schema for schema in schemas if schema.startswith("pg_")]
    assert "information_schema" not in schemas


async def test_it_lists_tables_and_views_with_their_comments(
    customer_database: CustomerDatabase,
) -> None:
    async with customer_database.reader() as connector:
        tables = await connector.list_tables(["public"])

    by_name = {table.name: table for table in tables}
    assert set(by_name) == {"regions", "shops", "busy_shops"}
    assert by_name["regions"].kind == "table"
    assert by_name["busy_shops"].kind == "view"
    assert by_name["regions"].comment == "Sales regions."


async def test_it_lists_columns_with_types_nullability_and_keys(
    customer_database: CustomerDatabase,
) -> None:
    async with customer_database.reader() as connector:
        columns = await connector.list_columns(["public"])

    shops = {column.name: column for column in columns if column.table == "shops"}
    assert [column.name for column in columns if column.table == "shops"] == [
        "id",
        "region_id",
        "name",
        "opened_on",
    ], "columns must come back in ordinal order"
    assert shops["id"].is_primary_key is True
    assert shops["name"].is_primary_key is False
    assert shops["name"].nullable is False
    assert shops["opened_on"].nullable is True
    assert shops["opened_on"].data_type == "date"
    assert shops["name"].comment == "Trading name."


async def test_it_finds_the_foreign_key_graph(customer_database: CustomerDatabase) -> None:
    """Phase 4's join graph is built from exactly this."""
    async with customer_database.reader() as connector:
        keys = await connector.list_foreign_keys(["public"])

    assert len(keys) == 1
    key = keys[0]
    assert (key.from_table, key.from_columns) == ("shops", ("region_id",))
    assert (key.to_table, key.to_columns) == ("regions", ("id",))


# ---------------------------------------------------------------------------
# Bounded execution
# ---------------------------------------------------------------------------


async def test_a_result_is_cut_to_the_limit_and_says_so(
    customer_database: CustomerDatabase,
) -> None:
    async with customer_database.reader() as connector:
        frame = await connector.execute(_forge("SELECT id FROM shops ORDER BY id"), ExecLimits(2))

    assert frame.rows == ((1,), (2,))
    assert frame.truncated is True


async def test_a_result_within_the_limit_is_not_marked_truncated(
    customer_database: CustomerDatabase,
) -> None:
    async with customer_database.reader() as connector:
        frame = await connector.execute(_forge("SELECT id FROM shops ORDER BY id"), ExecLimits(50))

    assert len(frame.rows) == 5
    assert frame.truncated is False


async def test_an_empty_result_still_describes_its_shape(
    customer_database: CustomerDatabase,
) -> None:
    """Column names come from the statement, not from the first row."""
    async with customer_database.reader() as connector:
        frame = await connector.execute(
            _forge("SELECT id, name FROM shops WHERE false"), ExecLimits(5)
        )

    assert frame.columns == ("id", "name")
    assert frame.rows == ()


async def test_a_slow_query_is_stopped_by_the_statement_timeout(
    customer_database: CustomerDatabase,
) -> None:
    async with customer_database.reader() as connector:
        with pytest.raises(ConnectorError):
            await connector.execute(
                _forge("SELECT pg_sleep(5)"), ExecLimits(max_rows=1, timeout_seconds=0.1)
            )


async def test_the_session_refuses_a_write_even_before_privileges_are_considered(
    customer_database: CustomerDatabase,
) -> None:
    """The owner *can* write; on this session, they still cannot."""
    async with customer_database.owner() as connector:
        with pytest.raises(ConnectorError, match="read-only"):
            await connector.execute(
                _forge("INSERT INTO regions VALUES (99, 'Forged')"), ExecLimits(1)
            )


# ---------------------------------------------------------------------------
# Verification — the point of the work package
# ---------------------------------------------------------------------------


async def test_a_read_only_login_is_verified(customer_database: CustomerDatabase) -> None:
    async with customer_database.reader() as connector:
        health = await connector.test_connection()

    assert health.reachable is True
    assert health.readonly_verified is True
    assert "cannot write" in health.detail
    assert health.server_version is not None and health.server_version.startswith("PostgreSQL")
    assert any("refused" in note for note in health.evidence)


async def test_an_owner_login_is_not_verified_and_the_reason_is_named(
    customer_database: CustomerDatabase,
) -> None:
    """The failure mode this exists to catch: registering with the wrong account."""
    async with customer_database.owner() as connector:
        health = await connector.test_connection()

    assert health.reachable is True
    assert health.readonly_verified is False
    assert "not read-only" in health.detail
    assert health.evidence


async def test_verification_never_names_the_role_it_connected_as(
    customer_database: CustomerDatabase,
) -> None:
    """Architecture 7.3: a response may carry the last four characters, no more."""
    async with customer_database.reader() as connector:
        health = await connector.test_connection()

    rendered = health.detail + " ".join(health.evidence)
    assert customer_database.reader_username not in rendered
    assert customer_database.reader_password not in rendered


async def test_the_write_probe_leaves_nothing_behind(customer_database: CustomerDatabase) -> None:
    """It runs as the owner too, where it succeeds — and is still rolled back."""
    async with customer_database.owner() as connector:
        await connector.test_connection()

        frame = await connector.execute(
            _forge(
                "SELECT count(*) FROM pg_class WHERE relname = "
                f"'{introspection.READONLY_PROBE_TABLE}'"
            ),
            ExecLimits(1),
        )

    assert frame.rows == ((0,),), "the write probe committed something"


async def test_the_owner_probe_reports_that_it_could_write(
    customer_database: CustomerDatabase,
) -> None:
    async with customer_database.owner() as connector:
        health = await connector.test_connection()

    assert any("can write" in note for note in health.evidence)


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


async def test_a_wrong_password_fails_without_quoting_the_credential(
    customer_database: CustomerDatabase,
) -> None:
    connector = PostgresConnector(
        host=customer_database.host,
        port=customer_database.port,
        database=customer_database.database,
        username=customer_database.reader_username,
        password=WRONG_LOGIN,
    )

    async with connector:
        health = await connector.test_connection()

    assert health.reachable is False
    assert health.readonly_verified is False
    assert WRONG_LOGIN not in health.detail
    assert customer_database.reader_username not in health.detail


async def test_a_database_that_does_not_exist_is_reported_not_guessed(
    customer_database: CustomerDatabase,
) -> None:
    connector = PostgresConnector(
        host=customer_database.host,
        port=customer_database.port,
        database="no_such_database_here",
        username=customer_database.reader_username,
        password=customer_database.reader_password,
    )

    async with connector:
        health = await connector.test_connection()

    assert health.reachable is False
    assert health.readonly_verified is False
    assert "no_such_database_here" not in health.detail


async def test_closing_twice_is_harmless(customer_database: CustomerDatabase) -> None:
    connector = customer_database.reader()
    await connector.list_schemas()

    await connector.aclose()
    await connector.aclose()
