#!/usr/bin/env sh
#
# Compile every Bicep module and both parameter files.
#
#     make build.infra
#
# The same commands CI runs, for the reason the lint recipes give at length: what
# a developer runs must be what CI runs, with nothing between them that could
# differ.
#
# **Both parameter files, including the one nothing deploys.** prod is deferred
# (D-041), and its file exists so that standing prod up later is a parameter file
# rather than a rewrite — which is only true while it still compiles. A parameter
# added to main.bicep and not to prod fails here rather than on the day somebody
# needs it.

set -eu

# Obvious dummies, and deliberately not plausible ones. Both parameter files read
# these from the environment rather than carrying them, so a compile has to
# supply something; a value that looked real would be one somebody eventually
# deployed.
POSTGRES_ADMIN_PASSWORD=${POSTGRES_ADMIN_PASSWORD:-lint-only-not-a-password}
BUDGET_ALERT_EMAIL=${BUDGET_ALERT_EMAIL:-lint@example.invalid}
export POSTGRES_ADMIN_PASSWORD BUDGET_ALERT_EMAIL

echo "Compiling infra/main.bicep..."
az bicep build --file infra/main.bicep --stdout >/dev/null

for params in infra/params/*.bicepparam; do
  echo "Compiling $params..."
  az bicep build-params --file "$params" --stdout >/dev/null
done

echo "infra: every module and parameter file compiles."
