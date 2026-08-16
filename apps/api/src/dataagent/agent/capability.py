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

**One edge is undirected; a path is not** (DECISIONS **D-026**). A foreign key
points one way and a single join works either way, so `payments → orders →
customers` is answerable in two hops. But a foreign key is many-to-one *by
construction* — its target is a unique key or the engine would not have accepted
the constraint — and that direction decides what a **path** does. Walking an edge
child→parent **narrows**: each row on the left matches at most one on the right.
Walking it parent→child **fans out**. So a path is a safe join exactly when it
never turns *up and then down* at an intermediate node, and a node where it does
is that node's two children being joined through their shared parent — the
textbook **chasm trap**:

    fact_sale  --up--> dim_business --down--> fact_purchase    chasm
    payments   --up--> orders       --up----> customers        safe
    dim_outlet --down--> fact_sale  --up----> dim_item         safe

Treating reachability as joinability is how a hub dimension defeats this check
(**B-057**). In a star schema every fact carries `business_key`, so a single-row
`dim_business` connects everything to everything, and `fact_sale → dim_business →
fact_purchase` — 112,327 rows against 13,660, sharing one key value — is a
1.5-billion-row cartesian product the check called answerable. The DAL's row cap
bounds what comes back and does nothing at all for the aggregate, which is the
number a person reads.

**A chasm is not a refusal.** Two facts over a shared dimension genuinely *are*
comparable: aggregate each to the common grain, then join the aggregates. So the
verdict is three-valued — `joinable`, `comparable`, `unreachable` — and only the
last one refuses. Making a chasm refuse would trade B-057 for **B-058**, the
false-refusal defect, which is the worse of the two: a wrong answer is at least
in principle checkable, while a fluent refusal of an answerable question teaches
people the product is broken.

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
from dataclasses import dataclass, field

from sqlalchemy import select

from dataagent.db.models import CatalogRelationship, CatalogSnapshot
from dataagent.tenancy.session import org_session

__all__ = [
    "COMPARABLE",
    "JOINABLE",
    "MIN_CONFIDENCE",
    "UNREACHABLE",
    "CapabilityChasm",
    "CapabilityGap",
    "CapabilityVerdict",
    "JoinGraph",
    "load_join_graph",
]

#: The three verdicts a pair of tables can earn. Only ``UNREACHABLE`` refuses.
JOINABLE = "joinable"
COMPARABLE = "comparable"
UNREACHABLE = "unreachable"

#: A hop's direction. ``UP`` follows a foreign key from child to parent and
#: narrows; ``DOWN`` follows it backwards and fans out. Spelled out rather than
#: left as booleans because the whole of this module's new behaviour turns on
#: which of the two a hop is, and `if not up` reads as nothing in particular.
UP = "up"
DOWN = "down"

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
class CapabilityChasm:
    """Two tables joinable only through their shared parent — the chasm trap.

    Not a gap and not a refusal. The pair is answerable; what is unsafe is the
    *direct* join, because the shared parent fans both sides out and the product
    of the two is what comes back. `via` is the node where the path turns from
    narrowing to fanning out, which is the key both sides must be aggregated to.
    """

    left: str
    right: str
    via: str

    def sentence(self) -> str:
        """The instruction, not an apology — this pair has a correct query."""
        return (
            f"{self.left} and {self.right} are both children of {self.via}, so joining "
            f"them directly multiplies their rows together instead of matching them. "
            f"Aggregate each one to the key it shares with {self.via} first, then join "
            f"the two aggregates."
        )


@dataclass(frozen=True, slots=True)
class CapabilityVerdict:
    """What the check found, for the trace, the refusal and the guidance."""

    tables: tuple[str, ...]
    gaps: tuple[CapabilityGap, ...]
    chasms: tuple[CapabilityChasm, ...] = ()

    @property
    def answerable(self) -> bool:
        """A chasm is deliberately not counted here.

        Only an unreachable pair makes a question unanswerable. A chasm pair has
        a correct query — see `guidance()` — and refusing it would be the false
        refusal this module exists to avoid on the other side (B-058).
        """
        return not self.gaps

    def reason(self) -> str:
        """One message covering every gap found, or empty when there are none."""
        return " ".join(gap.sentence() for gap in self.gaps)

    def guidance(self) -> str:
        """What to do about the chasms, or empty when there are none."""
        return " ".join(chasm.sentence() for chasm in self.chasms)

    def as_payload(self) -> dict[str, object]:
        """10.3's ``capability_checked {verdicts}`` payload — built for eyes.

        The vocabulary in 10.3 is closed, so a chasm rides in this payload rather
        than earning an event type no UI was built to render.
        """
        return {
            "tables": list(self.tables),
            "answerable": self.answerable,
            "verdicts": [
                {"left": gap.left, "right": gap.right, "reachable": False} for gap in self.gaps
            ]
            + [
                {
                    "left": chasm.left,
                    "right": chasm.right,
                    "reachable": True,
                    "verdict": COMPARABLE,
                    "via": chasm.via,
                }
                for chasm in self.chasms
            ],
        }


