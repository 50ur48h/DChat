"""usage_ledger.cached_input_tokens — the discount we have been billing at full price

**Recorded, not yet priced, and that order is the owner's** (2026-08-29):
*"Record cached_tokens first, before touching pricing. The gap becomes an
observation rather than an argument."*

OpenAI's `/responses` reports `input_tokens_details.cached_tokens`, and cached
input is billed at a discount. `input_tokens` is the number **inclusive** of
them, and that is the number `meter.estimate_cost` multiplies by the full input
rate — so every cache hit makes our figure larger than the invoice.

The workload is about as cache-friendly as one gets. A single run on 2026-08-28
made eight `sql` calls averaging ~7,000 input tokens each behind a stable
prefix, and input was **50.4%** of that run's $0.6567.

**What made it unarguable-in-principle and unmeasurable-in-fact** is that the
number was never stored. `Usage` carried `input_tokens` and `output_tokens` and
nothing else, so how much of the bill was cached could be reasoned about and not
counted. This column ends that, and deliberately changes no total: the next
question — what the discount is, and whether to model it — is answered against
recorded data rather than against an argument about likelihoods.

Nullable, and null is not zero. Every row written before this revision has an
unknown cached share, and a provider that does not report one leaves it unknown
too; `0` would claim we asked and were told none, which for those rows is false.

Revision ID: 0034
Revises: 0033
Create Date: 2026-08-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "usage_ledger",
        sa.Column("cached_input_tokens", sa.Integer(), nullable=True),
    )
    # Cached input is a *subset* of input, never an addition to it. Without this
    # a bad adapter could record more cached tokens than were sent and the ratio
    # would exceed 1 with nothing to catch it.
    op.create_check_constraint(
        "cached_within_input",
        "usage_ledger",
        "cached_input_tokens IS NULL OR "
        "(cached_input_tokens >= 0 AND cached_input_tokens <= input_tokens)",
    )


def downgrade() -> None:
    op.drop_constraint("cached_within_input", "usage_ledger", type_="check")
    op.drop_column("usage_ledger", "cached_input_tokens")
