"""The path from a statement to rows (architecture Part 7.1, steps 4 to 6).

The connector is a stand-in here — what it would send to a real engine is
recorded rather than executed — because what these tests are about is what the
DAL *does* with a statement and with the answer, and both are decided before any
network is involved. The connectors have their own suites against real servers.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

import pytest
from catalog_fixture import build_source

from dataagent.connectors.base import Caps, ConnectorError, ExecLimits, ResultFrame, ValidatedQuery
from dataagent.dal import executor
from dataagent.dal import policy as policy_module
from dataagent.dal.errors import PolicyViolation, ViolationCode
from dataagent.dal.masking import REDACTED
from dataagent.dal.policy import SourcePolicy, invalidate_all

PLANTED = "ada@lovelace.example.com"


class RecordingConnector:
    """Accepts a ValidatedQuery, records it, and answers with what it was given."""

    def __init__(self, frame: ResultFrame | None = None, error: Exception | None = None) -> None:
        self.frame = frame or ResultFrame(columns=(), rows=(), truncated=False, duration_ms=3)
        self.error = error
        self.seen: list[tuple[str, ExecLimits]] = []
        self.closed = False

    def capabilities(self) -> Caps:  # pragma: no cover - not exercised here
        raise NotImplementedError

    async def aclose(self) -> None:
        self.closed = True

    async def execute(self, query: ValidatedQuery, limits: ExecLimits) -> ResultFrame:
        self.seen.append((query.sql, limits))
        if self.error is not None:
            raise self.error
        return self.frame

    # The rest of the Connector protocol. Stubbed rather than left off, so that
    # pyright checks this double against the same interface a real connector
    # implements — a test double that has quietly drifted from the protocol is a
    # test that proves something about nothing.
    async def test_connection(self) -> Any:  # pragma: no cover - not exercised here
        raise NotImplementedError

    async def list_schemas(self) -> list[str]:  # pragma: no cover
        raise NotImplementedError

    async def list_tables(self, schemas: Sequence[str]) -> list[Any]:  # pragma: no cover
        raise NotImplementedError

    async def list_columns(self, schemas: Sequence[str]) -> list[Any]:  # pragma: no cover
        raise NotImplementedError

    async def list_foreign_keys(self, schemas: Sequence[str]) -> list[Any]:  # pragma: no cover
        raise NotImplementedError


@pytest.fixture(autouse=True)
def stub_policy(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> SourcePolicy:
    """`run` loads its own policy, so the loader is what gets stubbed — the test
    then exercises the real validate → execute → mask chain end to end."""
    invalidate_all()
    source = build_source(getattr(request, "param", "pg"), max_rows=1000)

    async def fake_policy(
        org_id: uuid.UUID, data_source_id: uuid.UUID, **_: object
    ) -> SourcePolicy:
        return source

    monkeypatch.setattr(executor, "source_policy", fake_policy)
    monkeypatch.setattr(policy_module, "source_policy", fake_policy)
    return source


async def run(sql: str, connector: RecordingConnector, **kwargs: object) -> executor.Execution:
    return await executor.execute(
        org_id=uuid.uuid4(),
        data_source_id=uuid.uuid4(),
        sql=sql,
        connector=connector,
        **kwargs,  # pyright: ignore[reportArgumentType]
    )


# --- what is sent -----------------------------------------------------------


async def test_the_connector_receives_the_canonical_statement() -> None:
    connector = RecordingConnector()

    execution = await run("select id from orders", connector)

    sent, _ = connector.seen[0]
    assert sent == execution.sql
    assert '"public"."orders"' in sent


async def test_the_row_cap_is_written_into_the_sql_and_into_the_fetch() -> None:
    """Twice, in two places: the engine stops early, and the read stops too. A
    query that would scan a hundred million rows should be cheap for the
    customer's server, not merely survivable for ours."""
    connector = RecordingConnector()

    execution = await run("SELECT id FROM orders", connector)

    sent, limits = connector.seen[0]
    assert "LIMIT 1000" in sent or "TOP 1000" in sent
    assert limits.max_rows == 1000
    assert execution.validated.row_limit == 1000


async def test_a_caller_may_ask_for_fewer_rows() -> None:
    connector = RecordingConnector()

    await run("SELECT id FROM orders", connector, max_rows=5)

    sent, limits = connector.seen[0]
    assert "LIMIT 5" in sent or "TOP 5" in sent
    assert limits.max_rows == 5


async def test_a_caller_may_not_ask_for_more_than_policy_allows() -> None:
    """Not an error — an agent guessing at a number — so it is held to the
    ceiling rather than refused."""
    connector = RecordingConnector()

    await run("SELECT id FROM orders", connector, max_rows=1_000_000)

    sent, limits = connector.seen[0]
    assert "LIMIT 1000" in sent or "TOP 1000" in sent
    assert limits.max_rows == 1000


async def test_a_smaller_limit_in_the_query_is_kept() -> None:
    connector = RecordingConnector()

    await run("SELECT id FROM orders LIMIT 10", connector)

    sent, _ = connector.seen[0]
    assert "LIMIT 10" in sent or "TOP 10" in sent


async def test_the_deadline_comes_from_policy() -> None:
    connector = RecordingConnector()

    await run("SELECT id FROM orders", connector)

    _, limits = connector.seen[0]
    assert limits.timeout_seconds == 30.0


# --- what comes back --------------------------------------------------------


