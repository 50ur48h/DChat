#!/usr/bin/env bash
#
# The API's real coverage number, assembled from every job that measured part of
# it (B-016).
#
# The test suite is deliberately split across CI jobs: `api` has a Postgres
# service, `mssql` has a SQL Server one, and neither can run the other's tests.
# Measuring in one job alone therefore reports the *other* engine's connector as
# nearly untested — `connectors/sqlserver.py` at ~27%, dragging the total from 94
# to 88 — while the job that does exercise it measures nothing at all. That gap
# is invisible until a threshold lands on top of it, and then it is a blocked
# merge with no bug behind it.
#
# So each job writes its own `.coverage.<shard>` data file, uploads it, and this
# script combines them into one number that is true (plan §4.4).
#
# Usage:
#   bash scripts/coverage_report.sh
#
# Environment:
#   COVERAGE_DATA_DIR   where the downloaded shards are (default apps/api/coverage-data)
#   COVERAGE_FAIL_UNDER percentage floor (default 70, per plan §4.4 from Phase 5)

set -euo pipefail

API_DIR=${API_DIR:-apps/api}
COVERAGE_DATA_DIR=${COVERAGE_DATA_DIR:-$API_DIR/coverage-data}
COVERAGE_FAIL_UNDER=${COVERAGE_FAIL_UNDER:-70}

# The shards that exist, and what each one is the only source of. A missing
# shard is not an error — most PRs do not touch the connectors, and the SQL
# Server job does not run for them — but it does change what the number means,
# so it is said out loud rather than left for someone to infer.
shard_note() {
  case $1 in
    api) printf 'the API suite, against Postgres' ;;
    mssql) printf 'the connector suite, against SQL Server' ;;
    *) printf 'unrecognised shard' ;;
  esac
}

shopt -s nullglob
shards=("$COVERAGE_DATA_DIR"/.coverage.*)
shopt -u nullglob

if ((${#shards[@]} == 0)); then
  # Every job that would have produced a shard was skipped by the path filters,
  # which is the normal state of a docs-only PR. Reporting 0% here would be a
  # lie about untested code rather than a measurement of it.
  echo "coverage: no shards were uploaded — nothing ran that measures anything."
  exit 0
fi

echo "coverage: combining ${#shards[@]} shard(s):"
found_mssql=0
for shard in "${shards[@]}"; do
  name=${shard##*/.coverage.}
  printf '  %-8s %s\n' "$name" "$(shard_note "$name")"
  [[ $name == mssql ]] && found_mssql=1
done

if ((found_mssql == 0)); then
  echo
  echo "coverage: no SQL Server shard in this run, so connectors/sqlserver.py is"
  echo "          measured only by what the API suite imports. The total below is"
  echo "          structurally low for that reason, not because code lost tests."
fi

echo
# `combine` consumes the shard files and leaves one `.coverage` behind. Paths
# inside them are relative (relative_files in pyproject), so they line up across
# jobs and with the checkout this runs in. The data directory is passed as an
# absolute path because the commands below run from inside $API_DIR.
data_dir=$(cd "$COVERAGE_DATA_DIR" && pwd)
uv run --directory "$API_DIR" coverage combine "$data_dir"
uv run --directory "$API_DIR" coverage report --fail-under="$COVERAGE_FAIL_UNDER"
