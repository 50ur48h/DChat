"""row-level security, the application role, and append-only audit_log

Architecture Part 6.3. This is the structural half of tenant isolation: even a
repository that forgets its ``WHERE org_id = ...`` cannot return another
organization's rows, because the database will not hand them over.

Three things make that true, and all three are needed:

* ``ENABLE ROW LEVEL SECURITY`` turns policies on for ordinary roles.
* ``FORCE ROW LEVEL SECURITY`` applies them to the table **owner** as well —
  without it, whoever owns the table silently bypasses every policy.
* ``dataagent_app`` is a plain role: no superuser, no BYPASSRLS, and not the
  owner of anything. It is what the API connects as.

The role is created here without a password and without LOGIN, because a
migration must never contain a credential. Granting it LOGIN and a password is
environment provisioning: `make db.setup` locally, Key Vault + Bicep in Phase 12.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "dataagent_app"
POLICY = "org_isolation"

#: table -> the column that identifies its tenant. Mirrors
#: dataagent.db.models.TENANT_TABLES; a test asserts the two agree, so this copy
#: cannot rot. It is duplicated deliberately: a migration must describe the
#: schema as it was at this revision, not as the models happen to look today.
TENANT_TABLES: dict[str, str] = {
    "organizations": "id",
    "org_memberships": "org_id",
    "invitations": "org_id",
    "audit_log": "org_id",
}


def upgrade() -> None:
    # Idempotent: the role is a cluster-level object, so it may already exist
    # from another database on the same server (a test database, for instance).
    op.execute(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} NOLOGIN;
            END IF;
        END
        $$;
    """)

    op.execute(f"""
        DO $$
        BEGIN
            EXECUTE format('GRANT CONNECT ON DATABASE %I TO {APP_ROLE}', current_database());
        END
        $$;
    """)
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    # Identity columns draw from a sequence; without USAGE, every audit insert fails.
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")

    # Tables added by later revisions inherit the same grants, so a new table is
    # never accidentally unreadable — or, worse, quietly readable by no one until
    # someone "fixes" it with a broader grant.
    op.execute(f"""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}
    """)
    op.execute(f"""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}
    """)

    # Append-only, enforced by the database rather than by discipline
    # (architecture Part 8.2). The application can write history; it cannot
    # rewrite it.
    op.execute(f"REVOKE UPDATE, DELETE ON audit_log FROM {APP_ROLE}")

    for table, key in TENANT_TABLES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        # USING filters what can be read; WITH CHECK stops a row being written
        # under another organization's id. Both, or the protection is half a
        # protection.
        op.execute(f"""
            CREATE POLICY {POLICY} ON {table}
            USING ({key} = current_setting('app.org_id')::uuid)
            WITH CHECK ({key} = current_setting('app.org_id')::uuid)
        """)


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute(f"""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {APP_ROLE}
    """)
    op.execute(f"""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        REVOKE USAGE, SELECT ON SEQUENCES FROM {APP_ROLE}
    """)
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {APP_ROLE}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {APP_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}")
    op.execute(f"""
        DO $$
        BEGIN
            EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM {APP_ROLE}', current_database());
        END
        $$;
    """)

    # The role itself is deliberately left in place: it is cluster-wide, so
    # another database on this server may still be using it. Dropping it here
    # would break that database from underneath.
