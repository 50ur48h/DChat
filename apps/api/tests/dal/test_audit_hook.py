"""What the DAL writes down (architecture Part 8.2).

Against a real platform database, because the properties under test are the
ones a database enforces: the check constraint that a refusal must name what it
refused, the append-only audit trail, and row-level security on two new tenant
tables.

The question these rows exist to answer is *who accessed what, when, and was it
sensitive*. The test for that is not "a row was written" but "the row says the
right thing", so each case asserts the contents.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from catalog_fixture import build_source
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dataagent.connectors.base import ResultFrame
from dataagent.dal import audit_hook
from dataagent.dal.artifacts import LocalArtifactStore
from dataagent.dal.errors import PolicyViolation, ViolationCode
from dataagent.dal.executor import Execution
from dataagent.dal.masking import mask_frame, styles_for
from dataagent.dal.validator import validate
from dataagent.db import engine as engine_module
from dataagent.tenancy import session as session_module

PLANTED = "ada@lovelace.example.com"


@pytest.fixture
async def platform(
    app_database: URL, migrated_database: URL, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[URL]:
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


async def _org(migrated_database: URL) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """An organization with a user and a registered data source."""
    org_id, user_id, source_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    engine = create_async_engine(migrated_database)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            await connection.execute(
                text("INSERT INTO organizations (id, name) VALUES (:id, 'Recording')"),
                {"id": org_id},
            )
            await connection.execute(
                text("INSERT INTO users (id, external_subject, email) VALUES (:i, :s, :e)"),
                {"i": user_id, "s": f"sub-{user_id}", "e": "owner@example.com"},
            )
            await connection.execute(
                text(
                    "INSERT INTO data_sources "
                    "(id, org_id, name, engine, host_display, secret_ref) VALUES "
                    "(:id, :org, 'demo', 'pg', 'db:5432/pizza', :ref)"
                ),
                {"id": source_id, "org": org_id, "ref": f"ds/{org_id}/{source_id}/credentials"},
            )
    finally:
        await engine.dispose()
    return org_id, user_id, source_id


async def _rows(url: URL, statement: str, org_id: uuid.UUID) -> list[dict[str, object]]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, false)"), {"org": str(org_id)}
            )
            result = await connection.execute(text(statement))
            return [dict(row) for row in result.mappings().all()]
    finally:
        await engine.dispose()


def _execution(sql: str = "SELECT email FROM customers") -> Execution:
    """A real validation and a pretend engine answer."""
    source = build_source("pg")
    validated = validate(sql, source=source, max_rows=1000)
    frame = mask_frame(
        ResultFrame(
            columns=tuple(projection.name for projection in validated.projections),
            rows=((PLANTED,),),
            truncated=False,
            duration_ms=12,
        ),
        validated.projections,
        styles_for(source.catalog),
    )
    return Execution(validated=validated, frame=frame)


# --- the query that ran -----------------------------------------------------


async def test_a_successful_query_is_recorded_with_what_it_read(
    platform: URL, migrated_database: URL
) -> None:
    org_id, user_id, source_id = await _org(migrated_database)

    await audit_hook.record_success(
        org_id=org_id,
        data_source_id=source_id,
        actor_user_id=user_id,
        execution=_execution(),
    )

    rows = await _rows(platform, "SELECT * FROM query_executions", org_id)
    assert len(rows) == 1
    recorded = rows[0]
    assert recorded["status"] == "ok"
    assert recorded["tables"] == ["public.customers"]
    assert recorded["sensitive_accessed"] is True
    assert recorded["row_count"] == 1
    assert recorded["duration_ms"] == 12
    assert recorded["violation_code"] is None


async def test_the_stored_sample_is_the_masked_one(platform: URL, migrated_database: URL) -> None:
    """The rows persisted here have been through the masker. There is no
    unmasked copy of them in this database for a later bug to find — the same
    rule catalog samples follow (D-013)."""
    org_id, user_id, source_id = await _org(migrated_database)

    await audit_hook.record_success(
        org_id=org_id, data_source_id=source_id, actor_user_id=user_id, execution=_execution()
    )

    artifacts = await _rows(platform, "SELECT * FROM result_artifacts", org_id)
    assert artifacts[0]["sample_rows"] == [["a***@l***.com"]]
    assert artifacts[0]["summary"]["masked_columns"] == ["email"]  # pyright: ignore[reportIndexIssue]


async def test_nothing_unmasked_reaches_the_platform_database(
    platform: URL, migrated_database: URL
) -> None:
    """The blunt version of the same check: dump both tables as text and look
    for the planted address."""
    org_id, user_id, source_id = await _org(migrated_database)

    await audit_hook.record_success(
        org_id=org_id, data_source_id=source_id, actor_user_id=user_id, execution=_execution()
    )

    dumped = str(
        await _rows(
            platform,
            "SELECT e.*, a.* FROM query_executions e JOIN result_artifacts a "
            "ON a.query_execution_id = e.id",
            org_id,
        )
    )

    assert PLANTED not in dumped
    assert "lovelace" not in dumped


async def test_the_full_result_goes_to_the_store_and_the_row_points_at_it(
    platform: URL, migrated_database: URL, tmp_path: Path
) -> None:
    org_id, user_id, source_id = await _org(migrated_database)
    store = LocalArtifactStore(tmp_path)

    recorded = await audit_hook.record_success(
        org_id=org_id,
        data_source_id=source_id,
        actor_user_id=user_id,
        execution=_execution(),
        store=store,
    )

    assert recorded.storage_ref == f"{org_id}/{recorded.execution_id}.json"
    assert recorded.storage_ref is not None
    payload = await store.get(org_id=org_id, reference=recorded.storage_ref)
    assert payload is not None
    assert PLANTED not in payload.decode()

    artifacts = await _rows(platform, "SELECT storage_ref FROM result_artifacts", org_id)
    assert artifacts[0]["storage_ref"] == recorded.storage_ref


# --- the query that was refused ---------------------------------------------


async def test_a_refusal_is_recorded_with_its_code(platform: URL, migrated_database: URL) -> None:
    """The row this module exists for. A refused query reaches no engine, so
    there is no other trace of it anywhere — no connection, no server log, no
    latency graph."""
    org_id, user_id, source_id = await _org(migrated_database)
    violation = PolicyViolation(
        ViolationCode.DENIED_COLUMN, "not queryable", subject="public.customers.tax_id"
    )

    await audit_hook.record_refusal(
        org_id=org_id,
        data_source_id=source_id,
        actor_user_id=user_id,
        sql="SELECT tax_id FROM customers",
        violation=violation,
    )

    rows = await _rows(platform, "SELECT * FROM query_executions", org_id)
    assert rows[0]["status"] == "refused"
    assert rows[0]["violation_code"] == "denied_column"
    assert rows[0]["sql_text"] == "SELECT tax_id FROM customers"
    assert rows[0]["row_count"] is None


async def test_the_audit_trail_names_what_was_refused(
    platform: URL, migrated_database: URL
) -> None:
    org_id, user_id, source_id = await _org(migrated_database)

    await audit_hook.record_refusal(
        org_id=org_id,
        data_source_id=source_id,
        actor_user_id=user_id,
        sql="SELECT tax_id FROM customers",
        violation=PolicyViolation(
            ViolationCode.DENIED_COLUMN, "not queryable", subject="public.customers.tax_id"
        ),
    )

    audited = await _rows(platform, "SELECT * FROM audit_log", org_id)
    assert audited[0]["action"] == "dal.query_refused"
    details = audited[0]["details"]
    assert details["code"] == "denied_column"  # pyright: ignore[reportIndexIssue]
    assert details["subject"] == "public.customers.tax_id"  # pyright: ignore[reportIndexIssue]


async def test_a_row_cannot_claim_to_be_a_refusal_without_saying_what(
    platform: URL, migrated_database: URL
) -> None:
    """A constraint rather than a convention: a refusal with no code would be a
    row that answers none of the questions the table is read with."""
    org_id, _, source_id = await _org(migrated_database)
    engine = create_async_engine(platform)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            with pytest.raises(Exception, match="violation_code_matches_status"):
                await connection.execute(
                    text(
                        "INSERT INTO query_executions "
                        "(org_id, data_source_id, sql_text, sql_hash, status) VALUES "
                        "(:org, :ds, 'SELECT 1', 'abc', 'refused')"
                    ),
                    {"org": org_id, "ds": source_id},
                )
    finally:
        await engine.dispose()


# --- the query that failed --------------------------------------------------


async def test_a_failure_is_recorded_with_its_sanitized_reason(
    platform: URL, migrated_database: URL
) -> None:
    org_id, user_id, source_id = await _org(migrated_database)

    await audit_hook.record_failure(
        org_id=org_id,
        data_source_id=source_id,
        actor_user_id=user_id,
        sql="SELECT id FROM orders",
        sql_hash="abc123def456",
        error="the database refused the connection",
    )

    rows = await _rows(platform, "SELECT * FROM query_executions", org_id)
    assert rows[0]["status"] == "error"
    assert rows[0]["error"] == "the database refused the connection"
    assert rows[0]["violation_code"] is None


# --- the record outlives what it is about -----------------------------------


async def test_removing_a_data_source_keeps_the_record_of_what_it_read(
    platform: URL, migrated_database: URL
) -> None:
    """An audit trail that disappears when somebody deletes the thing it is
    about is not an audit trail."""
    org_id, user_id, source_id = await _org(migrated_database)
    await audit_hook.record_success(
        org_id=org_id, data_source_id=source_id, actor_user_id=user_id, execution=_execution()
    )

    engine = create_async_engine(migrated_database)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            await connection.execute(
                text("DELETE FROM data_sources WHERE id = :id"), {"id": source_id}
            )
    finally:
        await engine.dispose()

    rows = await _rows(platform, "SELECT * FROM query_executions", org_id)
    assert len(rows) == 1
    assert rows[0]["data_source_id"] is None
    assert rows[0]["tables"] == ["public.customers"]
