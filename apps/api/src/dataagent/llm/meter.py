"""What every model call cost, written down (architecture Part 4.9, 8.3).

One row per provider call, in the organization's own ``usage_ledger``. Not per
run and not per question: 8.3 checks quotas *at each LLM call*, and a run that is
still going has already spent what it has spent.

Three things this module is careful about.

**Failures are metered.** A provider that dies mid-generation has still consumed
tokens, and a 429 that produced nothing is the trace of a fallback — with WP6.2's
chain, one attempt writes an ``error`` row and the next writes an ``ok`` row, so
the ledger shows the switch instead of quietly reporting the survivor.

**An unpriced model costs null, not zero.** Prices are configuration
(``LLM_PRICES``) because provider price lists change and a number baked into a
release would be wrong within months — and a wrong price that looks like a real
one is worse than an absent one. ``cost_usd IS NULL`` reads as "nobody has priced
this model", which a quota check must treat as unknown rather than free.

**Recording failing is not allowed to be silent.** Same rule as the DAL's audit
hook: if the platform database is unreachable this raises, and the caller decides.
What no caller gets is a way to skip it — ``llm/service.py`` is the front door and
it meters on every path, including the ones that raise.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from dataagent.config import Settings, get_settings
from dataagent.db.models import UsageLedger
from dataagent.llm.base import Role, Tier, Usage
from dataagent.tenancy.session import org_session

__all__ = ["Price", "estimate_cost", "price_for", "record"]

STATUS_OK = "ok"
STATUS_ERROR = "error"

#: Prices are quoted per million tokens everywhere in the industry, so that is
#: how ``LLM_PRICES`` is written and this is where the division happens once.
TOKENS_PER_PRICE_UNIT = Decimal(1_000_000)

#: Six decimal places, matching ``usage_ledger.cost_usd``. A single cheap call
#: costs a small fraction of a cent, and rounding it to zero at each row would
#: make a million of them cost nothing.
CENTS = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class Price:
    """USD per million tokens, in and out."""

    input_per_million: Decimal
    output_per_million: Decimal


def price_for(model: str, settings: Settings | None = None) -> Price | None:
    """The configured price for a model, or None if nobody has set one.

    Read through ``Decimal(str(...))`` rather than from the float directly: money
    computed from binary floats accumulates error, and this number is multiplied
    by token counts and then summed over a month.
    """
    resolved = settings if settings is not None else get_settings()
    entry = resolved.llm_prices.get(model)
    if not entry:
        return None
    try:
        return Price(
            input_per_million=Decimal(str(entry["input"])),
            output_per_million=Decimal(str(entry["output"])),
        )
    except (KeyError, InvalidOperation):
        # A malformed price is treated as no price. Recording a wrong number
        # would be worse than recording none: a total that looks authoritative
        # and is not is what a quota would then be enforced from.
        return None


def estimate_cost(model: str, usage: Usage, settings: Settings | None = None) -> Decimal | None:
    """What this call cost, or None if the model has no configured price.

    None is a real answer and not a failure: it says the deployment has not told
    us what this model costs. Returning zero instead would put a lie into every
    total that is computed from these rows.
    """
    price = price_for(model, settings)
    if price is None:
        return None
    total = (
        Decimal(usage.input_tokens) * price.input_per_million
        + Decimal(usage.output_tokens) * price.output_per_million
    ) / TOKENS_PER_PRICE_UNIT
    return total.quantize(CENTS, rounding=ROUND_HALF_UP)


async def record(
    *,
    org_id: uuid.UUID,
    role: Role,
    tier: Tier,
    provider: str,
    model: str,
    usage: Usage,
    latency_ms: int,
    run_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    repaired: bool = False,
    error: str | None = None,
    settings: Settings | None = None,
) -> uuid.UUID:
    """Write one ledger row and return its id.

    ``error`` decides the status: a call that says what went wrong is an error, a
    call that does not is an ``ok``. The database enforces that pairing, so a row
    claiming failure without a reason cannot exist.
    """
    async with org_session(org_id) as session:
        row = UsageLedger(
            org_id=org_id,
            run_id=run_id,
            actor_user_id=actor_user_id,
            role=role,
            tier=tier,
            provider=provider,
            model=model,
            status=STATUS_ERROR if error is not None else STATUS_OK,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            tokens_estimated=usage.estimated,
            cost_usd=estimate_cost(model, usage, settings),
            latency_ms=latency_ms,
            repaired=repaired,
            error=error,
        )
        session.add(row)
        await session.flush()
        return row.id
