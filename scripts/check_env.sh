#!/usr/bin/env bash
#
# The environment a developer has is not the environment the product runs in.
#
# B-086 is the instance that cost a phase. `ops/docker-compose.yml` passed every
# `LLM_*` key to the API under a comment explaining that runs happen *inside the
# API process* -- and then did not pass `EMBEDDINGS_*`. So `get_embedder()`
# returned None in the container while the identical question answered from the
# host had a working embedder, and every card and document in the browser was
# lexically searchable only. No test could see it: the tests run on the host,
# where the variable was there.
#
# B-090 is the class. Nothing compared the two, so the next variable added goes
# missing the same way -- silently, and only where the product actually runs.
#
# A plain diff cannot be the guard, because the legitimate absences outnumber the
# defects: `DATABASE_URL` is rebuilt for the compose network, `SEED_*_HOST` is
# `localhost` for tools run on the host, the `MSSQL_*` seed block belongs to a
# script. So the guard is a **declaration**, in the idiom `TENANT_TABLES` uses:
# every key deliberately not passed is named below with the reason. Adding a
# variable then costs one deliberate line -- pass it, or say why not -- and
# forgetting costs a red build instead of a silently degraded product.
#
# Five questions:
#
#   1. Is every key `.env.example` documents either passed to a service or
#      declared HOST_ONLY? This is B-086's question.
#   2. Does every HOST_ONLY declaration still name a key that exists? A stale
#      declaration is a claim about a file that has moved on.
#   3. Is any HOST_ONLY key passed after all? Then the declaration is a lie in
#      the more dangerous direction: it says nobody meant this to reach the
#      container, and something does.
#   4. Does compose reference anything `.env.example` never documents? That is
#      the same defect facing the other way -- a variable nobody setting the
#      project up can discover.
#   5. Does every `Settings` field have a `.env.example` line? `config.py` is
#      where the API decides what is configurable, and a field nobody documents
#      is a knob with no label.
#
# Usage:
#   bash scripts/check_env.sh              # the repo's own files
#   bash scripts/check_env.sh --selftest   # prove the guard still fails when it should
#
# Overrides, all used by --selftest and none needed in normal use:
#   ENV_EXAMPLE_FILE   default .env.example
#   COMPOSE_FILE       default ops/docker-compose.yml
#   SETTINGS_FILE      default apps/api/src/dataagent/config.py

set -euo pipefail

ENV_EXAMPLE_FILE=${ENV_EXAMPLE_FILE:-.env.example}
COMPOSE_FILE=${COMPOSE_FILE:-ops/docker-compose.yml}
SETTINGS_FILE=${SETTINGS_FILE:-apps/api/src/dataagent/config.py}

# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------
#
# `KEY | why it is not passed to any service`. A reason is mandatory: the point
# of this file is that the next person reads why rather than guessing, and
# "somebody decided" is what the guard exists to replace.
#
# Two rules for deciding which side a new key belongs on:
#
#   * If the API, the web app or a database container reads it, **pass it**.
#     Everything the product does at runtime happens inside a container.
#   * If it is for a tool run on the host -- a seed script, a migration, psql --
#     or if compose necessarily rebuilds it, declare it here and say so.
#
readonly HOST_ONLY='
DATABASE_URL | The owner connection, rebuilt inside compose: the database is `platform-pg` on the compose network, not `localhost:5432`. Used on the host by alembic and psql.
APP_DATABASE_URL | The same, for the unprivileged application role the API actually connects as.
ENV | Compose sets `ENV: local` literally, because a stack of containers on somebody laptop *is* local. Passing the host value would let a stray `ENV=prod` in a .env disable dev-only features in a local stack.
ARTIFACTS_PATH | The container writes to `/app/ops/ops/artifacts` only because a bind mount puts the host directory there. Passing a host path would move the setting and not the mount, and query results would land somewhere nothing is watching.
LOCAL_SECRETS_PATH | Same shape: compose points the API at `/app/ops/.secrets/secrets.json`, which is the host file through a mount. The host value is a host path.
SEED_PIZZA_HOST | `localhost` for tools run on the host. Inside the network the database is `seed-pizza-pg`, which the seed service already knows.
SEED_FNB_HOST | The same for the F&B seed database.
SEED_PIZZA_READONLY_USER | The read-only login `make seed` creates and a person types into the data-source form. No container reads it; the API receives it through the API, encrypted, like any customer credential.
SEED_PIZZA_READONLY_PASSWORD | As above.
SEED_FNB_READONLY_USER | As above, for the F&B seed.
SEED_FNB_READONLY_PASSWORD | As above.
MSSQL_DB | Read by `ops/scripts/seed_mssql.sh`, which runs on the host against the on-demand SQL Server container.
MSSQL_PIZZA_READONLY_USER | Created by that seed script and typed into the data-source form, exactly like the Postgres read-only login.
MSSQL_PIZZA_READONLY_PASSWORD | As above.
'

