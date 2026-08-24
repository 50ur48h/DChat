"""Five SECURITY DEFINER lookups, so the API can stop being the owner (B-123)

**What was wrong.** CLAUDE.md's hard rule is *"The API connects as
`dataagent_app` (no superuser, no BYPASSRLS, owns nothing). Migrations run as the
owner. Never collapse the two."* The API collapsed them: `system_session()` is the
owner connection, and eight request-path call sites used it — including
`auth/context.py`, which runs on **every authenticated request**. It was invisible
for eleven phases because every developer `.env` sets both DSNs, so the owner
connection was always available and always worked. Azure was the first
environment that handed the API only the unprivileged DSN, and the API failed
immediately.

**Why the obvious fix was refused.** Giving the deployed API `DATABASE_URL` makes
the rule false in the one environment where it matters, and the owner declined it
on 2026-08-24: a temporary owner credential is not revisited.

**Why three of the eight needed nothing.** `resolve_user_id`, `ensure_user` and
`record_security_event` touch `users` and `security_events`, which are not tenant
tables and carry no RLS policy. They moved to the application role unchanged;
`dataagent_app` has held the grants since revision 0002.

**Why the other five could not simply move.** They read *genuine tenant tables*
either across every organization or **before the organization is known** — which
is the authorization bootstrap, since `app.org_id` cannot be set until the
caller's membership has been discovered. Under the application role with RLS in
force they would return nothing, which `rls_proof`'s *"a session without an org
sees nothing and says so"* already asserts is correct behaviour. The bypass is
real and it is needed.

**So the bypass becomes an enumerable list instead of an ambient privilege.**
That is the owner's reasoning and it is the same shape as everything else here:
`TENANT_TABLES` is a list, the SQL allowlist is a list, `PLATFORM_ENV` is a list.
Five functions, each doing exactly one thing, are five lines a reviewer can read
— where "the API happens to hold the owner credential" is a capability nobody can
enumerate at all.

**Four properties every function below has, and each is load-bearing.**

* ``SECURITY DEFINER`` — runs as the owner, which is what supplies the bypass.
* ``SET search_path = pg_catalog, public`` — **the one that matters most.** A
  SECURITY DEFINER function without a pinned search_path is the textbook
  PostgreSQL privilege escalation: the caller prepends a schema of their own,
  and an unqualified name inside the body resolves to their object, executed as
  the owner. `pg_catalog` first so built-in operators cannot be shadowed either.
* ``STABLE`` and read-only — none of these writes. Both token flows already do
  every write inside `org_session`, which is why only the lookups are here.
* ``REVOKE ... FROM PUBLIC`` then ``GRANT EXECUTE`` to the application role
  alone. A SECURITY DEFINER function is executable by PUBLIC by default, and
  granting without revoking leaves it that way.

**No function takes a table name, a column name, or any fragment of SQL.** Every
parameter is a value. A SECURITY DEFINER function that assembles a statement is
the same hole with more steps, and the narrowest query that answers the question
is the whole point of the design.

`tests/db/test_security_definer.py` enumerates every SECURITY DEFINER function in
the database and fails on any not declared in `SECURITY_DEFINER_FUNCTIONS`, in the
`TENANT_TABLES` idiom — an allowlist nothing checks is a comment.
"""

from __future__ import annotations

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

APP_ROLE = "dataagent_app"

#: The statuses a run can be left in by a restart. Duplicated from
#: `agent.scheduler.ORPHANABLE` deliberately: a migration that imported
#: application code would run whatever that code says today against a schema from
#: whenever it was written. `tests/db/test_security_definer.py` asserts the two
#: agree, which is the same arrangement `TENANT_TABLES` has with revision 0002.
ORPHANABLE = ("queued", "running", "validating")

_FUNCTIONS: tuple[tuple[str, str, str], ...] = (
    (
        "auth_membership_role",
        "p_user_id uuid, p_org_id uuid",
        """
        RETURNS text
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT m.role
            FROM org_memberships m
            WHERE m.user_id = p_user_id AND m.org_id = p_org_id
        $$
        """,
    ),
    (
        "auth_memberships_for_user",
        "p_user_id uuid",
        """
        RETURNS TABLE (org_id uuid, org_name varchar, member_role varchar)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT m.org_id, o.name, m.role
            FROM org_memberships m
            JOIN organizations o ON o.id = m.org_id
            WHERE m.user_id = p_user_id
            ORDER BY o.name
        $$
        """,
    ),
    (
        "auth_invitation_by_token",
        "p_token_hash varchar",
        """
        RETURNS TABLE (
            id uuid,
            org_id uuid,
            org_name varchar,
            member_role varchar,
            expires_at timestamptz,
            accepted_at timestamptz
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT i.id, i.org_id, o.name, i.role, i.expires_at, i.accepted_at
            FROM invitations i
            JOIN organizations o ON o.id = i.org_id
            WHERE i.token_hash = p_token_hash
        $$
        """,
    ),
    (
        "auth_recovery_grant_by_token",
        "p_token_hash varchar",
        """
        RETURNS TABLE (
            id uuid,
            org_id uuid,
            org_name varchar,
            expires_at timestamptz,
            used_at timestamptz,
            revoked_at timestamptz
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT g.id, g.org_id, o.name, g.expires_at, g.used_at, g.revoked_at
            FROM org_recovery_grants g
            JOIN organizations o ON o.id = g.org_id
            WHERE g.token_hash = p_token_hash
        $$
        """,
    ),
    (
        "ops_orphaned_runs",
        "",
        f"""
        RETURNS TABLE (run_id uuid, org_id uuid)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT r.id, r.org_id
            FROM agent_runs r
            WHERE r.status IN ({", ".join(f"'{s}'" for s in ORPHANABLE)})
        $$
        """,
    ),
)


def upgrade() -> None:
    for name, args, body in _FUNCTIONS:
        op.execute(f"CREATE OR REPLACE FUNCTION {name}({args}) {body}")
        # Revoke first. A SECURITY DEFINER function is executable by PUBLIC the
        # moment it exists, so granting without revoking leaves every role able
        # to call it — including, on a shared server, roles this product has
        # never heard of.
        op.execute(f"REVOKE ALL ON FUNCTION {name}({args}) FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {name}({args}) TO {APP_ROLE}")


def downgrade() -> None:
    for name, args, _ in reversed(_FUNCTIONS):
        op.execute(f"DROP FUNCTION IF EXISTS {name}({args})")
