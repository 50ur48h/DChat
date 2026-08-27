"""Join relationships a database does not declare, found by measuring (B-145).

Most customer databases declare no foreign keys. `miseq` declares **none at
all** while joining `dim_outlet` to `fact_sale` across 112,327 of 112,327 rows,
and an empty relationship graph made the capability check refuse nearly every
real question. What is missing is not a rule; it is knowledge.

**Evidence, never names.** A column called `item_key` on one table and a column
called `item_key` on another look like a foreign key and, in this very dataset,
are not: `map_item_key.item_key` matches `dim_item.item_key` on **0.0%** of rows.
Name matching would have invented that edge with total confidence, and an
invented edge is worse than a missing one — a missing edge refuses, an invented
one answers, and a wrong join returns a cartesian product rather than an error.
So nothing here reads a column name. Two measurements decide:

1. **The parent side is unique.** Every non-null value appears once. Without
   this the edge is not many-to-one, and D-026's chasm-trap reasoning — which
   depends on knowing which direction narrows — has nothing to stand on.
2. **The child side is contained.** *Every* non-null child value exists in the
   parent. Not most: an edge that is 99% contained is a join that silently drops
   rows, which is a wrong answer with no symptom.

Both are exact queries against the customer's database, not the profiler's
sample. The profiler reads the **first** `max_rows` rows and computes "distinct"
within them, which is a floor rather than a fact — enough to describe a column,
never enough to assert a key.

**Everything measured is stored** (`catalog_relationships.evidence`). A wrong
edge should be traceable to the numbers that produced it rather than argued
about, and the numbers are also what a later change has to beat.

**Bounded like the profiler, and for the same reason.** Containment is a scan of
the child table, so the work is capped by pairs and by a wall clock, and stopping
early is a normal outcome rather than an error: fewer inferred edges means more
honest refusals, which is the safe direction to fail in.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from dataagent.connectors.base import ColumnInfo, Connector, ExecLimits, TableRef
from dataagent.connectors.introspection import (
    POSTGRES,
    pg_column_stats,
    pg_orphan_count,
    tsql_column_stats,
    tsql_orphan_count,
)

#: **The SQL lives in `introspection`, not here.** Only sanctioned modules may
#: declare a statement validated (`PolicyGrant`, architecture 5.1 and 7.5), and
#: widening that list for a new caller would trade a deliberate boundary for a
#: convenience. This module decides *what to measure*; that one owns *how it is
#: written*, which is also where a reader can check both dialects side by side.

#: Below this the check will not rely on an edge (`capability.MIN_CONFIDENCE`).
#: Recorded anyway: an edge that was measured and judged too weak is a fact worth
#: keeping, and a silent omission is the thing this module exists to end.
_STRONG = 0.95
_ADEQUATE = 0.92
_TOO_LITTLE_DATA = 0.80

#: How many non-null child values make containment mean something. Three rows
#: that all happen to match is a coincidence; a hundred thousand is a key.
_CONFIDENT_ROWS = 100
_MINIMUM_ROWS = 10

#: The work cap. Each candidate costs one pass over the child column's distinct
#: values, so this is the number that decides whether discovery finishes in
#: seconds or minutes.
MAX_PAIRS_CHECKED = 400
#: Raised from 120 once the containment scan started walking distinct values
#: instead of rows. It is still not enough for `miseq`, which stops early at 61
#: of 97 candidates — an honest partial sweep, and B-146 carries it.
BUDGET_SECONDS = 240.0

#: **How much of the parent the child has to use.** The rule that containment
#: alone was missing, and the live run showed why: `outlet_key` holds five
#: values, `1` to `5`, and *every dense integer range contains them*. It was
#: measured as "contained" in `fact_transfer.transfer_id` (1 to 9),
#: `fact_sale.sale_id` (1 to 112,327) and seven more, all true and all meaningless.
#:
#: A child that uses five of a parent's 112,327 values is not keyed to it; it is
#: a small number that happens to fit. Coverage is `child.distinct /
#: parent.distinct`, it is arithmetic on counts already measured, and it costs
#: no query at all — so it runs before any scan and removed most of the 3,681
#: candidates the first run could not get through.
#:
#: **Set high because a lone candidate is never contradicted.** Where several
#: parents contain a child, the best-covered one wins and the rest are recorded;
#: where only one does, nothing argues against it and coverage is the only thing
#: standing between a measurement and an invented join. The second live run
#: still produced `fact_sale_line.outlet_key -> fact_transfer.transfer_id` at
#: **0.556** for exactly that reason — `dim_outlet.outlet_key` is `text` in this
#: schema while `fact_sale_line.outlet_key` is `bigint`, so the real parent was
#: never a candidate and a nine-row surrogate counter ran unopposed.
#:
#: **The cost is stated rather than hidden**: a fact table covering one year of a
#: ten-year `dim_calendar` is a real foreign key at 0.1 coverage, and this floor
#: discards it. That is the trade taken deliberately — a missing edge produces a
#: refusal, an invented one produces a cartesian product presented as an answer —
#: and B-146 carries what it costs.
COVERAGE_FLOOR = 0.9

#: A column contained in this many different parent tables is telling you its
#: domain is generic, not that it is a key. Above the cap it is skipped whole
#: rather than resolved, because resolution over a truncated candidate list can
#: crown a winner the real parent was never allowed to run against.
MAX_PARENTS_PER_CHILD_COLUMN = 12


@dataclass(frozen=True, slots=True)
class ColumnStats:
    """What one column is, measured exactly rather than sampled."""

    schema: str
    table: str
    name: str
    rows: int
    non_null: int
    distinct: int

    @property
    def unique(self) -> bool:
        """Every value that exists, exists once.

        Nulls are excluded rather than disqualifying: a nullable column can
        still be the one side of a many-to-one, and Postgres allows nulls in a
        unique index for the same reason.
        """
        return self.non_null > 1 and self.distinct == self.non_null

    @property
    def repeats(self) -> bool:
        """Some value occurs more than once — the fan-in a join is made of.

        **A foreign key is a many-to-one**, and a child column that never
        repeats a value has not shown the many. It is also precisely what a
        coincidence between two surrogate counters looks like: `dim_outlet`'s
        five keys sit inside `fact_transfer`'s nine because both are ranges
        starting at one, and nothing about that containment is a relationship.
        """
        return self.non_null > self.distinct


@dataclass(frozen=True, slots=True)
class InferredKey:
    """One edge, and the measurements that justify it."""

    from_schema: str
    from_table: str
    from_column: str
    to_schema: str
    to_table: str
    to_column: str
    confidence: float
    evidence: dict[str, object]

    @property
    def constraint_name(self) -> str:
        """A name that says where it came from, since no constraint exists."""
        return f"inferred_{self.from_table}_{self.from_column}_{self.to_table}_{self.to_column}"[
            :255
        ]


@dataclass(slots=True)
class InferenceReport:
    """What was found, and what was not looked at."""

    keys: tuple[InferredKey, ...] = ()
    pairs_considered: int = 0
    pairs_measured: int = 0
    stopped_early: bool = False
    notes: list[str] = field(default_factory=list[str])


def _family(data_type: str) -> str:
    """A coarse type family, used only to skip pairs that cannot possibly join.

    **This is not name matching by another route.** It never looks at what a
    column is called, and it only ever *removes* candidates — a text column and
    a timestamp cannot be a key pair whatever they are called, and measuring
    that costs a scan.
    """
    t = data_type.lower()
    if any(k in t for k in ("int", "serial", "numeric", "decimal", "real", "double", "float")):
        return "number"
    if any(k in t for k in ("timestamp", "date", "time")):
        return "temporal"
    if any(k in t for k in ("bool",)):
        return "boolean"
    return "text"


async def _measure_columns(
    connector: Connector,
    dialect: str,
    table: TableRef,
    columns: Sequence[ColumnInfo],
) -> tuple[ColumnStats, ...]:
    """Row count, non-null count and distinct count for every column, in one query.

    One statement per table rather than one per column: the scan is the cost and
    a wide aggregate reads the table once.
    """
    if not columns:
        return ()

    build = pg_column_stats if dialect == POSTGRES else tsql_column_stats
    query = build(table.schema, table.name, [column.name for column in columns])
    frame = await connector.execute(query, ExecLimits(max_rows=1, timeout_seconds=60.0))
    if not frame.rows:
        return ()

    row = frame.rows[0]

    def count_at(position: int) -> int:
        """A count out of an untyped result row.

        The frame carries `object`, because a customer's driver decides what it
        returns; an aggregate that came back as something other than a number is
        a measurement that did not happen, and zero is the safe reading — it
        makes a column look non-unique and an edge is not inferred.
        """
        value = row[position] if position < len(row) else None
        return int(value) if isinstance(value, (int, float)) else 0

    total = count_at(0)
    stats: list[ColumnStats] = []
    for index, column in enumerate(columns):
        non_null = count_at(1 + index * 2)
        distinct = count_at(2 + index * 2)
        stats.append(
            ColumnStats(
                schema=column.schema,
                table=column.table,
                name=column.name,
                rows=total,
                non_null=non_null,
                distinct=distinct,
            )
        )
    return tuple(stats)


async def _orphan_count(
    connector: Connector,
    dialect: str,
    child: ColumnStats,
    parent: ColumnStats,
) -> int:
    """How many non-null child values have no match in the parent.

    `NOT EXISTS` rather than `NOT IN`: `NOT IN` against a subquery containing a
    single null is false for every row, which would report perfect containment
    for a column that has none — the exact failure this module must not have.
    """
    build = pg_orphan_count if dialect == POSTGRES else tsql_orphan_count
    query = build(child.schema, child.table, child.name, parent.schema, parent.table, parent.name)
    frame = await connector.execute(query, ExecLimits(max_rows=1, timeout_seconds=60.0))
    if not frame.rows:
        return -1
    value = frame.rows[0][0]
    # Not a number means the test did not run. `-1` is "unknown", and the caller
    # only ever accepts exactly zero, so an unknown can never become an edge.
    return int(value) if isinstance(value, (int, float)) else -1


def _child_rows(key: InferredKey) -> int:
    """How much data stood behind an edge, read back out of its evidence."""
    value = key.evidence.get("child_non_null")
    return value if isinstance(value, int) else 0


def _confidence(child: ColumnStats) -> float:
    """How much the containment result is worth, given how much was checked.

    Never 1.0. That value means *the engine guarantees this*, and nothing
    measured after the fact can promise what a constraint does — tomorrow's
    insert can break an edge that every row today supports.
    """
    if child.non_null >= _CONFIDENT_ROWS:
        return _STRONG
    if child.non_null >= _MINIMUM_ROWS:
        return _ADEQUATE
    return _TOO_LITTLE_DATA


async def infer_relationships(
    connector: Connector,
    tables: Sequence[TableRef],
    columns_by_table: dict[tuple[str, str], tuple[ColumnInfo, ...]],
    declared: Sequence[tuple[str, str, str, str]],
    *,
    dialect: str = POSTGRES,
    max_pairs: int = MAX_PAIRS_CHECKED,
    budget_seconds: float = BUDGET_SECONDS,
) -> InferenceReport:
    """Measure the database for join relationships it does not declare.

    `declared` is `(from_table, from_column, to_table, to_column)` for the keys
    the engine already states, so the same edge is never inferred twice.
    """
    started = time.monotonic()
    report = InferenceReport()

    # Phase 1 — what every column is. One scan per table.
    stats: list[ColumnStats] = []
    for table in tables:
        if time.monotonic() - started > budget_seconds:
            report.stopped_early = True
            report.notes.append("stopped before measuring every table")
            break
        columns = columns_by_table.get((table.schema, table.name), ())
        try:
            stats.extend(await _measure_columns(connector, dialect, table, columns))
        except Exception:
            # A table that will not aggregate — a permission, an exotic type —
            # costs its own edges and nothing else. Discovery still succeeds.
            report.notes.append(f"could not measure {table.schema}.{table.name}")

    parents = [s for s in stats if s.unique]
    already = {(f.lower(), fc.lower(), t.lower(), tc.lower()) for f, fc, t, tc in declared}

    # Phase 2 — pairs worth measuring. Cheap arithmetic only; no scans yet.
    candidates: list[tuple[ColumnStats, ColumnStats]] = []
    for parent in parents:
        p_family = _family(
            next(
                (
                    c.data_type
                    for c in columns_by_table.get((parent.schema, parent.table), ())
                    if c.name == parent.name
                ),
                "text",
            )
        )
        for child in stats:
            if (child.schema, child.table) == (parent.schema, parent.table):
                continue
            if child.non_null == 0:
                continue
            # A contained child cannot hold more distinct values than its parent.
            if child.distinct > parent.distinct:
                continue
            # ...nor meaningfully fewer. `COVERAGE_FLOOR` is where the first live
            # run went wrong: five values are contained in every dense integer
            # range, so containment against a 112,327-row key was true and told
            # nobody anything. Free, and it runs before any scan — which is also
            # what makes the whole sweep affordable.
            if child.distinct < parent.distinct * COVERAGE_FLOOR:
                continue
            # ...and the child has to actually fan in. Free, and it removes the
            # unique-inside-unique case that coverage alone leaves standing.
            if not child.repeats:
                continue
            c_family = _family(
                next(
                    (
                        c.data_type
                        for c in columns_by_table.get((child.schema, child.table), ())
                        if c.name == child.name
                    ),
                    "text",
                )
            )
            if c_family != p_family:
                continue
            pair_key = (
                child.table.lower(),
                child.name.lower(),
                parent.table.lower(),
                parent.name.lower(),
            )
            if pair_key in already:
                continue
            candidates.append((child, parent))

    report.pairs_considered = len(candidates)

    # Phase 3 — grouped by **child column**, because that is the unit ambiguity
    # is about.
    #
    # A child column contained in two different parent tables has not told you
    # which one it is keyed to, and picking by column name is the one move this
    # module refuses. It is settled by coverage instead — a child that accounts
    # for every value of one parent and all but one of another has said which it
    # belongs to, without either column's name being read. Where nothing
    # dominates, **no edge is recorded**: two equally good candidates are not
    # half an answer.
    #
    # Only pairs past `COVERAGE_FLOOR` ever get here, so this decides between
    # close candidates. It is not what saves the product from a nine-row
    # surrogate counter at 0.556 — the floor is, because a lone candidate has
    # nothing to lose to.
    by_child: dict[tuple[str, str, str], list[tuple[ColumnStats, ColumnStats]]] = {}
    for child, parent in candidates:
        by_child.setdefault((child.schema, child.table, child.name), []).append((child, parent))

    ordered = sorted(
        by_child.items(),
        key=lambda item: item[1][0][0].non_null,
        reverse=True,
    )

    found: list[InferredKey] = []
    measurements = 0
    for _, options in ordered:
        if time.monotonic() - started > budget_seconds or measurements >= max_pairs:
            report.stopped_early = True
            report.notes.append("stopped before measuring every candidate")
            break

        child = options[0][0]
        parent_tables = {(p.schema, p.table) for _, p in options}
        if len(parent_tables) > MAX_PARENTS_PER_CHILD_COLUMN:
            report.notes.append(
                f"skipped {child.table}.{child.name}: fits {len(parent_tables)} tables, "
                f"so containment identifies none of them"
            )
            continue

        # Best surviving candidate per parent *table*. Two unique columns of one
        # table holding identical values — `dim_item.item_key` and
        # `dim_item.item_name` both did — are one join offered twice, not a
        # choice between two tables, so they must not read as ambiguity.
        best: dict[tuple[str, str], tuple[ColumnStats, float]] = {}
        for _, parent in options:
            try:
                orphans = await _orphan_count(connector, dialect, child, parent)
            except Exception as error:
                # A pair that will not measure costs its own edge and nothing
                # else. **The reason is carried**: a bare "could not test" once
                # hid a `TypeError` in a test fake and produced three red tests
                # that looked like product failures.
                report.notes.append(
                    f"could not test {child.table}.{child.name} -> "
                    f"{parent.table}.{parent.name}: {type(error).__name__}"
                )
                continue
            measurements += 1
            if orphans != 0:
                continue
            coverage = child.distinct / parent.distinct if parent.distinct else 0.0
            key = (parent.schema, parent.table)
            if key not in best or coverage > best[key][1]:
                best[key] = (parent, coverage)

        if not best:
            continue
        ranked = sorted(best.values(), key=lambda item: item[1], reverse=True)
        if len(ranked) > 1 and ranked[0][1] <= ranked[1][1]:
            report.notes.append(
                f"no edge for {child.table}.{child.name}: contained in "
                f"{ranked[0][0].table} and {ranked[1][0].table} equally well"
            )
            continue

        parent, coverage = ranked[0]
        found.append(
            InferredKey(
                from_schema=child.schema,
                from_table=child.table,
                from_column=child.name,
                to_schema=parent.schema,
                to_table=parent.table,
                to_column=parent.name,
                confidence=_confidence(child),
                evidence={
                    "method": "exact",
                    "measured_at": datetime.now(UTC).isoformat(),
                    "parent_rows": parent.rows,
                    "parent_non_null": parent.non_null,
                    "parent_distinct": parent.distinct,
                    "parent_unique": parent.unique,
                    "child_rows": child.rows,
                    "child_non_null": child.non_null,
                    "child_distinct": child.distinct,
                    "orphans": 0,
                    "coverage": round(coverage, 6),
                    # What else fitted, and how well. An edge that turns out to
                    # be wrong is then a question about numbers rather than an
                    # argument about intent.
                    "runners_up": [
                        {"table": other.table, "column": other.name, "coverage": round(score, 6)}
                        for other, score in ranked[1:4]
                    ],
                },
            )
        )

    report.pairs_measured = measurements
    # One edge per table pair. The graph is about tables, and a second column
    # linking two tables already linked changes no answer.
    seen: set[frozenset[str]] = set()
    deduped: list[InferredKey] = []
    for key in sorted(found, key=_child_rows, reverse=True):
        pair = frozenset((key.from_table, key.to_table))
        if pair in seen:
            continue
        seen.add(pair)
        deduped.append(key)
    found = deduped

    report.keys = tuple(found)
    return report


__all__ = [
    "BUDGET_SECONDS",
    "COVERAGE_FLOOR",
    "MAX_PAIRS_CHECKED",
    "MAX_PARENTS_PER_CHILD_COLUMN",
    "ColumnStats",
    "InferenceReport",
    "InferredKey",
    "infer_relationships",
]
