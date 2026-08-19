"""a definition's history learns the word `reinstated` (B-094)

Revision 0022 gave a definition four states it could be put into: written,
accepted, edited, retired. Retiring was the last of them in both senses — there
was no way back through the product, and `accept` refuses anything that is not
`proposed`, `PATCH` refuses anything that is not `active`, and an import skips a
name any row already holds. Three individually correct rules, and together they
left a mis-clicked **Retire** recoverable only in `psql`, which is the shape of
the hole **B-088** was filed for one verb earlier.

The owner raised it to P1 on their first real use of the feature: *"a semantic
layer whose definitions are write-once will not survive real use. Retire with no
way back is the same sentence one verb later, and worse in one respect: retired
definitions vanish from the screen, so nobody can see there is anything to bring
back."*

**Why a fifth word rather than reusing `updated`.** Bringing a definition back
into force is a decision, not a field edit — the same argument that made `accept`
and `reject` two POSTs rather than one PATCH of `status`. A history that recorded
it as `updated` would read as though somebody had changed the wording, and the
one thing the history exists to answer is *what was in force when that answer was
written*. The gap between retired and active is exactly what a reader needs to
see.

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-19
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VERSIONS = "semantic_definition_versions"
#: The bare name. `env.py` configures the metadata's naming convention, so
#: alembic renders this into `ck_semantic_definition_versions_change_valid` —
#: passing the rendered name instead gets it prefixed a second time.
CONSTRAINT = "change_valid"

BEFORE = ("created", "accepted", "updated", "retired")
AFTER = ("created", "accepted", "updated", "retired", "reinstated")


def _in_list(values: Sequence[str]) -> str:
    return "change IN ({})".format(", ".join(f"'{value}'" for value in values))


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, VERSIONS, type_="check")
    op.create_check_constraint(CONSTRAINT, VERSIONS, _in_list(AFTER))


def downgrade() -> None:
    # A row already written as `reinstated` would fail the narrower constraint,
    # and there is no honest value to rewrite it to: `updated` would claim
    # somebody edited the wording. Deleting the row is worse still — the history
    # is append-only precisely so that nothing quietly removes a state a run may
    # have been judged against. So the downgrade refuses rather than guesses,
    # and says which rows are in the way.
    op.execute(f"""
        DO $$
        DECLARE offending integer;
        BEGIN
            SELECT count(*) INTO offending FROM {VERSIONS} WHERE change = 'reinstated';
            IF offending > 0 THEN
                RAISE EXCEPTION
                    'cannot downgrade: % definition version row(s) record a reinstatement, '
                    'and no earlier value describes it honestly', offending;
            END IF;
        END $$;
    """)
    op.drop_constraint(CONSTRAINT, VERSIONS, type_="check")
    op.create_check_constraint(CONSTRAINT, VERSIONS, _in_list(BEFORE))
