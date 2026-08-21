#!/usr/bin/env sh
#
# Prepare the local platform database: run migrations as the owner, then give
# dataagent_app a login so the API can connect as an unprivileged role.
#
# Local development only. In Azure (Phase 12) the role's credential comes from
# Key Vault and this script has no equivalent.
#
# POSIX sh, not bash: on Windows a bare `bash` resolves to WSL's bash, which
# cannot see the Docker CLI or this drive the same way. The Makefile runs this
# with $(SHELL), which is the shell it already verified.

set -eu

if [ ! -f .env ]; then
  echo "No .env found. Run 'make env' first." >&2
  exit 1
fi

# **Read three values; do not source the file** (**B-102**). The reasoning, and
# the reader that carries it, live in env_file.sh — shared with web_smoke.sh,
# which needs the same values for the same reason. Two hand-rolled copies is how
# the two would drift, and the drifted one would be whichever nobody was running.
. "$(dirname "$0")/env_file.sh"

PLATFORM_DB_USER=$(env_value PLATFORM_DB_USER)
PLATFORM_DB_NAME=$(env_value PLATFORM_DB_NAME)
APP_DB_PASSWORD=$(env_value APP_DB_PASSWORD)

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
