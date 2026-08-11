#!/usr/bin/env bash
#
# Prepare the local platform database: run migrations as the owner, then give
# dataagent_app a login so the API can connect as an unprivileged role.
#
# Local development only. In Azure (Phase 12) the role's credential comes from
# Key Vault and this script has no equivalent.

set -euo pipefail

if [[ ! -f .env ]]; then
  echo "No .env found. Run 'make env' first." >&2
  exit 1
fi

# shellcheck disable=SC1091
set -a
. ./.env
set +a

: "${PLATFORM_DB_USER:?PLATFORM_DB_USER missing from .env}"
: "${PLATFORM_DB_NAME:?PLATFORM_DB_NAME missing from .env}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD missing from .env}"

echo "Applying migrations as the owner role..."
uv run --directory apps/api alembic upgrade head

echo "Granting dataagent_app a local login..."
docker compose --env-file .env -f ops/docker-compose.yml exec -T platform-pg \
  psql -v ON_ERROR_STOP=1 \
       -U "$PLATFORM_DB_USER" \
       -d "$PLATFORM_DB_NAME" \
       -v app_password="$APP_DB_PASSWORD" \
  < ops/sql/app_role.sql

echo
echo "Done. The API connects as dataagent_app via APP_DATABASE_URL."
