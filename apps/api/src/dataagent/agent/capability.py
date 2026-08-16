"""Can this schema answer that question at all? (architecture 4.3, Part 4.4)

The check the model cannot talk its way past. A question needing two tables with
no join path between them is *unanswerable*, and the honest thing is to say which
link is missing — not to write a plausible query, run it, and present whatever
comes back. That last failure is the one this exists to prevent: a query joining
unrelated tables does not error, it returns a cartesian product, and a confident
answer computed from one is indistinguishable from a real one.

**Deterministic, and it decides rather than advises.** The verdict comes from
`catalog_relationships` — what the engine itself declares — walked as a graph. No
model is consulted, and no model can override it. Architecture 4.3 is explicit:
*"the planner is told so as fact"* and *"the model cannot talk its way past this
check."*

**The required tables are the ones the proposed statement names.** Deciding up
front which tables a question "needs" would mean inferring intent, and inferring
it wrongly means refusing a question that was perfectly answerable — a false
refusal is worse than no check, because it teaches people the product is broken.
So the model proposes, the tables are read out of its own SQL, and reachability
is checked **before the statement is sent**. The gaps are also handed to the
planner up front as fact, so a well-behaved model avoids the dead end rather than
being caught in it.

**Edges are undirected.** A foreign key points one way; a join works either way.
`payments → orders → customers` means a question about payments and customers is
answerable, and the path is two hops.

**Inferred edges must earn their place.** Only relationships at or above
`MIN_CONFIDENCE` join the graph. A speculative edge would make the check claim a
question is answerable when the join it depends on is a guess — turning an honest
refusal into a wrong answer, which is the trade this whole module exists to
refuse.
"""

from __future__ import annotations

import uuid
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select

from dataagent.db.models import CatalogRelationship, CatalogSnapshot
from dataagent.tenancy.session import org_session

__all__ = [
    "MIN_CONFIDENCE",
    "CapabilityGap",
    "CapabilityVerdict",
    "JoinGraph",
    "load_join_graph",
]

#: How sure an inferred relationship must be before the check will rely on it.
#: Declared foreign keys are 1.0 and always qualify. The threshold matters in the
#: direction that costs most: too low and the check says "answerable" on the
#: strength of a guess, which produces a wrong answer instead of an honest
#: refusal.
MIN_CONFIDENCE = 0.9


@dataclass(frozen=True, slots=True)
class CapabilityGap:
    """Two tables a question needs together, and no way to join them."""

    left: str
    right: str

    def sentence(self) -> str:
        """The refusal, in the shape architecture 4.3 gives it.

        Names both tables, says what kind of thing is missing, and says what
        would unlock it — because "I cannot answer that" without a reason is
        indistinguishable from the product being broken.
        """
        return (
            f"This database has {self.left} and {self.right}, but nothing linking "
            f"them — there is no foreign key or join table connecting the two, so "
            f"they cannot be combined in one query. Answering this needs a linking "
            f"table (for example an order-items table joining the two), or a "
            f"column on one that refers to the other."
        )


@dataclass(frozen=True, slots=True)
class CapabilityVerdict:
    """What the check found, for the trace and for the refusal."""

    tables: tuple[str, ...]
    gaps: tuple[CapabilityGap, ...]

    @property
    def answerable(self) -> bool:
        return not self.gaps

    def reason(self) -> str:
        """One message covering every gap found, or empty when there are none."""
        return " ".join(gap.sentence() for gap in self.gaps)

    def as_payload(self) -> dict[str, object]:
        """10.3's ``capability_checked {verdicts}`` payload — built for eyes."""
        return {
            "tables": list(self.tables),
            "answerable": self.answerable,
            "verdicts": [
                {"left": gap.left, "right": gap.right, "reachable": False} for gap in self.gaps
            ],
        }