# Keys the image or the build sets, which a person never puts in a `.env` and
# which therefore have no `.env.example` line. Declared so that check 4 and
# check 5 do not each need a special case -- and so that adding a third one is a
# visible decision.
readonly SET_BY_THE_BUILD='
GIT_SHA | Baked in at `docker build` time by the Makefile so `/healthz` can say which commit is running. A value in a .env would be a claim about an image it did not build.
BUILD_ENV | Set by the Dockerfile target that built the process, and the reason the prod image can physically exclude the dev token issuer. A person setting it would be asserting something about an image rather than configuring one.
'

failures=0

fail() {
  printf 'check_env: %s\n' "$1" >&2
  failures=$((failures + 1))
}

# --- Reading the three files ------------------------------------------------

# Every key `.env.example` documents, commented ones included. `# OPENAI_API_KEY=`
# is the file's own idiom for "optional, uncomment to set" -- it is documentation,
# and treating it as absent would report the whole optional half as undocumented.
# `|| true` on every grep here and below: grep exits 1 when it matches nothing,
# and these are all read as `keys=$(...)`, where a non-zero status under `set -e`
# would end the run silently -- the one failure mode a guard must not have.
documented_keys() {
  sed -E 's/\r$//; s/^[[:space:]]*#[[:space:]]*//' "$ENV_EXAMPLE_FILE" |
    { grep -oE '^[A-Z_][A-Z0-9_]*=' || true; } | tr -d '=' | sort -u
}

# Every `${KEY}` compose expands, with comment lines stripped first -- a comment
# explaining `${VAR:?}` is prose about the syntax, not a variable anyone passes.
compose_keys() {
  sed -E 's/\r$//; s/^[[:space:]]*#.*$//' "$COMPOSE_FILE" |
    { grep -oE '\$\{[A-Z_][A-Z0-9_]*' || true; } | sed 's/^\${//' | sort -u
}

# Every field of `Settings`, as the environment variable name pydantic reads it
# for. The class ends at the next top-level `class`, `def` or decorator; no field
# uses an alias, and one that did would need this to learn about aliases -- which
# is why check 5's message says where to look rather than only what is missing.
settings_keys() {
  awk '
    /^class Settings\(BaseSettings\):/ { inside = 1; next }
    inside && /^(class |def |@)/ { inside = 0 }
    inside && /^    [a-z_][a-z0-9_]*[[:space:]]*:/ {
      sub(/\r$/, "")
      name = $1
      sub(/:.*/, "", name)
      if (name != "model_config") print toupper(name)
    }
  ' "$SETTINGS_FILE" | sort -u
}

# `KEY | reason` lines out of a declaration block. Reasons may contain anything
# but a newline, so the split is on the first pipe only.
declared_keys() {
  printf '%s\n' "$1" | { grep -E '^[A-Z_][A-Z0-9_]*[[:space:]]*\|' || true; } |
    sed -E 's/^([A-Z_][A-Z0-9_]*)[[:space:]]*\|.*$/\1/' | sort -u
}

declared_reason() {
  printf '%s\n' "$2" | { grep -E "^$1[[:space:]]*\|" || true; } |
    sed -E 's/^[A-Z_][A-Z0-9_]*[[:space:]]*\|[[:space:]]*//'
}

contains() {
  printf '%s\n' "$2" | grep -qxF "$1"
}

# --- 1. A documented key is passed, or it is declared -----------------------

check_every_documented_key_reaches_a_service() {
  local documented=$1 compose=$2 host_only=$3 build=$4
  local key
  while read -r key; do
    [[ -n $key ]] || continue
    contains "$key" "$compose" && continue
    contains "$key" "$host_only" && continue
    contains "$key" "$build" && continue
    fail "$key is documented in $ENV_EXAMPLE_FILE and passed to no service in $COMPOSE_FILE.
    Either add it to the service that reads it, or declare it in HOST_ONLY with
    the reason it stays on the host. This is B-086: EMBEDDINGS_* was set on every
    developer's machine and reached nothing in the container, for a whole phase."
  done <<<"$documented"
}

# --- 2. A declaration names a key that exists -------------------------------

