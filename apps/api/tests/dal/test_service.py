"""The front door: `dal.run` records on every path (architecture Part 7.1).

No database here on purpose. What is under test is the *wiring* — which recorder
runs for which outcome, and that none of them can be skipped — and that is a
property of this module rather than of any table. The recorders themselves are
tested against a real database in `test_audit_hook.py`.
"""

from __future__ import annotations

import uuid
from contextlib import suppress
from typing import Any

import pytest
from catalog_fixture import build_source

from dataagent.connectors.base import ConnectorError, ResultFrame
from dataagent.dal import service
from dataagent.dal.errors import PolicyViolation, ViolationCode
from dataagent.dal.executor import Execution
from dataagent.dal.masking import mask_frame, styles_for
from dataagent.dal.validator import validate


class Recorder:
    """Stands in for the three recorders and remembers which one was called."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def hook(self, name: str) -> Any:
        async def record(**kwargs: Any) -> Any:
            self.calls.append((name, kwargs))
            return None

        return record

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recording = Recorder()
    for name in ("record_success", "record_failure", "record_refusal"):
        monkeypatch.setattr(service.audit_hook, name, recording.hook(name))
    return recording


def _execution(sql: str = "SELECT id FROM orders") -> Execution:
    source = build_source("pg")
    validated = validate(sql, source=source, max_rows=1000)
    frame = mask_frame(
        ResultFrame(
            columns=tuple(projection.name for projection in validated.projections),
            rows=((1,),),
            truncated=False,
            duration_ms=4,
        ),
        validated.projections,
        styles_for(source.catalog),
    )
    return Execution(validated=validated, frame=frame)


def _executes(monkeypatch: pytest.MonkeyPatch, outcome: Execution | Exception) -> None:
    async def execute(**kwargs: Any) -> Execution:
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(service, "execute", execute)


async def run(**kwargs: Any) -> Execution:
    return await service.run(
        org_id=uuid.uuid4(), data_source_id=uuid.uuid4(), sql="SELECT id FROM orders", **kwargs
    )


async def test_a_query_that_ran_is_recorded_as_a_success(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    _executes(monkeypatch, _execution())

    execution = await run(store=object())  # pyright: ignore[reportArgumentType]

    assert recorder.names == ["record_success"]
    assert execution.row_count == 1


async def test_a_refusal_is_recorded_and_then_re_raised(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caller gets exactly the violation it would have got without any of
    this, and the trail has the row either way."""
    violation = PolicyViolation(ViolationCode.DENIED_COLUMN, "no", subject="a.b.c")
    _executes(monkeypatch, violation)

    with pytest.raises(PolicyViolation) as caught:
        await run(store=object())  # pyright: ignore[reportArgumentType]

    assert caught.value is violation
    assert recorder.names == ["record_refusal"]


async def test_a_connector_failure_is_recorded_and_then_re_raised(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    _executes(monkeypatch, ConnectorError("gone"))

    with pytest.raises(ConnectorError):
        await run(store=object())  # pyright: ignore[reportArgumentType]

    assert recorder.names == ["record_failure"]


async def test_the_refusal_row_carries_the_submitted_sql(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused statement has no canonical form — canonicalising is what it did
    not get to — so what is stored is what was asked for."""
    _executes(monkeypatch, PolicyViolation(ViolationCode.SYSTEM_SCHEMA, "no", subject="pg_catalog"))

    with pytest.raises(PolicyViolation):
        await service.run(
            org_id=uuid.uuid4(),
            data_source_id=uuid.uuid4(),
            sql="SELECT * FROM pg_catalog.pg_user",
            store=object(),  # pyright: ignore[reportArgumentType]
        )

    _, kwargs = recorder.calls[0]
    assert kwargs["sql"] == "SELECT * FROM pg_catalog.pg_user"
    assert kwargs["violation"].code is ViolationCode.SYSTEM_SCHEMA


async def test_the_failure_row_carries_a_hash_of_what_was_sent(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    _executes(monkeypatch, ConnectorError("gone"))

    with pytest.raises(ConnectorError):
        await run(store=object())  # pyright: ignore[reportArgumentType]

    _, kwargs = recorder.calls[0]
    assert len(kwargs["sql_hash"]) == 12


async def test_who_ran_it_and_which_run_are_carried_through(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`who` is what makes the trail answer architecture 8.2's question, and it
    is the field most easily lost between layers."""
    _executes(monkeypatch, _execution())
    actor, run_id = uuid.uuid4(), uuid.uuid4()

    await run(actor_user_id=actor, run_id=run_id, store=object())  # pyright: ignore[reportArgumentType]

    _, kwargs = recorder.calls[0]
    assert kwargs["actor_user_id"] == actor
    assert kwargs["run_id"] == run_id


async def test_every_outcome_writes_exactly_one_record(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not two, and never none. A second row would double-count an access; none
    would leave a query invisible."""
    for outcome in (
        _execution(),
        PolicyViolation(ViolationCode.DENIED_COLUMN, "no"),
        ConnectorError("gone"),
    ):
        recorder.calls.clear()
        _executes(monkeypatch, outcome)
        with suppress(PolicyViolation, ConnectorError):
            await run(store=object())  # pyright: ignore[reportArgumentType]

        assert len(recorder.calls) == 1
