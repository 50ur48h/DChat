-- Give the application role a login for local development.
--
-- The role and every grant it holds come from migration 0002. Only the password
-- lives here, because a migration must never contain a credential.
--
-- **The Phase 12 half of that promise now exists, and it is half** (WP12.2). A
-- deployment does this same ALTER ROLE from `dataagent.db.grant_app_login`, run
-- by the migration job because the Postgres server has no public endpoint, with
-- the password coming from Key Vault rather than from a file. What this header
-- used to promise as well — a *managed identity* instead of a password — is
-- deferred and recorded as **B-121**, not quietly dropped. This file stays: it
-- is what `make db.setup` runs, and local development has no managed identity to
-- authenticate with.
--
-- Run through `make db.setup`, which supplies :app_password from .env.

\set ON_ERROR_STOP on

ALTER ROLE dataagent_app WITH LOGIN PASSWORD :'app_password';

-- Read back what the role can actually do. If any of these is `t`, tenant
-- isolation is not what this project claims it is.
SELECT rolname,
       rolsuper      AS is_superuser,
       rolbypassrls  AS can_bypass_rls,
       rolcreatedb   AS can_create_db,
       rolcreaterole AS can_create_role
FROM pg_roles
WHERE rolname = 'dataagent_app';