check_declarations_are_not_stale() {
  local documented=$1 host_only=$2
  local key
  while read -r key; do
    [[ -n $key ]] || continue
    contains "$key" "$documented" ||
      fail "$key is declared HOST_ONLY but $ENV_EXAMPLE_FILE does not document it.
    Either the key was removed and the declaration should go with it, or it was
    renamed and the declaration now excuses a key nobody has."
  done <<<"$host_only"
}

# --- 3. A declaration is not contradicted by the file it describes ----------

check_declarations_are_not_contradicted() {
  local compose=$1 host_only=$2
  local key
  while read -r key; do
    [[ -n $key ]] || continue
    if contains "$key" "$compose"; then
      fail "$key is declared HOST_ONLY and $COMPOSE_FILE passes it anyway.
    The declaration says nobody meant this to reach a container. Delete the
    declaration if passing it was right; remove it from compose if it was not."
    fi
  done <<<"$host_only"
}

# --- 4. Nothing is passed that nobody documented ----------------------------

check_every_passed_key_is_documented() {
  local documented=$1 compose=$2 build=$3
  local key
  while read -r key; do
    [[ -n $key ]] || continue
    contains "$key" "$documented" && continue
    contains "$key" "$build" && continue
    fail "$COMPOSE_FILE expands \${$key} and $ENV_EXAMPLE_FILE never mentions it.
    Somebody setting this project up from the example file cannot discover it,
    and will get whatever the compose default happens to be."
  done <<<"$compose"
}

# --- 5. Every setting the API reads has a line somebody can find ------------

