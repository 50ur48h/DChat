#!/usr/bin/env sh
# Run the golden evals inside the API container (plan WP9.2).
#
# Inside, because the data source is registered with the compose network's own
# hostname — `seed-pizza-pg` resolves there and nowhere else. A harness run from
# the host gets `getaddrinfo failed` and reports twenty failures that are really
# one DNS lookup, which is the least useful failure a suite can produce.
# `scripts/agent_smoke.py` is run the same way and for the same reason.
#
# The files are copied in rather than mounted: the api service mounts only
# `apps/api/src`, and widening that for a harness would put the whole repository
# inside a container that has customer credentials in its environment.

set -eu

CONTAINER="${EVALS_CONTAINER:-dataagent-api-1}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
    echo "No container called $CONTAINER. Run 'make up' first." >&2
    exit 1
fi

if [ -z "${EVALS_ORG_ID:-}" ]; then
    echo "EVALS_ORG_ID is not set." >&2
    echo "It needs an organization with the pizza seed registered as a data" >&2
    echo "source — the same state 'make agent.smoke' needs." >&2
    exit 1
fi

docker exec "$CONTAINER" sh -c "exec mkdir -p /tmp/evals"
docker cp "$ROOT/ops/evals/runner.py" "$CONTAINER:/tmp/evals/runner.py" >/dev/null
docker cp "$ROOT/ops/evals/golden.yaml" "$CONTAINER:/tmp/evals/golden.yaml" >/dev/null
docker cp "$ROOT/ops/seed/truths.json" "$CONTAINER:/tmp/evals/truths.json" >/dev/null
docker cp "$ROOT/apps/api/tests/llm_fixture.py" "$CONTAINER:/tmp/evals/llm_fixture.py" >/dev/null

# `exec` first: Git Bash rewrites a leading `/` into a Windows path, so the
# command string has to start with a word (see CLAUDE.md).
docker exec \
    -e EVALS_ORG_ID="$EVALS_ORG_ID" \
    -e EVALS_SOURCE="${EVALS_SOURCE:-Demo}" \
    -e EVALS_LIVE="${EVALS_LIVE:-}" \
    -e EVALS_TOKEN_BUDGET="${EVALS_TOKEN_BUDGET:-400000}" \
    "$CONTAINER" sh -c "exec python /tmp/evals/runner.py $*"
