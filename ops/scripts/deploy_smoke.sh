#!/usr/bin/env sh
#
# Post-deploy smoke against dev (plan §4.4, WP12.2).
#
# **What this can prove and what it deliberately does not claim.** Plan §4.4
# asks for "health, authd request, one real single-shot run against seed source".
# The first two are here. **The third is not, and cannot be**: there is no seed
# database in Azure — `seed-pizza-pg` is a compose service — so there is nothing
# for a deployed run to be asked *about*. Registering a customer database against
# dev is a person's decision with a real credential, not a pipeline's.
#
# Saying so here rather than quietly shipping two of three checks is the point.
# WP11.2b's own note is the precedent: *the CI smoke proves the stack wires up
# end to end, not that the agent answered.* This proves less than that, and the
# gate that needs more is WP12.4's.
#
# What it does prove:
#
#   1. The web app answers on its public hostname.
#   2. The API answers /healthz **from inside the environment**, and reports the
#      git sha this deploy pushed — so a green smoke against a stale revision is
#      not possible.
#   3. An unauthenticated call to a tenant route is refused. A deployment whose
#      auth was misconfigured would serve it, and that is worth one curl.
#   4. Key Vault holds what the deployment expects, **by name only**. No value is
#      read, and this script has no permission to read one.

set -eu

: "${WEB_URL:?WEB_URL is not set}"
: "${KEY_VAULT:?KEY_VAULT is not set}"
: "${RESOURCE_GROUP:?RESOURCE_GROUP is not set}"

API_APP="${API_APP_NAME:-ca-dataagent-api-dev}"
failures=0

fail() {
  echo "smoke: $1" >&2
  failures=$((failures + 1))
}

echo "1. The web app answers on $WEB_URL"
# Retried: a revision that has just been created may still be starting, and a
# cold scale-to-zero app takes a moment. Ten tries at six seconds is a minute,
# which is longer than a cold start and shorter than a stuck deploy.
i=0
code=000
while [ "$i" -lt 10 ]; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$WEB_URL" || echo 000)
  case "$code" in
  2* | 3*) break ;;
  esac
  i=$((i + 1))
  sleep 6
done
[ "$i" -lt 10 ] || fail "the web app returned $code after a minute of trying"

echo "2. The API answers /healthz inside the environment"
# `exec` in the container rather than a request from here: the API's ingress is
# internal, which is the arrangement being verified as much as the health.
HEALTH=$(az containerapp exec \
  --name "$API_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --command "sh -c 'curl -sf http://localhost:8000/healthz'" 2>/dev/null || echo '')
case "$HEALTH" in
*'"status":"ok"'*) echo "   $HEALTH" ;;
*) fail "the API did not report healthy from inside the environment: ${HEALTH:-no response}" ;;
esac

if [ -n "${EXPECT_GIT_SHA:-}" ]; then
  case "$HEALTH" in
  *"$EXPECT_GIT_SHA"*) : ;;
  *) fail "healthz does not name the sha this deploy pushed — a stale revision is serving" ;;
  esac
fi

echo "3. An unauthenticated tenant request is refused"
UNAUTH=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$WEB_URL/api/v1/me" || echo 000)
case "$UNAUTH" in
401 | 403 | 404) echo "   refused with $UNAUTH" ;;
2*) fail "an unauthenticated call to a tenant route returned $UNAUTH — auth is not on" ;;
*) echo "   inconclusive ($UNAUTH); the web app does not proxy that path" ;;
esac

echo "4. Key Vault holds the expected secrets, by name"
NAMES=$(az keyvault secret list --vault-name "$KEY_VAULT" --query "[].name" -o tsv 2>/dev/null || echo '')
[ -n "$NAMES" ] || fail "could not list secrets in $KEY_VAULT"
echo "$NAMES" | sed 's/^/   /'

if [ "$failures" -gt 0 ]; then
  echo "smoke: $failures check(s) failed." >&2
  exit 1
fi
echo "smoke: dev is serving, healthy, refusing anonymous callers, and its vault is readable by name."