check_every_setting_is_documented() {
  local documented=$1 settings=$2 build=$3
  local key
  while read -r key; do
    [[ -n $key ]] || continue
    contains "$key" "$documented" && continue
    contains "$key" "$build" && continue
    fail "Settings reads $key and $ENV_EXAMPLE_FILE does not document it.
    A configurable knob with no label is undiscoverable; add a line (commented
    out is fine -- that is this file's idiom for optional), or, if the value is
    set by the image rather than by a person, declare it in SET_BY_THE_BUILD.
    If the field carries an explicit alias, this guard reads the field name and
    needs teaching -- see settings_keys()."
  done <<<"$settings"
}

# --- 6. Every declaration says why ------------------------------------------

check_every_declaration_has_a_reason() {
  local block=$1 name=$2 key reason
  while read -r key; do
    [[ -n $key ]] || continue
    reason=$(declared_reason "$key" "$block")
    [[ -n ${reason// /} ]] ||
      fail "$key is declared $name with no reason. The reason is the whole point:
    the next person has to be able to read why rather than guess."
  done < <(declared_keys "$block")
}

run_checks() {
  local host_only_block=$1 build_block=$2
  local documented compose settings host_only build
  documented=$(documented_keys)
  compose=$(compose_keys)
  settings=$(settings_keys)
  host_only=$(declared_keys "$host_only_block")
  build=$(declared_keys "$build_block")

  [[ -n $documented ]] || fail "$ENV_EXAMPLE_FILE documents no keys at all — is the path right?"
  [[ -n $compose ]] || fail "$COMPOSE_FILE expands no variables at all — is the path right?"

  check_every_documented_key_reaches_a_service "$documented" "$compose" "$host_only" "$build"
  check_declarations_are_not_stale "$documented" "$host_only"
  check_declarations_are_not_contradicted "$compose" "$host_only"
  check_every_passed_key_is_documented "$documented" "$compose" "$build"
  check_every_setting_is_documented "$documented" "$settings" "$build"
  check_every_declaration_has_a_reason "$host_only_block" HOST_ONLY
  check_every_declaration_has_a_reason "$build_block" SET_BY_THE_BUILD
}

# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------
#
# Run before the check it tests, like `check_status.sh --selftest` and
# `check_backlog.sh --selftest`: a guard that has stopped catching anything must
# fail loudly rather than pass everything. Each case builds the exact mistake a
# future change might make and requires this script to notice.

SELFTEST_WORKSPACE=''
cleanup_workspace() { [[ -n $SELFTEST_WORKSPACE ]] && rm -rf "$SELFTEST_WORKSPACE"; return 0; }

selftest() {
  local passed=0 failed=0 workspace
  SELFTEST_WORKSPACE=$(mktemp -d)
  workspace=$SELFTEST_WORKSPACE
  trap cleanup_workspace EXIT

  expect() {
    local want=$1 name=$2
    shift 2
    local got=0
    ( "$@" ) >/dev/null 2>&1 || got=1
    if [[ $got == "$want" ]]; then
      printf '  ok    %s\n' "$name"
      passed=$((passed + 1))
    else
      printf '  FAIL  %s (expected exit %s, got %s)\n' "$name" "$want" "$got"
      failed=$((failed + 1))
    fi
  }

  # A minimal repo that passes: one key passed to a service, one declared.
  write_fixture() {
    local dir=$1
    mkdir -p "$dir"
    cat >"$dir/.env.example" <<'FIXTURE'
PASSED_KEY=value
# OPTIONAL_KEY=
HOST_KEY=value
FIXTURE
    cat >"$dir/compose.yml" <<'FIXTURE'
services:
  api:
    environment:
      PASSED_KEY: ${PASSED_KEY:-}
      OPTIONAL_KEY: ${OPTIONAL_KEY:-}
      # A comment about ${COMMENTED_KEY:?} is prose, not a variable.
FIXTURE
    cat >"$dir/config.py" <<'FIXTURE'
class Settings(BaseSettings):
    model_config = SettingsConfigDict()
    passed_key: str = "x"
    optional_key: str | None = None


def other() -> None:
    pass
FIXTURE
  }

  # Runs this script against a fixture, with HOST_KEY declared unless told not to.
  guard() {
    local dir=$1 declaration=${2-'HOST_KEY | A host tool reads it.'}
    ENV_EXAMPLE_FILE="$dir/.env.example" \
      COMPOSE_FILE="$dir/compose.yml" \
      SETTINGS_FILE="$dir/config.py" \
      SELFTEST_HOST_ONLY="$declaration" \
      bash "${BASH_SOURCE[0]}" --with-declaration
  }

  local dir="$workspace/clean"
  write_fixture "$dir"
  expect 0 "a repo where every key is passed or declared passes" guard "$dir"

  dir="$workspace/undeclared"
  write_fixture "$dir"
  printf 'FORGOTTEN_KEY=value\n' >>"$dir/.env.example"
  expect 1 "a documented key passed to nothing and declared nowhere fails" guard "$dir"

  dir="$workspace/stale"
  write_fixture "$dir"
  expect 1 "a declaration naming a key that no longer exists fails" \
    guard "$dir" 'HOST_KEY | A host tool reads it.
GONE_KEY | Removed two phases ago.'

  dir="$workspace/contradicted"
  write_fixture "$dir"
  expect 1 "a declared key that compose passes anyway fails" \
    guard "$dir" 'HOST_KEY | A host tool reads it.
PASSED_KEY | Claimed host-only while compose passes it.'

  dir="$workspace/undocumented"
  write_fixture "$dir"
  printf '      MYSTERY_KEY: ${MYSTERY_KEY:-}\n' >>"$dir/compose.yml"
  expect 1 "a compose variable nobody documented fails" guard "$dir"

  dir="$workspace/unlabelled"
  write_fixture "$dir"
  # Written whole rather than appended to: a field after the fixture's
  # trailing `def` would be outside `Settings`, the guard would be right to
  # ignore it, and this case would pass while proving nothing.
  {
    echo 'class Settings(BaseSettings):'
    echo '    passed_key: str = "x"'
    echo '    optional_key: str | None = None'
    echo '    undocumented_field: str = "x"'
  } >"$dir/config.py"
  expect 1 "a Settings field with no .env.example line fails" guard "$dir"

  dir="$workspace/reasonless"
  write_fixture "$dir"
  expect 1 "a declaration with no reason fails" guard "$dir" 'HOST_KEY |'

  # The parser's own two traps, both of which would make the guard pass
  # everything: a commented example line read as absent, and a compose comment
  # read as a reference.
  dir="$workspace/comments"
  write_fixture "$dir"
  expect 0 "a commented example line counts as documented" guard "$dir"

  dir="$workspace/prose"
  write_fixture "$dir"
  printf '      # ${PROSE_KEY:-} appears only in a comment\n' >>"$dir/compose.yml"
  expect 0 "a variable named only inside a compose comment is not a reference" guard "$dir"

  printf 'check_env --selftest: %d passed, %d failed\n' "$passed" "$failed"
  ((failed == 0))
}

case "${1-}" in
--selftest)
  selftest
  ;;
--with-declaration)
  # Selftest only: run the checks against the declaration the fixture supplied
  # rather than this file's own. Kept behind a flag so the real invocation can
  # never be talked out of the declaration it ships with.
  run_checks "${SELFTEST_HOST_ONLY-}" ''
  ((failures == 0)) || exit 1
  ;;
"")
  run_checks "$HOST_ONLY" "$SET_BY_THE_BUILD"
  if ((failures == 0)); then
    printf 'check_env: %s and %s agree\n' "$ENV_EXAMPLE_FILE" "$COMPOSE_FILE"
  else
    exit 1
  fi
  ;;
*)
  printf 'usage: %s [--selftest]\n' "$0" >&2
  exit 2
  ;;
esac
