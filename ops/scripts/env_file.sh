#!/usr/bin/env sh
#
# Read one value out of .env, without sourcing it (**B-102**).
#
# Sourced by other scripts; not executable on its own.
#
# `set -a; . ./.env; set +a` is the obvious way to do this and it is wrong. A
# shell assignment strips quotes, so `LLM_ROLE_MAP={"compose":"small"}` enters
# the environment as `{compose:small}` — and environment beats dotenv in
# pydantic's settings order, so the mangled value wins over the correct one
# still sitting in the file. That is what made `make db.setup`, step 2 of the
# documented quickstart, die inside `alembic upgrade head` on any machine whose
# .env came from .env.example, which ships three JSON-valued keys uncommented.
#
# Reading only the keys a script actually uses fixes the class rather than the
# instance: no future JSON-valued key can break a command merely by being
# present. Anything downstream that needs the whole file reads it itself, where
# a real parser handles it.
#
# Extracted here because a second hand-rolled copy is how the two would drift,
# and the copy that drifted would be the one nobody was running.

# Last assignment wins, as a shell would. The value is taken verbatim except for
# one pair of matching surrounding quotes, which somebody may reasonably have
# written around a password containing spaces.
env_value() {
  value=$(sed -n "s/^$1=//p" "${ENV_FILE:-.env}" | tail -n 1)
  case $value in
  \"*\") value=${value#\"}; value=${value%\"} ;;
  \'*\') value=${value#\'}; value=${value%\'} ;;
  esac
  printf '%s' "$value"
}
