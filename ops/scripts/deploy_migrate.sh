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
    az containerapp job logs show \
      --name "$JOB_NAME" \
      --resource-group "$RESOURCE_GROUP" \
      --execution "$EXECUTION" \
      --tail 100 >&2 || true
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
