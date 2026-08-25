"""agent_runs.outcome_state and .unanswered — a question can be half-answered (B-134, D-044)

**Revision 0029 was one day old and already describing runs wrongly.** It added
`answered boolean`, on WP7.2b's premise that a run either answers or does not. The
first engine trial found three runs of *"which outlet wastes the most, and what
does it cost?"* that recorded `answered = false` while answering the volume half —
*"Outlet C, 3.398 kg across 2 waste events"* — two of them backed by a **cited,
verified** finding. The platform's own record asserted the run produced nothing,
in the same transaction as a claim it stood behind.

**`outcome_state` replaces the boolean rather than joining it.** Two fields that
must agree are two fields that will not, and the boolean has no true value for a
partial run: `false` denies the 3.398 kg, `true` denies the missing cost.

**`unanswered` is what makes the state useful rather than merely correct.** A card
that says *"partly answered"* and nothing else tells a reader less than the old
wrong badge did — they now know something is missing and not what. The state is
only ever `partly` when this column has text, so *"could not answer the cost"* is
always renderable and a bare partial badge is unreachable by construction.

**The back-fill asserts exactly what each row already said**, and no more:
`true → answered`, `false → refused`, `null → null`. Mapping `false` to `refused`
is not a claim that those runs were total refusals — it is a record of what the
row stated at a time when partial was not representable. The alternative, mapping
everything to null, would lose the badge for every historical refusal to avoid a
claim no reader would misread. Owner's call, 2026-08-25.

Revision ID: 0030
Revises: 0029
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

#: The three endings a run can have once it has finished. Mirrored in
#: `agent/composer.py` as `RunState`, and `tests/agent/test_outcome_state.py`
#: asserts the two agree — the arrangement `TENANT_TABLES` has with revision 0002,
#: for the same reason: a list in two languages needs something that counts.
OUTCOME_STATES = ("answered", "partly", "refused")


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("outcome_state", sa.Text(), nullable=True))
    op.add_column("agent_runs", sa.Column("unanswered", sa.Text(), nullable=True))

    # What the row already said, and nothing beyond it.
    op.execute(
        "UPDATE agent_runs SET outcome_state = CASE "
        "WHEN answered IS TRUE THEN 'answered' "
        "WHEN answered IS FALSE THEN 'refused' "
        "END"
    )

    op.create_check_constraint(
        "outcome_state_valid",
        "agent_runs",
        "outcome_state IS NULL OR outcome_state IN ({})".format(
            ", ".join(f"'{state}'" for state in OUTCOME_STATES)
        ),
    )
    # **The property the card depends on, enforced where it cannot be edited
    # away.** `partly` without the missing half is a badge that says less than
    # the wrong one did, so the database refuses the combination rather than
    # trusting every future writer to remember.
    op.create_check_constraint(
        "partly_names_what_is_missing",
        "agent_runs",
        "outcome_state <> 'partly' OR (unanswered IS NOT NULL AND unanswered <> '')",
    )

    op.drop_column("agent_runs", "answered")


def downgrade() -> None:
    op.add_column("agent_runs", sa.Column("answered", sa.Boolean(), nullable=True))
    # `partly` has no true boolean. It becomes `false`, which is what this column
    # meant before the distinction existed, and the information is lost — which is
    # the honest consequence of going backwards rather than something to paper over.
    op.execute(
        "UPDATE agent_runs SET answered = CASE "
        "WHEN outcome_state = 'answered' THEN true "
        "WHEN outcome_state IN ('partly', 'refused') THEN false "
        "END"
    )
    op.drop_constraint("partly_names_what_is_missing", "agent_runs", type_="check")
    op.drop_constraint("outcome_state_valid", "agent_runs", type_="check")
    op.drop_column("agent_runs", "unanswered")
    op.drop_column("agent_runs", "outcome_state")