@dataclass(frozen=True, slots=True)
class JoinGraph:
    """The tables of one catalog and the joins between them.

    Undirected adjacency, because a foreign key points one way and a join works
    either way. Bare table names are the keys: a statement says ``FROM orders``
    as often as ``FROM public.orders``, and a check that missed a gap because of
    a schema prefix would be worse than no check.
    """

    edges: dict[str, frozenset[str]]

    def path(self, left: str, right: str) -> tuple[str, ...] | None:
        """A shortest join path, or None when the two are unreachable.

        Breadth-first, so a multi-hop path is found and reported at its shortest:
        ``payments → orders → customers`` is a real answer to "can these be
        combined", and the number of hops is what a person needs to judge whether
        the join is sensible.
        """
        left, right = _bare(left), _bare(right)
        if left == right:
            return (left,)
        if left not in self.edges or right not in self.edges:
            # A table absent from the catalog is not "unreachable" — it is
            # unknown, and the DAL's grounding refuses it with a better message
            # than this module could give.
            return None
        seen = {left}
        queue: deque[tuple[str, ...]] = deque([(left,)])
        while queue:
            trail = queue.popleft()
            for neighbour in sorted(self.edges.get(trail[-1], frozenset())):
                if neighbour in seen:
                    continue
                if neighbour == right:
                    return (*trail, neighbour)
                seen.add(neighbour)
                queue.append((*trail, neighbour))
        return None

    def check(self, tables: Iterable[str]) -> CapabilityVerdict:
        """Whether every table named can be joined to every other.

        Pairwise rather than "is the set connected", so the refusal can name the
        specific pair that fails. A question over three tables where two are
        joined and the third is stranded should say *which* is stranded.

        One table is always answerable: there is nothing to join.
        """
        wanted = sorted({_bare(name) for name in tables if _bare(name)})
        gaps: list[CapabilityGap] = []
        for index, left in enumerate(wanted):
            for right in wanted[index + 1 :]:
                if self.path(left, right) is None:
                    gaps.append(CapabilityGap(left=left, right=right))
        return CapabilityVerdict(tables=tuple(wanted), gaps=tuple(gaps))

    def unreachable_pairs(self) -> tuple[CapabilityGap, ...]:
        """Every pair in this catalog that cannot be joined.

        Given to the planner up front as fact (4.3), so a well-behaved model
        avoids the dead end rather than being caught in it by the check below.
        """
        names = sorted(self.edges)
        return tuple(
            CapabilityGap(left=left, right=right)
            for index, left in enumerate(names)
            for right in names[index + 1 :]
            if self.path(left, right) is None
        )


def _bare(name: str) -> str:
    """``public.orders`` and ``orders`` are the same table to this check."""
    return name.strip().strip('"').split(".")[-1].lower()


async def load_join_graph(org_id: uuid.UUID, data_source_id: uuid.UUID) -> JoinGraph:
    """The active catalog's join graph, including tables that join to nothing.

    Isolated tables are in the graph with no edges, deliberately: `menu_items` in
    the demo database has no relationships at all, and a graph built only from
    edges would not know it exists — so a question about it would fall through
    this check instead of being refused by it.
    """
    async with org_session(org_id) as session:
        snapshot = (
            await session.execute(
                select(CatalogSnapshot.id).where(
                    CatalogSnapshot.data_source_id == data_source_id,
                    CatalogSnapshot.status == "active",
                )
            )
        ).scalar_one_or_none()
        if snapshot is None:
            return JoinGraph(edges={})

        from dataagent.db.models import CatalogTable

        names = (
            (
                await session.execute(
                    select(CatalogTable.table_name).where(CatalogTable.snapshot_id == snapshot)
                )
            )
            .scalars()
            .all()
        )
        rows = (
            await session.execute(
                select(
                    CatalogRelationship.from_table,
                    CatalogRelationship.to_table,
                    CatalogRelationship.confidence,
                ).where(CatalogRelationship.snapshot_id == snapshot)
            )
        ).all()

    adjacency: dict[str, set[str]] = {_bare(name): set() for name in names}
    for from_table, to_table, confidence in rows:
        if confidence is not None and float(confidence) < MIN_CONFIDENCE:
            continue
        left, right = _bare(from_table), _bare(to_table)
        adjacency.setdefault(left, set()).add(right)
        adjacency.setdefault(right, set()).add(left)
    return JoinGraph(edges={name: frozenset(links) for name, links in adjacency.items()})
