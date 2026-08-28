#!/usr/bin/env sh
#
# Load a file of metric definitions into a registered data source.
#
#     make seed.definitions FILE=ops/seed/miseq_definitions.json SOURCE="MiseQ v6.4"
#
# Inside the api container, for the reason demo_setup.sh gives: writing a
# definition validates its filters against the catalog, so whoever writes one
# has to stand where the product stands.
#
# POSIX sh, not bash — see demo_setup.sh.

set -eu

CONTAINER="${DEMO_CONTAINER:-dataagent-api-1}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
FILE="${FILE:-ops/seed/miseq_definitions.json}"

if [ ! -f "$ROOT/$FILE" ] && [ ! -f "$FILE" ]; then
  echo "No such file: $FILE" >&2
  exit 1
fi
[ -f "$FILE" ] || FILE="$ROOT/$FILE"

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "No container called $CONTAINER. Run 'make up' first." >&2
  exit 1
fi

docker cp "$ROOT/ops/seed/provision_definitions.py" "$CONTAINER:/tmp/provision_definitions.py" >/dev/null
docker cp "$FILE" "$CONTAINER:/tmp/definitions.json" >/dev/null

# `exec` first: Git Bash rewrites a leading `/` into a Windows path.
docker exec \
  -e DEFINITIONS_FILE=/tmp/definitions.json \
  -e DEFINITIONS_SOURCE="${SOURCE:-}" \
  -e DEMO_ORG_NAME="${DEMO_ORG_NAME:-Demo}" \
  "$CONTAINER" sh -c "exec python /tmp/provision_definitions.py"
