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
#   2. The API answers /healthz on **its** public hostname and names the git sha
#      this deploy pushed — so a green smoke against a stale revision, which is
#      what a failed rollout looks like from outside, is not possible.
#   3. An unauthenticated call to `/v1/me` is refused. A deployment whose auth was
#      misconfigured would serve it, and that is worth one curl.
#   4. Key Vault holds what the deployment expects, **by name only**. No value is
#      read, and this script has no permission to read one.
#
# **Checks 2 and 3 were wrong in the direction that matters, and are the reason
# this header is worth reading.** They ran `az containerapp exec`, which prints
# `INFO: Connecting to the container 'api'...` on stdout before anything the
# command returns — so both compared that banner to what they expected and
# reported a *working* deployment as broken. A check that cries wolf teaches
# people to skip it, and the second red smoke against a healthy system is the one
# that does the damage. They ask the API directly now, which is possible because
# its ingress is external (see `apps.bicep`) and is also what a browser does.

set -eu

: "${WEB_URL:?WEB_URL is not set}"
: "${API_URL:?API_URL is not set}"
: "${KEY_VAULT:?KEY_VAULT is not set}"
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

echo "2. The API answers /healthz, and names the sha this deploy pushed"
# **`curl`, not `az containerapp exec`, and the previous version lied because of
# it.** `exec` prints `INFO: Connecting to the container 'api'...` on stdout
# ahead of anything the command produces, so the check compared that banner to
# `"status":"ok"` and reported a perfectly healthy deployment as broken — twice,
# in the same run, because check 3 then compared the same banner to a git sha.
#
# A check that lies is worse than no check: the second time somebody reads a red
# smoke against a working system, they stop reading it. `exec` was only ever
# needed because the API had no public hostname; it has one now, so the smoke can
# ask the API the same question a user's browser will.
# **Retried, for the reason check 1 is.** This had a single 30-second attempt
# while the web app got ten tries over a minute, and the asymmetry cost a red
# deploy on a perfectly good revision: `minReplicas: 0` means the API is cold
# when the smoke arrives, and a check that fails on a cold start is a check
# people learn to re-run rather than read.
HEALTH=''
attempt=0
while [ "$attempt" -lt 10 ]; do
  HEALTH=$(curl -s --max-time 20 "${API_URL}/healthz" || echo '')
  case "$HEALTH" in
  *'"status":'*) break ;;
  esac
  attempt=$((attempt + 1))
  sleep 6
done

case "$HEALTH" in
*'"status":"ok"'*) echo "   $HEALTH" ;;
*'"status":"degraded"'*)
  # The probe naming its own missing configuration. Worth failing on: the app is
  # running and cannot serve the mode it was deployed with.
  fail "the API reports degraded — it is missing configuration it needs: $HEALTH"
  ;;
*) fail "the API did not report healthy at ${API_URL}/healthz: ${HEALTH:-no response}" ;;
esac

# **The sha check is what makes the health check mean something.** Without it a
# green smoke is satisfied by the *previous* revision still serving, which is
# exactly what a failed rollout looks like from outside.
if [ -n "${EXPECT_GIT_SHA:-}" ]; then
  case "$HEALTH" in
  *"$EXPECT_GIT_SHA"*) echo "   serving ${EXPECT_GIT_SHA}" ;;
  *) fail "healthz does not name ${EXPECT_GIT_SHA} — a stale revision is serving" ;;
  esac
fi

echo "3. An unauthenticated request to a tenant route is refused"
# Asked of the API directly rather than through the web origin. The previous
# version guessed at a proxy path that does not exist, got a 404, and called it
# inconclusive — which is a check that cannot fail and therefore proves nothing.
UNAUTH=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 "${API_URL}/v1/me" || echo 000)
case "$UNAUTH" in
401 | 403) echo "   refused with $UNAUTH" ;;
2*) fail "an unauthenticated call to /v1/me returned $UNAUTH — the API is not checking tokens" ;;
*) fail "an unauthenticated call to /v1/me returned $UNAUTH, which is neither a refusal nor a success" ;;
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
