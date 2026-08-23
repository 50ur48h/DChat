#!/usr/bin/env sh
#
# Put the three secrets the deployment needs into the vault (WP12.2).
#
# **This step did not exist and the first deploy could not have worked without
# it.** `infra/README.md` said Key Vault "is created empty and its secrets are
# written out of band" — and nothing wrote them, out of band or otherwise. An app
# revision that cannot resolve a `keyVaultUrl` secret reference does not start,
# and the vault only exists *after* the deployment that also creates the apps, so
# there was no earlier moment to do it by hand either. Hence the deploy's first
# pass creates infrastructure with `deployApps=false`, this runs, and the apps
# come second.
#
# **Idempotent, and it never overwrites.** A secret that already has a value is
# left exactly as it is. That is what makes rotation work: change the value in
# the vault, redeploy, and this step keeps its hands off. It also means a
# generated password is generated *once* — regenerating `app-database-password`
# on every deploy would lock the API out of its own database until the migration
# job caught up, which is a self-inflicted outage on a schedule.
#
# **What is seeded, and what is deliberately not.**
#
#   * `openai-api-key`  — from the repository secret. Already the owner's, already
#     in GitHub, and hand-seeding it would be a manual step repeated on every
#     rebuild, which is how a boundary quietly erodes.
#   * `database-password` — the Postgres admin password, the same value the Bicep
#     deployed the server with. Two copies of one secret is not ideal; the
#     alternative is a migration job that cannot log in.
#   * `app-database-password` — generated here, never chosen and never printed.
#     The migration job reads it and gives `dataagent_app` its login
#     (`dataagent.db.grant_app_login`); the API reads the same secret to connect.
#
# **No customer database credential is seeded, and none ever should be by a
# pipeline.** Those arrive through the product, encrypted, when a person
# registers a data source — that is the whole point of `SecretsProvider`.
#
# POSIX sh, for the reason the other ops scripts are: see db_setup.sh.

set -eu

: "${KEY_VAULT:?KEY_VAULT is not set; it comes from the infra deployment outputs}"

# Set a secret only if it has no value yet. `az keyvault secret show` fails for a
# secret that has never existed, which is the signal — and `|| true` keeps that
# expected failure from ending the script under `set -e`.
seed_if_absent() {
  name=$1
  value=$2
  why=$3
  existing=$(az keyvault secret show --vault-name "$KEY_VAULT" --name "$name" \
    --query id -o tsv 2>/dev/null || true)
  if [ -n "$existing" ]; then
    echo "  $name: already set, left alone"
    return 0
  fi
  if [ -z "$value" ]; then
    echo "  $name: MISSING — $why" >&2
    return 1
  fi
  # `--value` is passed as an argument, so it would appear in a process listing
  # on this runner. Acceptable: the runner is ephemeral and single-tenant, and
  # the alternative (`--file`) writes the secret to its disk instead. Neither
  # reaches the log, which is what matters — `--output none` and no echo of the
  # value anywhere in this file.
  az keyvault secret set --vault-name "$KEY_VAULT" --name "$name" \
    --value "$value" --output none
  echo "  $name: written"
}

echo "Seeding $KEY_VAULT (values are never printed)"

failures=0

seed_if_absent openai-api-key "${OPENAI_API_KEY:-}" \
  "set the OPENAI_API_KEY repository secret; the agent cannot answer without a model" ||
  failures=$((failures + 1))

seed_if_absent database-password "${POSTGRES_ADMIN_PASSWORD:-}" \
  "the dev environment secret POSTGRES_ADMIN_PASSWORD is what the server was created with" ||
  failures=$((failures + 1))

# Generated, never chosen, and only on the first deploy. 32 bytes of urandom
# rendered as hex: no shell-special characters to escape through a DSN, and
# `_with_password` percent-encodes it anyway.
if ! az keyvault secret show --vault-name "$KEY_VAULT" --name app-database-password \
  --query id -o tsv >/dev/null 2>&1; then
  generated=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
  [ -n "$generated" ] || {
    echo "  app-database-password: could not generate a password" >&2
    exit 1
  }
  seed_if_absent app-database-password "$generated" "generated here" ||
    failures=$((failures + 1))
  unset generated
else
  echo "  app-database-password: already set, left alone"
fi

if [ "$failures" -gt 0 ]; then
  echo "$failures secret(s) could not be seeded; the apps would not start." >&2
  exit 1
fi

echo "Vault seeded. Names only:"
az keyvault secret list --vault-name "$KEY_VAULT" --query "[].name" -o tsv | sed 's/^/  /'
