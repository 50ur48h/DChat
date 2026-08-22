#!/usr/bin/env bash
#
# No secret value may be written into a deployment template.
#
# A template is the one place in this repo where a leaked credential is both easy
# to write and invisible in review: it looks like configuration, it sits beside a
# hundred other assignments, and it reads as *correct* — the resource does need a
# password. Worse, a value here is copied into the resource group's deployment
# history, where it is readable by anyone with reader access and survives the
# secret being rotated.
#
# So `infra/` carries no secret values. Key Vault is created empty and filled out
# of band; Container Apps reference secrets by URI and read them with a managed
# identity; the two parameters that would otherwise carry one — the Postgres
# administrator password and the budget alert address — are read from the
# environment at deploy time.
#
# **What this looks for is a quoted literal**, which is the thing that cannot be
# right. `readEnvironmentVariable('POSTGRES_ADMIN_PASSWORD')` is the correct
# pattern and must pass; `= 'hunter2'` must not. The first version of this check
# lived inline in the workflow, where YAML escaping mangled the pattern into one
# that matched any assignment at all — it failed the build on the correct code
# and would have been "fixed" by loosening it. It is a file now, with a selftest,
# like every other guard in this directory.
#
# Usage:
#   bash scripts/check_infra_secrets.sh            # check infra/
#   bash scripts/check_infra_secrets.sh --selftest # prove it still catches one

set -euo pipefail

INFRA_DIR=${INFRA_DIR:-infra}

# A quoted literal assigned to something whose name says secret, or a quoted
# email address. Anchored on `= '` so a function call — which is how a real value
# arrives — cannot match.
readonly SECRET_ASSIGNMENT="=[[:space:]]*'[^']*'"
readonly SECRET_NAMES='(password|passwd|secret|apikey|api_key|connectionstring|accountkey|sas|token)'

# Hostnames that are part of Azure's own namespace rather than somebody's inbox.
readonly ALLOWED='(example\.invalid|azurecr\.io|database\.azure\.com|blob\.core\.windows\.net|vaultcore\.azure\.net|aka\.ms)'

# **A GUID is not a secret, and one of them is unavoidable here.** Azure's role
# definition ids are GUIDs, and `roles.bicep` assigns them to variables named
# `keyVaultSecretsUser` and the like — a name containing "secret" holding a
# constant that is public, documented and identical in every tenant. Excluding
# the *shape* is exact; excluding the file or relaxing the name pattern would
# have been the loosening this guard's own header warns about.
readonly GUID="'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'\$"


scan() {
  local dir=$1 found=0

  # A quoted value assigned to a secret-shaped name, excluding bare GUIDs.
  if grep -rniE "${SECRET_NAMES}[a-z_]*[[:space:]]*${SECRET_ASSIGNMENT}" "$dir" \
    --include='*.bicep' --include='*.bicepparam' 2>/dev/null |
    grep -viE "$GUID"; then
    found=1
  fi

  # A quoted email address, which is only ever a person.
  if grep -rniE "'[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}'" "$dir" \
    --include='*.bicep' --include='*.bicepparam' 2>/dev/null |
    grep -viE "$ALLOWED"; then
    found=1
  fi

  return $found
}

selftest() {
  local passed=0 failed=0
  # **Global, not local.** The EXIT trap runs after this function has returned,
  # and under `set -u` a local would be unbound by then — which is exactly the
  # note `check_status.sh` already carries about its own selftest. Written wrong
  # here first, and caught by the selftest passing five cases and then exiting 1.
  dir=$(mktemp -d)
  trap 'rm -rf "$dir"' EXIT

  # The correct patterns, which must pass.
  cat >"$dir/good.bicepparam" <<'GOOD'
using '../main.bicep'
param postgresAdminPassword = readEnvironmentVariable('POSTGRES_ADMIN_PASSWORD')
param budgetAlertEmail = readEnvironmentVariable('BUDGET_ALERT_EMAIL')
param postgresAdminLogin = 'dataagent_admin'
param registryHost = 'crdataagentdev01.azurecr.io'
GOOD

  # A role definition id: a name containing "secret", holding a public constant.
  cat >"$dir/roles.bicep" <<'ROLES'
var keyVaultSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'
ROLES

  if INFRA_DIR=$dir scan "$dir" >/dev/null 2>&1; then
    printf '  ok    the correct patterns pass, role GUIDs included\n'
    passed=$((passed + 1))
  else
    printf '  FAIL  the correct patterns were flagged\n' >&2
    failed=$((failed + 1))
  fi

  # Each of these must be caught. A guard that cannot fail is decoration.
  #
  # **The values are deliberately low-entropy.** What this guard keys on is a
  # quoted literal assigned to a secret-shaped *name*, so the realism of the
  # value tests nothing — and a realistic one, which is what the first draft
  # used, is a string the repository's own secret scanner then flags: one guard
  # tripping another, and a fake credential in the history of a public
  # repository forever. The role GUID above cannot be dulled the same
  # way, because it has to be the real constant; that one is allowlisted in
  # `.gitleaks.toml` by value, with a comment saying what it is.
  local case
  for case in \
    "param adminPassword = 'hunter2'" \
    "param apiKey = 'placeholder-not-a-real-key'" \
    "var connectionString = 'AccountKey=abc123'" \
    "param alertEmail = 'someone@gmail.com'"; do
    printf '%s\n' "$case" >"$dir/bad.bicep"
    if INFRA_DIR=$dir scan "$dir" >/dev/null 2>&1; then
      printf '  FAIL  not caught: %s\n' "$case" >&2
      failed=$((failed + 1))
    else
      printf '  ok    caught: %s\n' "$case"
      passed=$((passed + 1))
    fi
    rm -f "$dir/bad.bicep"
  done

  printf 'check_infra_secrets --selftest: %d passed, %d failed\n' "$passed" "$failed"
  ((failed == 0))
}

if [[ ${1:-} == --selftest ]]; then
  selftest
  exit $?
fi

if [[ ! -d $INFRA_DIR ]]; then
  printf 'check_infra_secrets: no %s directory; nothing to check\n' "$INFRA_DIR"
  exit 0
fi

if scan "$INFRA_DIR"; then
  printf 'check_infra_secrets: no secret value in %s\n' "$INFRA_DIR"
  exit 0
fi

cat >&2 <<'WHY'

A quoted literal in infra/ looks like a credential or a personal address.

Secrets are not written into templates. Key Vault is created empty and filled out
of band; apps reference secrets by URI and read them with a managed identity; a
parameter that would carry one is read from the environment at deploy time:

    param postgresAdminPassword = readEnvironmentVariable('POSTGRES_ADMIN_PASSWORD')

See infra/README.md.
WHY
exit 1