@dataclass(frozen=True, slots=True)
class JoinGraph:
    """The tables of one catalog and the joins between them.

    Two views of the same relationships. ``edges`` is undirected, because a
    single join works either way and reachability is the question it answers.
    ``parents`` keeps the direction the foreign keys declare — ``parents[child]``
    is every table ``child`` references — and that is what tells a safe path from
    a chasm (D-026). Bare table names are the keys in both: a statement says
    ``FROM orders`` as often as ``FROM public.orders``, and a check that missed a
    gap because of a schema prefix would be worse than no check.

    ``parents`` may be empty for a graph built without direction, in which case
    every hop reads as narrowing and no path is ever classified as a chasm — the
    behaviour this module had before D-026. `load_join_graph` always fills it;
    the default exists so a hand-built graph is not silently *more* strict than
    the catalog it stands in for.
    """

    edges: dict[str, frozenset[str]]
    parents: dict[str, frozenset[str]] = field(default_factory=dict[str, frozenset[str]])

    def _hop(self, here: str, there: str) -> str:
        """Which way this single edge is being walked.

        A mutual pair of foreign keys is read as narrowing: it is the safe
        reading, and a cycle of two tables referencing each other is not a chasm
        in any case. An edge with no recorded direction reads the same way, so
        an admin-declared or inferred relationship that arrives without one
        cannot silently start refusing things.
        """
        downward = here in self.parents.get(there, frozenset())
        upward = there in self.parents.get(here, frozenset())
        return DOWN if downward and not upward else UP

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

    def safe_path(self, left: str, right: str) -> tuple[str, ...] | None:
        """A shortest path that never turns *up then down*, or None.

        Breadth-first over ``(node, how we arrived)`` rather than over nodes, so
        the same table can be reached twice — once narrowing, once fanning out —
        and only the arrival that keeps a join valid is extended. That doubles
        the state space and nothing else; the graph is one catalog.

        A path of all-up hops is a chain of many-to-one joins and preserves the
        left table's grain. A path of all-down hops is a drill-down and fans out
        once, which is a real query. What is forbidden is up followed by down at
        the same node, because that node is then a shared parent and its two
        children are being multiplied together rather than matched.
        """
        left, right = _bare(left), _bare(right)
        if left == right:
            return (left,)
        if left not in self.edges or right not in self.edges:
            return None
        # Direction of the hop that arrived; None at the start, where anything
        # is still allowed.
        start: tuple[str, ...] = (left,)
        queue: deque[tuple[tuple[str, ...], str | None]] = deque([(start, None)])
        seen: set[tuple[str, str | None]] = {(left, None)}
        while queue:
            trail, arrived = queue.popleft()
            here = trail[-1]
            for neighbour in sorted(self.edges.get(here, frozenset())):
                direction = self._hop(here, neighbour)
                if arrived == UP and direction == DOWN:
                    continue  # the chasm: `here` is a shared parent.
                if neighbour == right:
                    return (*trail, neighbour)
                state = (neighbour, direction)
                if state in seen:
                    continue
                seen.add(state)
                queue.append(((*trail, neighbour), direction))
        return None

    def classify(self, left: str, right: str) -> str:
        """`joinable`, `comparable` or `unreachable` for one pair."""
        if self.safe_path(left, right) is not None:
            return JOINABLE
        return COMPARABLE if self.path(left, right) is not None else UNREACHABLE

    def _hub(self, left: str, right: str) -> str:
        """The node where the shortest path turns from narrowing to fanning out.

        Reported so the guidance can name the key to aggregate to. Falls back to
        the midpoint of the path when no turn is found, which cannot happen for
        a pair this is called about but keeps the return type honest.
        """
        trail = self.path(left, right) or (left, right)
        for index in range(1, len(trail) - 1):
            if (
                self._hop(trail[index - 1], trail[index]) == UP
                and self._hop(trail[index], trail[index + 1]) == DOWN
            ):
                return trail[index]
        return trail[len(trail) // 2]

    def check(self, tables: Iterable[str]) -> CapabilityVerdict:
        """How every table named relates to every other.

        Pairwise rather than "is the set connected", so a message can name the
        specific pair. A question over three tables where two are joined and the
        third is stranded should say *which* is stranded.

        One table is always answerable: there is nothing to join.
        """
        wanted = sorted({_bare(name) for name in tables if _bare(name)})
        gaps: list[CapabilityGap] = []
        chasms: list[CapabilityChasm] = []
        for index, left in enumerate(wanted):
            for right in wanted[index + 1 :]:
                verdict = self.classify(left, right)
                if verdict == UNREACHABLE:
                    gaps.append(CapabilityGap(left=left, right=right))
                elif verdict == COMPARABLE:
                    chasms.append(
                        CapabilityChasm(left=left, right=right, via=self._hub(left, right))
                    )
        return CapabilityVerdict(tables=tuple(wanted), gaps=tuple(gaps), chasms=tuple(chasms))

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

    def comparable_pairs(self) -> tuple[CapabilityChasm, ...]:
        """Every pair reachable only through a shared parent.

        Also given to the planner up front, and for the better reason: an
        unreachable pair is a dead end to avoid, while these have a correct query
        the model will write if it is told how. On a star schema there are many
        of them — see `runner.relevant_pairs`, which picks the ones this question is
        about rather than the ones that sort first (B-056).
        """
        names = sorted(self.edges)
        return tuple(
            CapabilityChasm(left=left, right=right, via=self._hub(left, right))
            for index, left in enumerate(names)
            for right in names[index + 1 :]
            if self.classify(left, right) == COMPARABLE
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
    # `from` is the child — the table carrying the foreign key — and `to` is the
    # parent it references. That target is a unique key or the engine would have
    # refused the constraint, which is exactly why the direction means something
    # (D-026): child→parent narrows, parent→child fans out.
    parents: dict[str, set[str]] = {}
    for from_table, to_table, confidence in rows:
        if confidence is not None and float(confidence) < MIN_CONFIDENCE:
            continue
        left, right = _bare(from_table), _bare(to_table)
        adjacency.setdefault(left, set()).add(right)
        adjacency.setdefault(right, set()).add(left)
        parents.setdefault(left, set()).add(right)
    return JoinGraph(
        edges={name: frozenset(links) for name, links in adjacency.items()},
        parents={name: frozenset(links) for name, links in parents.items()},
    )
