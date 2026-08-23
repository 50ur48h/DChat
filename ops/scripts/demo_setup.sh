#!/usr/bin/env sh
#
# Build an organization the product can query, from an empty platform database
# (**B-115**).
#
#     make demo.setup
#
# Inside the api container, and that is the point rather than a convenience.
# Registering a data source also *connects* to it — `test_data_source`, discovery
# and profiling all open the database — so whoever provisions has to stand where
# the product stands. A host process cannot honestly register `seed-pizza-pg`,
# because it cannot reach it to check; and the address it can reach,
# `localhost:6543`, is the one the API cannot. `ops/scripts/evals.sh` copies its
# harness in for the same reason.
#
# The file is copied rather than mounted: the api service mounts only
# `apps/api/src`, and widening that for a provisioner would put the whole
# repository inside a container that has customer credentials in its environment.
#
# POSIX sh, not bash: on Windows a bare `bash` resolves to WSL's bash, which
# cannot see the Docker CLI or this drive the same way. The Makefile runs this
# with $(SHELL), which is the shell it already verified.

set -eu

CONTAINER="${DEMO_CONTAINER:-dataagent-api-1}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

if [ ! -f .env ]; then
  echo "No .env found. Run 'make env' first." >&2
  exit 1
fi

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "No container called $CONTAINER. Run 'make up' first." >&2
  exit 1
fi

# **Read the two values; do not source the file** (**B-102**). The reasoning
# lives in env_file.sh, shared with db_setup.sh and web_smoke.sh.
. "$(dirname "$0")/env_file.sh"

SEED_PIZZA_READONLY_PASSWORD=$(env_value SEED_PIZZA_READONLY_PASSWORD)
SEED_FNB_READONLY_PASSWORD=$(env_value SEED_FNB_READONLY_PASSWORD)

: "${SEED_PIZZA_READONLY_PASSWORD:?SEED_PIZZA_READONLY_PASSWORD missing from .env}"

docker cp "$ROOT/ops/seed/provision_demo.py" "$CONTAINER:/tmp/provision_demo.py" >/dev/null

# `exec` first: Git Bash rewrites a leading `/` into a Windows path, so the
# command string has to start with a word (see CLAUDE.md).
docker exec \
  -e SEED_PIZZA_READONLY_PASSWORD="$SEED_PIZZA_READONLY_PASSWORD" \
  -e SEED_FNB_READONLY_PASSWORD="$SEED_FNB_READONLY_PASSWORD" \
  -e DEMO_ORG_NAME="${DEMO_ORG_NAME:-Demo}" \
  "$CONTAINER" sh -c "exec python /tmp/provision_demo.py"
