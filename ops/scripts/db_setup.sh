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

# **Read three values; do not source the file.** This used to be
# `set -a; . ./.env; set +a`, which exports every key — and a shell assignment
# removes quotes, so `LLM_ROLE_MAP={"compose":"small"}` arrived in the
# environment as `{compose:small}`. Environment beats dotenv in pydantic's
# settings order, so the mangled value won over the correct one in the file and
# `alembic upgrade head` died parsing it. That made **step 2 of the README
# quickstart fail from a clean state**, on any machine whose .env came from
# .env.example — which ships all three JSON keys uncommented (WP11.2b).
#
# Taking only what this script uses fixes the class rather than the instance: no
# future JSON-valued key can break a migration by being present, and the Python
# that follows reads .env itself, where a JSON parser handles it correctly.
env_value() {
  # Last assignment wins, as a shell would. The value is taken verbatim except
  # for one pair of matching surrounding quotes, which a person may reasonably
  # have written around a password containing spaces.
  value=$(sed -n "s/^$1=//p" .env | tail -n 1)
  case $value in
  \"*\") value=${value#\"}; value=${value%\"} ;;
  '*') value=${value#'}; value=${value%'} ;;
  esac
  printf '%s' "$value"
}

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
