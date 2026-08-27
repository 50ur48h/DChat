"""Relationship inference decides on measurement, never on names (D-050, B-145).

The dataset that forced this feature also supplies its sharpest test, and both
halves are here as fakes so they run without a customer's database:

* `dim_outlet.outlet_key` and `fact_sale.outlet_key` — the parent is unique and
  every one of 112,327 child values matches. A key the database forgot to
  declare, and inference must find it.
* `map_item_key.item_key` and `dim_item.item_key` — the same column name on both
  sides and **0.0%** of rows match. Name matching would have invented this edge
  with total confidence, and inference must not.

An invented edge is worse than a missing one: a missing edge refuses, an
invented one answers, and the wrong join returns a cartesian product rather than
an error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, cast

import pytest

from dataagent.catalog.inference import MAX_PARENTS_PER_CHILD_COLUMN, infer_relationships
from dataagent.connectors.base import Caps, ColumnInfo, ExecLimits, ResultFrame, TableRef


@dataclass
class FakeConnector:
    """A database that answers the two questions inference asks.

    Built from a table of values rather than from expected SQL: a fake that
    matched on statement text would pass whatever the statement meant, which is
    the opposite of what these tests are for.
    """

    #: (schema, table) -> column -> the values in it, nulls included as None.
    data: dict[tuple[str, str], dict[str, list[object]]]
    statements: list[str] = field(default_factory=list[str])

    def capabilities(self) -> Caps:  # pragma: no cover - not consulted here
        raise NotImplementedError

    async def aclose(self) -> None:
        return None

    async def execute(self, query: Any, limits: ExecLimits) -> ResultFrame:
        sql = query.sql
        self.statements.append(sql)
        if sql.startswith("SELECT count(*) FROM") and "NOT EXISTS" in sql:
            return ResultFrame(
                columns=("count",),
                rows=((self._orphans(sql),),),
                truncated=False,
                duration_ms=0,
            )
        return ResultFrame(
            columns=("stats",),
            rows=(tuple(self._stats(sql)),),
            truncated=False,
            duration_ms=0,
        )

    # -- the answers, worked out from `data` -------------------------------

    @staticmethod
    def _relation_for(sql: str, alias: str) -> tuple[str, str]:
        """The table bound to `AS c` or `AS p`.

        Keyed on the alias rather than on "the first FROM": the probe wraps the
        child in a subquery, so positional parsing reads the wrapper. A fake
        that misreads the statement under test proves nothing about it.
        """
        match = re.search(rf'FROM\s+"([^"]+)"\."([^"]+)"\s+AS\s+{alias}(?![a-z])', sql)
        assert match, f"no relation for alias {alias} in: {sql}"
        return match.group(1), match.group(2)

    def _relation(self, sql: str, after: str) -> tuple[str, str]:
        piece = sql.split(after, 1)[1].strip().split(" ")[0]
        schema, table = piece.split(".")
        return schema.strip('"'), table.strip('"')

    def _stats(self, sql: str) -> list[int]:
        """`count(*)`, then non-null and distinct per column, in column order.

        Read from `data` rather than parsed back out of the projection. The
        first version of this fake did parse it, got `count(DISTINCT "k")`
        wrong, and produced red tests that looked like product failures.
        """
        schema, table = self._relation(sql, "FROM")
        columns = self.data[(schema, table)]
        total = max((len(values) for values in columns.values()), default=0)
        out = [total]
        for values in columns.values():
            non_null = [v for v in values if v is not None]
            out.append(len(non_null))
            out.append(len(set(non_null)))
        return out

    def _orphans(self, sql: str) -> int:
        """How many **distinct** child values the parent does not contain.

        Distinct, matching the statement under test: the scan walks
        `SELECT DISTINCT`, so a fake counting rows would report a different
        number for the same fact and the confidence scoring would drift with it.
        """
        assert "DISTINCT" in sql, f"the containment scan should be over distinct values: {sql}"
        child_schema, child_table = self._relation_for(sql, "c")
        parent_schema, parent_table = self._relation_for(sql, "p")
        child_col = sql.split("c.", 1)[1].split(" ")[0].strip('"')
        parent_col = sql.split("p.", 1)[1].split(" ")[0].strip('"')
        child = {v for v in self.data[(child_schema, child_table)][child_col] if v is not None}
        parent = {v for v in self.data[(parent_schema, parent_table)][parent_col] if v is not None}
        return sum(1 for value in child if value not in parent)


def _columns(schema: str, table: str, names: list[str]) -> tuple[ColumnInfo, ...]:
    return tuple(
        ColumnInfo(
            schema=schema,
            table=table,
            name=name,
            data_type="text",
            nullable=True,
            ordinal=index,
            is_primary_key=False,
        )
        for index, name in enumerate(names)
    )


MISEQ: dict[tuple[str, str], dict[str, list[object]]] = {
    # Five outlets, each key once — a parent.
    ("public", "dim_outlet"): {"outlet_key": list[object](["1", "2", "3", "4", "5"])},
    # Sales across those five, repeated — a child, fully contained.
    ("public", "fact_sale"): {"outlet_key": list[object](str(1 + i % 5) for i in range(200))},
    # The trap: same column name, disjoint values.
    ("public", "dim_item"): {"item_key": list[object](f"I{i}" for i in range(60))},
    ("public", "map_item_key"): {"item_key": list[object](f"WB-{i}" for i in range(60))},
}

TABLES = [TableRef(schema="public", name=name, kind="table") for _, name in MISEQ]
COLUMNS = {key: _columns(key[0], key[1], list(cols)) for key, cols in MISEQ.items()}


async def _infer(data: dict[tuple[str, str], dict[str, list[object]]]) -> Any:
    tables = [TableRef(schema=s, name=t, kind="table") for s, t in data]
    columns = {key: _columns(key[0], key[1], list(cols)) for key, cols in data.items()}
    return await infer_relationships(
        FakeConnector(data=data),  # type: ignore[arg-type]
        tables,
        columns,
        declared=(),
    )


async def test_it_finds_the_key_the_database_forgot_to_declare() -> None:
    report = await _infer({k: v for k, v in MISEQ.items() if k[1] in {"dim_outlet", "fact_sale"}})

    found = {(k.from_table, k.from_column, k.to_table, k.to_column) for k in report.keys}
    assert ("fact_sale", "outlet_key", "dim_outlet", "outlet_key") in found

    edge = next(k for k in report.keys if k.from_table == "fact_sale")
    # The direction is not a coin toss: the unique side is the parent, which is
    # what D-026's chasm reasoning needs to tell a narrowing hop from a fanning
    # one.
    assert edge.to_table == "dim_outlet"
    assert edge.evidence["orphans"] == 0
    assert edge.evidence["parent_unique"] is True
    assert edge.confidence >= 0.9


async def test_it_refuses_two_columns_that_only_share_a_name() -> None:
    """The test the owner set: 0.0% containment must produce no edge."""
    report = await _infer({k: v for k, v in MISEQ.items() if k[1] in {"dim_item", "map_item_key"}})

    found = {(k.from_table, k.from_column, k.to_table, k.to_column) for k in report.keys}
    assert ("map_item_key", "item_key", "dim_item", "item_key") not in found
    assert ("dim_item", "item_key", "map_item_key", "item_key") not in found


async def test_both_at_once_on_one_schema() -> None:
    """Together, because a rule that gets one right in isolation proves little."""
    report = await _infer(MISEQ)

    found = {(k.from_table, k.from_column, k.to_table, k.to_column) for k in report.keys}
    assert ("fact_sale", "outlet_key", "dim_outlet", "outlet_key") in found
    assert ("map_item_key", "item_key", "dim_item", "item_key") not in found


async def test_one_unmatched_value_is_enough_to_reject() -> None:
    """Containment is every row, not most of them.

    An edge that is 99% contained is a join that silently drops rows — a wrong
    answer with no symptom, which is worse than a refusal.
    """
    data: dict[tuple[str, str], dict[str, list[object]]] = {
        ("public", "dim_outlet"): {"outlet_key": list[object](["1", "2", "3", "4", "5"])},
        # Five distinct values, one of which the parent has never heard of. The
        # count matches the parent's, so this reaches the containment scan
        # instead of being filtered on arithmetic — which is the point: it is
        # the *measurement* that must reject it.
        ("public", "fact_sale"): {
            "outlet_key": list[object]([*(str(1 + i % 4) for i in range(199)), "99"]),
        },
    }
    report = await _infer(data)

    assert report.keys == ()


async def test_a_parent_with_duplicates_is_not_a_parent() -> None:
    """Without uniqueness the edge is not many-to-one and has no direction."""
    data: dict[tuple[str, str], dict[str, list[object]]] = {
        ("public", "lookup"): {"code": list[object](["a", "a", "b"])},
        ("public", "usage"): {"code": list[object](["a", "b", "a"])},
    }
    report = await _infer(data)

    assert report.keys == ()


async def test_too_few_rows_scores_below_the_threshold() -> None:
    """Three values that happen to match are a coincidence, not a key.

    Recorded rather than discarded — the measurement happened and is worth
    keeping — but scored below what the capability check will rely on.
    """
    data: dict[tuple[str, str], dict[str, list[object]]] = {
        ("public", "tiny_parent"): {"k": list[object](["a", "b"])},
        ("public", "tiny_child"): {"k": list[object](["a", "b", "a"])},
    }
    report = await _infer(data)

    assert [k.confidence for k in report.keys] == [pytest.approx(0.80)]


# ---------------------------------------------------------------------------
# What the first live run got wrong (D-050)
# ---------------------------------------------------------------------------


async def test_a_small_domain_inside_a_dense_range_is_not_a_key() -> None:
    """**The defect the miseq run actually found, and the reason for coverage.**

    Containment was implemented, correct, and worthless on its own. `outlet_key`
    holds five values, `1` to `5`, and every dense integer range contains them —
    so the first run against the real database inferred `outlet_key` into
    `fact_transfer.transfer_id` (1 to 9), `fact_sale.sale_id` (1 to 112,327) and
    seven more. Every one of those measurements was true. Eight of the ten edges
    it produced were rubbish.

    Coverage is what makes the difference measurable rather than obvious: five
    values account for a five-row parent completely and for a hundred-row parent
    not at all.
    """
    data: dict[tuple[str, str], dict[str, list[object]]] = {
        ("public", "orders"): {"outlet_key": list[object](str(1 + i % 5) for i in range(200))},
        ("public", "receipts"): {"receipt_id": list[object](str(i) for i in range(1, 101))},
    }
    report = await _infer(data)

    assert report.keys == (), "1..5 fits inside 1..100 and means nothing"


async def test_the_better_covered_parent_wins_and_names_the_other() -> None:
    """Two parents contain the child; only one accounts for itself.

    This is the miseq pair exactly: `outlet_key` is contained in `dim_outlet`
    (five of five) and in a nine-row surrogate key (five of nine). Both scans
    return zero orphans, so containment cannot separate them and a column name
    is not allowed to. Coverage can, and the loser is recorded rather than
    silently dropped — a wrong edge should be a question about numbers.
    """
    data: dict[tuple[str, str], dict[str, list[object]]] = {
        ("public", "fact_sale"): {"outlet_key": list[object](str(1 + i % 9) for i in range(200))},
        ("public", "dim_outlet"): {"outlet_key": list[object](str(i) for i in range(1, 10))},
        # Ten values, so the child accounts for nine of them: past the floor, and
        # still beaten by the parent it explains completely.
        ("public", "fact_transfer"): {
            "transfer_id": list[object](str(i) for i in range(1, 11)),
        },
    }
    report = await _infer(data)

    edges = {(k.from_table, k.to_table) for k in report.keys}
    assert edges == {("fact_sale", "dim_outlet")}

    edge = next(k for k in report.keys if k.from_table == "fact_sale")
    assert edge.evidence["coverage"] == 1.0
    runners_up = cast(list[dict[str, object]], edge.evidence["runners_up"])
    assert [entry["table"] for entry in runners_up] == ["fact_transfer"]


async def test_a_column_that_fits_everywhere_is_skipped_rather_than_resolved() -> None:
    """A generic domain is not a key into whichever table sorts first.

    Resolution compares candidates, so it is only trustworthy when they all ran.
    Past the cap the honest move is to measure nothing and say why, rather than
    to crown a winner from whichever subset the budget happened to reach.
    """
    data: dict[tuple[str, str], dict[str, list[object]]] = {
        ("public", "usage"): {"code": list[object](str(1 + i % 3) for i in range(50))},
    }
    for index in range(MAX_PARENTS_PER_CHILD_COLUMN + 1):
        data[("public", f"lookup_{index}")] = {
            f"id_{index}": list[object](["1", "2", "3"]),
        }
    report = await _infer(data)

    assert report.keys == ()
    assert any("identifies none of them" in note for note in report.notes)
