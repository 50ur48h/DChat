#!/usr/bin/env sh
#
# Run `alembic upgrade head` against the deployed platform database (WP12.2).
#
# **A one-off Container Apps job, not a step on this runner**, for two reasons
# that are both structural rather than stylistic:
#
#   * The Postgres flexible server has **no public endpoint**. It is reachable
#     from inside the Container Apps environment's subnet and from nowhere else,
#     so a runner cannot connect to it at all — and the fix for that is not a
#     firewall rule opening the database to GitHub's address range.
#   * Migrations run as the **owner** role, and the API deliberately does not.
#     Keeping the two apart is the rule CLAUDE.md states and the reason
#     `dataagent_app` owns nothing; a migration step that borrowed the app's
#     credential would quietly collapse that.
#
# **Ordering is the whole point.** This runs after the image is pushed and before
# the app revision is swapped. The alternative orderings are both worse: migrate
# after the swap and the new revision serves requests against a schema it does
# not expect, which answers wrongly rather than failing; skip the wait below and
# the deploy reports success while the migration is still running.
#
# POSIX sh, for the reason the other ops scripts are: see db_setup.sh.

set -eu

: "${RESOURCE_GROUP:?RESOURCE_GROUP is not set}"
: "${REGISTRY:?REGISTRY is not set; it comes from the infra deployment outputs}"
: "${IMAGE_TAG:?IMAGE_TAG is not set}"

JOB_NAME="${MIGRATE_JOB_NAME:-cj-dataagent-migrate-dev}"

# **Print why the job failed, and get this right: it is the only thing standing
# between a failed deploy and an afternoon.**
#
# The first version called `az containerapp job logs show` without `--container`,
# which that command requires. So the step whose entire purpose is explaining a
# failure printed a CLI usage message instead — and it did it in the case that
# matters, a real migration failure, where the actual cause (`extension
# "pgcrypto" is not allow-listed`) was sitting in the container stdout and never
# surfaced.
#
# Two sources, because the first stops working at exactly the wrong moment:
# `job logs show` reads the live replica, and Container Apps deletes it within a
# few minutes of the execution ending. After that it answers `No replicas found
# for execution`, which is neither an error nor the logs. The fallback queries
# Log Analytics, where the diagnostic setting sends the same output.
#
# **Console logs take up to ~15 minutes to reach Log Analytics.** Measured: when
# this was first needed, `ContainerAppConsoleLogs` did not exist as a table at
# all, and the rows appeared later. An empty fallback means "not yet", never
# "the container said nothing" — which is the difference between waiting two
# minutes and rebuilding an image to add print statements.
dump_logs() {
  execution=$1
  echo "--- container output for $execution ---" >&2
  if az containerapp job logs show \
    --name "$JOB_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --container migrate \
    --execution "$execution" \
    --tail 200 >&2 2>/dev/null; then
    return 0
  fi

  echo "The replica is gone, so live logs are unavailable. Querying Log Analytics." >&2
  # Installed explicitly rather than left to az's dynamic install, which prompts
  # when stdin is not a terminal and then dies with `EOFError: EOF when reading a
  # line` — a failure mode that replaces the diagnosis with a Python traceback.
  az extension add --name log-analytics --allow-preview true --only-show-errors >/dev/null 2>&1 || true
  workspace=$(az monitor log-analytics workspace show \
    --resource-group "$RESOURCE_GROUP" \
    --name "${LOG_WORKSPACE:-log-dataagent-dev}" \
    --query customerId -o tsv 2>/dev/null || true)
  if [ -z "$workspace" ]; then
    echo "No Log Analytics workspace found; the reason is not recoverable here." >&2
    return 0
  fi

  az monitor log-analytics query \
    --workspace "$workspace" \
    --analytics-query "ContainerAppConsoleLogs | where ContainerName == 'migrate' | order by TimeGenerated asc | project TimeGenerated, Log | take 200" \
    -o tsv >&2 2>/dev/null ||
    echo "The query failed; the az extension 'log-analytics' may not be installed." >&2

  echo >&2
  echo "If the output above is empty the logs have not arrived yet rather than" >&2
  echo "not existing: console logs can take ~15 minutes to reach Log Analytics." >&2
  echo "Re-run that query then, before changing anything." >&2
}

echo "Starting $JOB_NAME on ${REGISTRY}/dataagent-api:${IMAGE_TAG}"

# Point the job at the image this deploy built, then start it. Updating first
# rather than passing an override: a job execution inherits the job's template,
# so an override that failed to apply would silently migrate with the previous
# image — which is exactly the shape of defect that makes a deploy look fine.
az containerapp job update \
  --name "$JOB_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --image "${REGISTRY}/dataagent-api:${IMAGE_TAG}" \
  --output none

EXECUTION=$(az containerapp job start \
  --name "$JOB_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query name -o tsv)

echo "Execution $EXECUTION started; waiting for it to finish."

# **Wait, and fail on anything that is not Succeeded.** A job that is still
# Running when this script exits would let the revision swap overtake it, and a
# job that Failed must stop the deploy rather than be reported as started.
i=0
while [ "$i" -lt 60 ]; do
  STATUS=$(az containerapp job execution show \
    --name "$JOB_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --job-execution-name "$EXECUTION" \
    --query properties.status -o tsv 2>/dev/null || echo Unknown)
  case "$STATUS" in
  Succeeded)
    echo "Migration succeeded."
    exit 0
    ;;
  Failed | Cancelled)
    echo "Migration $STATUS. The deploy stops here — the app is still on the" >&2
    echo "previous revision, which matches the previous schema." >&2
    dump_logs "$EXECUTION"
    exit 1
    ;;
  *)
    i=$((i + 1))
    sleep 10
    ;;
  esac
done

echo "Migration did not finish within ten minutes; refusing to swap the" >&2
echo "revision. Its last status was '$STATUS'." >&2
exit 1
