"""an answer says what it could not do, and a finding says whether it was used

Two columns the composer needs and nothing had (architecture 4.2's
``ComposedAnswer``, and M9's *"answers cite findings; limitations rendered"*).

``agent_runs.limitations`` rather than a field inside ``state``: the checkpoint
is the agent's own scratchpad, and the answer card would then be reading the
agent's account of itself to decide what to show a person. A limitation is part
of the answer — the part that says what the answer is not — so it belongs where
the answer's other durable facts are, queryable without deserializing a blob.

``findings.cited`` because a run reaches several findings and the composed answer
rests on some of them. Without the flag the card either shows every intermediate
conclusion, which buries the answer, or shows none, which hides the evidence.

Both are additive with defaults, so every row written before this revision reads
correctly after it: no limitations, and nothing marked cited.

No column comments here: the drift check compares the models to the migration,
and this codebase documents columns on the model (the same reason 0014 has
none). What each column is for is written on `AgentRun.limitations` and
`Finding.cited`.

Revision ID: 0015
Revises: 0014
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column(
            "limitations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "findings",
        sa.Column(
            "cited",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("findings", "cited")
    op.drop_column("agent_runs", "limitations")