async def test_a_masked_column_is_masked_before_the_caller_sees_it() -> None:
    connector = RecordingConnector(
        ResultFrame(columns=("email",), rows=((PLANTED,),), truncated=False, duration_ms=7)
    )

    execution = await run("SELECT email FROM customers", connector)

    assert execution.frame.rows == (("a***@l***.com",),)
    assert PLANTED not in str(execution.frame.rows)
    assert execution.frame.masked_columns == ("email",)


async def test_a_truncated_result_says_so() -> None:
    connector = RecordingConnector(
        ResultFrame(columns=("id",), rows=((1,), (2,)), truncated=True, duration_ms=9)
    )

    execution = await run("SELECT id FROM orders", connector)

    assert execution.truncated
    assert execution.row_count == 2
    assert execution.duration_ms == 9


async def test_the_execution_carries_what_an_audit_row_needs() -> None:
    """Assembled from the validation rather than remembered separately: an audit
    row built from a copy is one that can disagree with what ran."""
    connector = RecordingConnector(
        ResultFrame(columns=("email",), rows=((PLANTED,),), truncated=False, duration_ms=2)
    )

    execution = await run("SELECT email FROM customers", connector)

    assert [str(table) for table in execution.validated.tables] == ["public.customers"]
    assert execution.sensitive_accessed
    assert len(execution.sql_hash) == 12


async def test_a_query_touching_nothing_sensitive_says_that_too() -> None:
    connector = RecordingConnector(
        ResultFrame(columns=("id",), rows=((1,),), truncated=False, duration_ms=1)
    )

    execution = await run("SELECT id FROM orders", connector)

    assert not execution.sensitive_accessed
    assert execution.frame.masked_columns == ()


# --- what never happens -----------------------------------------------------


async def test_a_refused_statement_never_reaches_the_connector() -> None:
    """A rejected query costs the customer's database nothing — not a
    connection, not a round trip."""
    connector = RecordingConnector()

    with pytest.raises(PolicyViolation) as caught:
        await run("SELECT tax_id FROM customers", connector)

    assert caught.value.code is ViolationCode.DENIED_COLUMN
    assert connector.seen == []


async def test_a_connector_failure_is_not_wrapped_in_anything_new() -> None:
    """`ConnectorError` is already sanitized. Re-raising it as something else
    would mean re-deciding what is safe to say, in a second place."""
    connector = RecordingConnector(error=ConnectorError("could not connect"))

    with pytest.raises(ConnectorError):
        await run("SELECT id FROM orders", connector)


async def test_a_supplied_connector_is_left_open_for_its_owner() -> None:
    connector = RecordingConnector()

    await run("SELECT id FROM orders", connector)

    assert not connector.closed


@pytest.mark.parametrize("stub_policy", ["pg", "mssql"], indirect=True)
async def test_both_engines_run_the_same_path(stub_policy: SourcePolicy) -> None:
    """One code path, two spellings — the difference is entirely in `Caps`."""
    connector = RecordingConnector(
        ResultFrame(columns=("email",), rows=((PLANTED,),), truncated=False, duration_ms=1)
    )

    execution = await run("SELECT email FROM customers", connector)
    sent, _ = connector.seen[0]

    assert execution.frame.rows == (("a***@l***.com",),)
    if stub_policy.dialect == "tsql":
        assert "TOP 1000" in sent and "[public].[customers]" in sent
    else:
        assert "LIMIT 1000" in sent and '"public"."customers"' in sent


async def test_the_masked_frame_is_what_a_caller_gets_even_when_the_result_is_odd() -> None:
    """Fails closed: a result whose shape does not match the statement is masked
    entirely rather than passed through on the assumption it is fine."""
    connector = RecordingConnector(
        ResultFrame(columns=("id", "email"), rows=((1, PLANTED),), truncated=False, duration_ms=1)
    )

    execution = await run("SELECT id FROM orders", connector)

    assert execution.frame.rows == ((REDACTED, REDACTED),)


async def test_a_connector_opened_here_is_closed_even_when_the_query_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A session left open is left open on somebody else's server, so the close
    is in a `finally` — and a failing query is exactly when it gets forgotten."""
    connector = RecordingConnector(error=ConnectorError("gone away"))

    async def fake_view(org_id: uuid.UUID, data_source_id: uuid.UUID) -> object:
        return object()

    async def fake_connector(view: object, provider: object | None = None) -> RecordingConnector:
        return connector

    monkeypatch.setattr(executor.datasources, "get_data_source", fake_view)
    monkeypatch.setattr(executor.datasources, "connector_for_view", fake_connector)

    with pytest.raises(ConnectorError):
        await executor.execute(
            org_id=uuid.uuid4(), data_source_id=uuid.uuid4(), sql="SELECT id FROM orders"
        )

    assert connector.closed


async def test_a_connector_opened_here_is_closed_after_a_good_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = RecordingConnector(
        ResultFrame(columns=("id",), rows=((1,),), truncated=False, duration_ms=1)
    )

    async def fake_view(org_id: uuid.UUID, data_source_id: uuid.UUID) -> object:
        return object()

    async def fake_connector(view: object, provider: object | None = None) -> RecordingConnector:
        return connector

    monkeypatch.setattr(executor.datasources, "get_data_source", fake_view)
    monkeypatch.setattr(executor.datasources, "connector_for_view", fake_connector)

    execution = await executor.execute(
        org_id=uuid.uuid4(), data_source_id=uuid.uuid4(), sql="SELECT id FROM orders"
    )

    assert execution.row_count == 1
    assert connector.closed
