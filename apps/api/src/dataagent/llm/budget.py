"""A ceiling on what one run may spend.

Architecture 8.3 wants per-organization daily and monthly quotas with a soft
warning at 80% and a hard stop at 100%. That is **B-025**, and it belongs with
the Phase 8 controller that owns ``BudgetState``. This module is the narrow
slice that is useful before any of it exists: a hard per-run ceiling, so an eval
sweep or a looping agent stops costing money at a number somebody chose.

It reads the same ``usage_ledger`` rows the meter writes, which means it needs
no state of its own and cannot drift from what was actually billed.

**An unpriced model cannot be capped, and the cap says so.** A model with no
entry in ``LLM_PRICES`` records ``cost_usd = NULL``; summing those gives a total
that is smaller than the truth and a ceiling that is never reached. So when a
limit is configured and either the model has no price or the run already holds
unpriced rows, the call is refused rather than waved through
(``LLM_REFUSE_UNPRICED_WHEN_CAPPED``, on by default). A ceiling that cannot see
what it is spending is not a ceiling.

**The cap is per run, and a call with no run is not capped.** Stated plainly
because it is a real hole rather than an oversight: there is nothing to
accumulate against without a run id. Everything that spends in a loop — the
agent, the eval harness, the smoke script — has one. Phase 8's BudgetState is
what closes this properly.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import text

from dataagent.config import Settings, get_settings
from dataagent.llm.base import LLMError
from dataagent.llm.meter import price_for
from dataagent.tenancy.session import org_session

__all__ = ["RunCostExceededError", "RunSpend", "assert_within_limit", "spent_on_run"]


class RunCostExceededError(LLMError):
    """This run has spent what it was allowed to spend.

    Not retryable, and deliberately its own type rather than a bare
    ``LLMError``: architecture 8.5 calls budget exhaustion *not a failure* — the
    right response is to compose an answer from the findings so far and state
    the limitation. There is no controller to do that until Phase 8, so this
    raises with a name that Phase 8 can catch and turn into a graceful ending
    rather than an apology.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


class RunSpend:
    """What one run has cost so far, and how much of that is actually known."""

    __slots__ = ("unpriced_calls", "usd")

    def __init__(self, usd: Decimal, unpriced_calls: int) -> None:
        self.usd = usd
        self.unpriced_calls = unpriced_calls

    def __repr__(self) -> str:
        return f"RunSpend(usd={self.usd}, unpriced_calls={self.unpriced_calls})"


_SPEND = text(
    "SELECT COALESCE(SUM(cost_usd), 0) AS usd, "
    "COUNT(*) FILTER (WHERE cost_usd IS NULL) AS unpriced "
    "FROM usage_ledger WHERE run_id = :run"
)


async def spent_on_run(org_id: uuid.UUID, run_id: uuid.UUID) -> RunSpend:
    """Sum this run's ledger rows. Scoped by RLS to the organization asking."""
    async with org_session(org_id) as session:
        row = (await session.execute(_SPEND, {"run": run_id})).mappings().one()
        return RunSpend(usd=Decimal(str(row["usd"])), unpriced_calls=int(row["unpriced"]))


async def assert_within_limit(
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID | None,
    model: str,
    settings: Settings | None = None,
) -> None:
    """Refuse the next call when this run has reached its ceiling.

    Checked *before* the call rather than after, because a limit enforced after
    the spend is a report.
    """
    resolved = settings if settings is not None else get_settings()
    limit = resolved.llm_run_cost_limit_usd
    if limit is None or run_id is None:
        return

    if resolved.llm_refuse_unpriced_when_capped and price_for(model, resolved) is None:
        raise RunCostExceededError(
            f"{model!r} has no entry in LLM_PRICES, so its spend cannot be counted "
            f"against this run's ${limit:.2f} ceiling. Price the model, or set "
            "LLM_REFUSE_UNPRICED_WHEN_CAPPED=false to accept an uncounted cost."
        )

    spend = await spent_on_run(org_id, run_id)

    if resolved.llm_refuse_unpriced_when_capped and spend.unpriced_calls:
        raise RunCostExceededError(
            f"this run has {spend.unpriced_calls} call(s) with an unknown cost, so "
            f"its ${limit:.2f} ceiling cannot be enforced honestly. Price every "
            "model it uses, or set LLM_REFUSE_UNPRICED_WHEN_CAPPED=false."
        )

    if spend.usd >= Decimal(str(limit)):
        raise RunCostExceededError(
            f"this run has spent ${spend.usd:.4f} of its ${limit:.2f} ceiling and "
            "will not make further model calls. Raise LLM_RUN_COST_LIMIT_USD, or "
            "treat what it has as the answer."
        )
