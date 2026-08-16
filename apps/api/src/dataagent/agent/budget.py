"""What a run is allowed to spend, and who says so (architecture 4.4).

Five ceilings, from 4.4's "loop-safety" paragraph: **8 iterations, 10 queries,
20 LLM calls, 150k tokens, 240 seconds of wall clock**. They exist because an
agent that can decide to keep going will, and the ways it goes wrong are not
symmetrical — a loop that never ends costs money continuously and produces
nothing, while a loop that stops early produces an answer with a caveat on it.

**Budgets decrement in the controller, never in the prompt** (4.4, verbatim). A
model told it has three calls left may believe it, forget it, or reason about it;
none of those is a limit. Nothing here is ever rendered into a message — the loop
asks this object whether it may continue, and the model is not consulted.

**Exhaustion is an ending, not an error.** Reaching a ceiling means the run
finalizes with what it has and says the budget stopped it — the same distinction
WP7.2b drew between a refusal and a failure. `agent_runs` has carried a
`budget_exhausted` status since revision 0012 for exactly this, and 10.3 has both
`budget_warning` and `budget_exhausted` in its event vocabulary.

**Overridable per organization, within reason.** 4.4 calls the defaults defaults,
and 8.3 makes tiering and caps the cost levers. An override may *lower* a ceiling
freely; raising one is allowed but bounded by `MAX_OVERRIDES`, so a
misconfiguration cannot turn a bounded loop into an unbounded one. The store
these overrides will come from is `agent_configs`, which does not exist yet
(**B-038**); until it does they arrive as a mapping from configuration and the
seam is the same.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

__all__ = [
    "DEFAULT_BUDGET",
    "MAX_OVERRIDES",
    "Budget",
    "BudgetState",
    "Exhaustion",
]

#: Architecture 4.4's defaults, named so the numbers are greppable from the doc.
DEFAULT_ITERATIONS = 8
DEFAULT_QUERIES = 10
#: Raised from 20 to 24 in WP9.1 (**D-028**), which is the move D-024 said would
#: be needed the day a stage was added: *"if a stage is ever added to the loop …
#: the iteration ceiling and the call ceiling stop fitting and one of them has to
#: move."* The critic is that stage. The arithmetic, worst case:
#:
#:   8 iterations x 2 calls (plan + reflect)   16
#:   compose, twice, because of the re-entry    2
#:   critic,  twice, for the same reason        2
#:   intake, when it is built (4.4 names it)    1
#:                                             ---
#:                                              21   against a ceiling of 24
#:
#: The three spare are the same headroom D-024 argued for and for the same
#: reason: a run must be stopped by the ceiling that describes what it did — its
#: iterations — and not by an accounting limit it hit first.
DEFAULT_LLM_CALLS = 24
DEFAULT_TOKENS = 150_000
DEFAULT_WALL_SECONDS = 240.0

#: How far an organization may raise a ceiling. Lowering is unrestricted — a
#: tenant that wants cheaper answers should get them — but raising is bounded,
#: because the whole point of a hard cap is that no configuration turns it off.
MAX_OVERRIDES: Mapping[str, float] = {
    "iterations": 24,
    "queries": 40,
    "llm_calls": 60,
    "tokens": 600_000,
    "wall_seconds": 900.0,
}

#: Fraction of a ceiling at which the trace says "getting close" (10.3's
#: `budget_warning`). Emitted once per dimension: a warning that repeats every
#: iteration is noise nobody reads by the third one.
WARN_AT = 0.75


@dataclass(frozen=True, slots=True)
class Exhaustion:
    """Which ceiling stopped the run, and how it reads to a person.

    Carried rather than raised, because reaching a budget is a normal ending and
    the loop finalizes on it — an exception would put an ordinary outcome on the
    error path.
    """

    dimension: str
    used: float
    limit: float

    @property
    def reason(self) -> str:
        """Plain words for the answer's caveat, not a metric name.

        The person reading this asked a question and got a partial answer; what
        they need to know is that the search stopped and why, not that
        `llm_calls` reached 20.
        """
        return {
            "iterations": "I reached the maximum number of research steps for one question.",
            "queries": "I reached the maximum number of queries I may run for one question.",
            "llm_calls": "I reached the maximum amount of reasoning allowed for one question.",
            "tokens": "I reached the size limit on how much I may read for one question.",
            "wall_seconds": "I reached the time limit for one question.",
        }.get(self.dimension, "I reached a limit set for one question.")


@dataclass(frozen=True, slots=True)
class Budget:
    """The ceilings for one run."""

    iterations: int = DEFAULT_ITERATIONS
    queries: int = DEFAULT_QUERIES
    llm_calls: int = DEFAULT_LLM_CALLS
    tokens: int = DEFAULT_TOKENS
    wall_seconds: float = DEFAULT_WALL_SECONDS

    @classmethod
    def from_overrides(cls, overrides: Mapping[str, object] | None) -> Budget:
        """Defaults, adjusted by whatever an organization configured.

        Unknown keys and unusable values are **ignored rather than raising**: this
        is configuration, and a typo in one field must not stop a run that would
        otherwise work. A value at or below the default is taken as given; one
        above is clamped to `MAX_OVERRIDES` rather than refused, so the run
        proceeds under a limit that still exists.
        """
        if not overrides:
            return cls()
        values: dict[str, float] = {}
        for name in ("iterations", "queries", "llm_calls", "tokens", "wall_seconds"):
            raw = overrides.get(name)
            if not isinstance(raw, int | float) or isinstance(raw, bool) or raw <= 0:
                continue
            ceiling = MAX_OVERRIDES[name]
            values[name] = min(float(raw), ceiling)
        base = cls()
        return replace(
            base,
            iterations=int(values.get("iterations", base.iterations)),
            queries=int(values.get("queries", base.queries)),
            llm_calls=int(values.get("llm_calls", base.llm_calls)),
            tokens=int(values.get("tokens", base.tokens)),
            wall_seconds=float(values.get("wall_seconds", base.wall_seconds)),
        )

    def as_json(self) -> dict[str, object]:
        """What `agent_runs.budget` holds — the allowance this run was given.

        Stored per run rather than looked up later, because a cap that changed
        after the fact would make an old run's caveat unexplainable.
        """
        return {
            "iterations": self.iterations,
            "queries": self.queries,
            "llm_calls": self.llm_calls,
            "tokens": self.tokens,
            "wall_seconds": self.wall_seconds,
        }


@dataclass
class BudgetState:
    """What has been spent, and whether there is any left.

    Mutable on purpose: this is the controller's counter, and one object is
    incremented as the loop runs. It is checkpointed into `agent_runs.state` with
    the rest of `ResearchState`, so an interrupted run can say how far it got.
    """

    budget: Budget = field(default_factory=Budget)
    iterations: int = 0
    queries: int = 0
    llm_calls: int = 0
    tokens: int = 0
    #: Monotonic, because a wall-clock limit must not be moved by an NTP step or
    #: a daylight-saving change mid-run.
    started_at: float = field(default_factory=time.monotonic)
    #: Dimensions already warned about, so the trace says it once.
    warned: set[str] = field(default_factory=set[str])

    def elapsed(self, *, now: float | None = None) -> float:
        return (now if now is not None else time.monotonic()) - self.started_at

    def spend_iteration(self) -> None:
        self.iterations += 1

    def spend_query(self) -> None:
        self.queries += 1

    def spend_llm(self, tokens: int = 0) -> None:
        """One model call and what it read. Counted together because they are
        always spent together, and forgetting the second is how a token ceiling
        silently stops being one."""
        self.llm_calls += 1
        self.tokens += max(0, tokens)

    def exhausted(self, *, now: float | None = None) -> Exhaustion | None:
        """The first ceiling reached, or None.

        Checked **before** doing the next thing rather than after, so a run never
        overshoots a cap it was told to respect. Order is deliberate: time first,
        because it is the one the person waiting actually feels.
        """
        elapsed = self.elapsed(now=now)
        checks: tuple[tuple[str, float, float], ...] = (
            ("wall_seconds", elapsed, self.budget.wall_seconds),
            ("iterations", self.iterations, self.budget.iterations),
            ("queries", self.queries, self.budget.queries),
            ("llm_calls", self.llm_calls, self.budget.llm_calls),
            ("tokens", self.tokens, self.budget.tokens),
        )
        for dimension, used, limit in checks:
            if used >= limit:
                return Exhaustion(dimension=dimension, used=used, limit=limit)
        return None

    def approaching(self, *, now: float | None = None) -> str | None:
        """A dimension newly past `WARN_AT`, once each.

        For 10.3's `budget_warning`, which exists so a trace shows the run
        tightening rather than stopping without warning.
        """
        elapsed = self.elapsed(now=now)
        for dimension, used, limit in (
            ("wall_seconds", elapsed, self.budget.wall_seconds),
            ("iterations", float(self.iterations), float(self.budget.iterations)),
            ("queries", float(self.queries), float(self.budget.queries)),
            ("llm_calls", float(self.llm_calls), float(self.budget.llm_calls)),
            ("tokens", float(self.tokens), float(self.budget.tokens)),
        ):
            if dimension in self.warned or limit <= 0:
                continue
            if used / limit >= WARN_AT:
                self.warned.add(dimension)
                return dimension
        return None

    def as_json(self) -> dict[str, object]:
        return {
            "iterations": self.iterations,
            "queries": self.queries,
            "llm_calls": self.llm_calls,
            "tokens": self.tokens,
            "elapsed_seconds": round(self.elapsed(), 3),
            "limits": self.budget.as_json(),
        }


#: The allowance a run gets when nobody configured one.
DEFAULT_BUDGET = Budget()
