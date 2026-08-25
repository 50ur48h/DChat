"""agent_runs.answered — a refusal stops reading as an answer (B-133)

**The same defect as B-100, one field over.** `RunOutcome.answered` has been
computed on every run since WP7.2b and stored nowhere durable: `_finish` puts it
in the `run_finished` event's `totals` and that is the end of it. `RunView` never
carried it, `RunOut` never returned it, and the screen has no way to ask.

So the conversation card renders `STATUS_WORDS[run.status]`, in which `completed`
is the word **"answered"** — and WP7.2b's rule is that *"a run that could not
answer completes with `answered=false` and a reason"*. A refusal is a `completed`
run by design. The screen was therefore labelling every honest refusal with the
one claim the refusal exists to deny.

Seen on the deployed app on 2026-08-25: *"which outlet wastes the most, and what
does it cost?"* came back badged **answered**, carrying **no supporting query**,
and saying *"The data does not establish which outlet wastes the most."* Three
statements, one of them contradicting the other two.

**A column rather than reading the event, for two reasons.** `_run_view` is called
once per run by `list_conversation_runs`, so pulling `totals` out of
`agent_events` would be a query per run to render a thread. And `answered` is a
property of the ending exactly as `failure_reason` and `method` are, both of which
are columns here already — `method` for precisely this reason, in revision 0025.

**Nullable, and null for every run written before this revision.** Those runs
ended without recording it, and deriving one now — from the presence of findings,
say — would be this migration asserting something it cannot know. The screen
treats null as "not recorded" and falls back to the status word, which is what it
did for all of them anyway.

Revision ID: 0029
Revises: 0028
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("answered", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "answered")
