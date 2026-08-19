"""agent_runs.chart — the picture an answer carries, or why it has none (WP11.1)

Architecture 4.2 puts chart specs on the `ComposedAnswer`, and this is where an
answer's other parts already live: `answer` is a message, `limitations` is a
column on the run. A chart belongs with them because **B-048** puts it inside the
answer card rather than beside it.

**One column for both outcomes, because a refusal is an outcome.** The chart tool
returns a spec to render *or* a sentence saying why it drew nothing, never
neither, and both belong to the answer. Keeping only the spec would leave the
refusal homeless and reproduce the failure the tool was written to prevent — a
picture that silently does not appear looks like a broken page, which is B-087's
lesson applied to charts.

**Why not on the finding, which is what plan WP11.1 said.** A decline can exist
on a run that reached no finding at all — a question the data could not answer
still gets a "no chart, and here is why". Attaching the outcome to a finding
would give the successful half a home and the refused half none. The plan's
sentence is corrected in the same PR; nothing in the architecture changes, since
4.2 already carries chart specs on the answer rather than on a finding.

The spec's link to the result it was drawn from survives anyway: a spec is always
of an execution this run cited, and the card already holds those ids.

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNS = "agent_runs"


def upgrade() -> None:
    # Nullable rather than defaulted to `{}`: a run from before this revision
    # asked for no chart and was told nothing, and `NULL` says that without
    # pretending an empty object is a decision somebody made.
    op.add_column(RUNS, sa.Column("chart", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column(RUNS, "chart")
