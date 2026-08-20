#!/usr/bin/env sh
#
# The Phase 11 gate's smoke: a real stack, a real browser, a scripted model.
#
#     make test.web.smoke
#
# Brings up compose, migrates, seeds the pizza fixture, drives the product in
# Chromium (`apps/web/e2e-compose/`), and takes the stack down again.
#
# **Its own compose project, on its own ports.** Not politeness — three things
# would otherwise go wrong on a developer's machine, and two of them silently.
# A developer's stack holds demo fixtures the walks of WP11.2b depended on, and
# `down` on the shared project would take them with it. Their api container is
# configured with a real key, so a smoke that used it would spend their money and
# turn a model's variability into a red build. And the ports are already taken.
# A parallel project is the same method both README walks used (`-p
# dataagent-clean`), for the same reason.
#
# **The model is scripted, and this script is what guarantees that.** The
# variables below are exported, and an exported variable beats `--env-file` in
# compose's interpolation order, so a developer's `LLM_PROVIDERS=openai` cannot
# reach this stack. `OPENAI_API_KEY` and `EMBEDDINGS_PROVIDER` are blanked in the
# same breath: discovery embeds catalog cards when an embedder is configured, so
# a smoke that left that alone would quietly bill the owner for the schema it
# just read.
#
# POSIX sh, not bash: on Windows a bare `bash` is WSL's, which cannot reach the
# Docker CLI or this drive the same way. The Makefile runs this with $(SHELL).
#
# Knobs, all optional:
#   SMOKE_KEEP=1     leave the stack up afterwards (for debugging a red run)
#   SMOKE_REUSE=1    do not build or migrate or seed; the stack is already there
#   SMOKE_PROJECT    compose project name (default dataagent-smoke)
#   SMOKE_*_PORT     host ports, if the defaults below are taken too

set -eu

if [ ! -f .env ]; then
  echo "No .env found. Run 'make env' first." >&2
  exit 1
fi

. "$(dirname "$0")/env_file.sh"

PROJECT=${SMOKE_PROJECT:-dataagent-smoke}

# Deliberately not one off the defaults. 3100 is the hermetic Playwright suite's
# and 5433 is the first port somebody reaches for when 5432 is taken, so a smoke
# that used either would collide with exactly the thing a developer is running
# while they debug this.
SMOKE_WEB_PORT=${SMOKE_WEB_PORT:-3200}
SMOKE_API_PORT=${SMOKE_API_PORT:-8200}
SMOKE_PG_PORT=${SMOKE_PG_PORT:-5532}
SMOKE_PIZZA_PORT=${SMOKE_PIZZA_PORT:-6643}
SMOKE_FNB_PORT=${SMOKE_FNB_PORT:-6644}

PLATFORM_DB_USER=$(env_value PLATFORM_DB_USER)
PLATFORM_DB_NAME=$(env_value PLATFORM_DB_NAME)
PLATFORM_DB_PASSWORD=$(env_value PLATFORM_DB_PASSWORD)
APP_DB_PASSWORD=$(env_value APP_DB_PASSWORD)
SEED_PIZZA_READONLY_PASSWORD=$(env_value SEED_PIZZA_READONLY_PASSWORD)
LOCAL_SECRETS_KEY=$(env_value LOCAL_SECRETS_KEY)

: "${PLATFORM_DB_USER:?PLATFORM_DB_USER missing from .env}"
: "${PLATFORM_DB_NAME:?PLATFORM_DB_NAME missing from .env}"
: "${PLATFORM_DB_PASSWORD:?PLATFORM_DB_PASSWORD missing from .env}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD missing from .env}"
: "${SEED_PIZZA_READONLY_PASSWORD:?SEED_PIZZA_READONLY_PASSWORD missing from .env}"

# The walk registers a database, so the API must be able to encrypt a credential.
# Generated rather than demanded: this is the one required value a fresh .env
# does not carry, and stopping here would make the smoke fail for a reason that
# has nothing to do with the product.
if [ -z "$LOCAL_SECRETS_KEY" ]; then
  echo "No LOCAL_SECRETS_KEY in .env — minting one for this stack only."
  LOCAL_SECRETS_KEY=$(uv run --directory apps/api python -c \
    "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
fi

# ---------------------------------------------------------------------------
# What this stack is, overriding whatever .env says
# ---------------------------------------------------------------------------
export LOCAL_SECRETS_KEY
export SECRETS_BACKEND=local

#: A stub in the ordinary image, selected by environment and refused at boot in
#: production twice over (config.assert_llm_providers_are_production_safe, and
#: registry.get_provider). See DECISIONS D-040.
export LLM_PROVIDERS=scripted
export LLM_MODELS='{"scripted":{"small":"scripted-1","mid":"scripted-1","strong":"scripted-1"}}'
export LLM_ROLE_MAP='{}'
# One model, no prices, no cap. A per-run cost ceiling against an unpriced model
# refuses the run when LLM_REFUSE_UNPRICED_WHEN_CAPPED is on — which is the
# correct policy and would look here like the agent declining to answer.
export LLM_PRICES='{}'
export LLM_RUN_COST_LIMIT_USD=
export LLM_REFUSE_UNPRICED_WHEN_CAPPED=

# Nothing in this stack may reach a paid API, whatever is in .env.
export OPENAI_API_KEY=
export ANTHROPIC_API_KEY=
export EMBEDDINGS_PROVIDER=
export EMBEDDINGS_MODEL=

# Sign-in, pinned end to end. A developer's .env is quite likely to say `entra`
# — the owner's does — and the smoke has no identity provider to redirect to.
export AUTH_MODE=dev
export NEXT_PUBLIC_AUTH_MODE=dev
export OIDC_AUTHORITY=
export OIDC_ISSUER=
export OIDC_AUDIENCE=dataagent-api

# **`DEV_ISSUER_URL` is not a browser address, and this is the trap.** It is the
# `iss` the dev issuer stamps *and* where the API fetches its JWKS from, and the
# API does that fetch **from inside its own container** — so it must name the
# port uvicorn is listening on there, which is always 8000 however the host has
# published it. Pointed at the published port instead, every request comes back
# 401 `jwks_unavailable`: sign-in appears to work, the profile screen renders,
# and the first authenticated call fails for a reason that looks like a bad
# token. The browser never fetches this URL; it only ever calls
# `NEXT_PUBLIC_API_URL/dev/token`.
export DEV_ISSUER_URL="http://localhost:8000/dev"

# These two are the browser's view, and so do carry the published port.
export NEXT_PUBLIC_API_URL="http://localhost:${SMOKE_API_PORT}"
export CORS_ORIGINS="http://localhost:${SMOKE_WEB_PORT}"

export WEB_PORT=$SMOKE_WEB_PORT
export API_PORT=$SMOKE_API_PORT
export PLATFORM_DB_PORT=$SMOKE_PG_PORT
export SEED_PIZZA_PORT=$SMOKE_PIZZA_PORT
export SEED_FNB_PORT=$SMOKE_FNB_PORT

COMPOSE="docker compose -p $PROJECT --env-file .env -f ops/docker-compose.yml"

teardown() {
  if [ "${SMOKE_KEEP:-}" = "1" ]; then
    echo
    echo "Stack left up as $PROJECT: web http://localhost:${SMOKE_WEB_PORT}"
    echo "Take it down with: docker compose -p $PROJECT -f ops/docker-compose.yml down --volumes"
    return
  fi
  echo
  echo "Taking $PROJECT down..."
  # Volumes too: this database exists only for one walk, and leaving it behind
  # would make the next run answer from the last one's rows.
  $COMPOSE down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap teardown EXIT INT TERM

if [ "${SMOKE_REUSE:-}" != "1" ]; then
  echo "Starting $PROJECT (model: scripted)..."
  # `--wait` rather than a sleep: every service in the file has a healthcheck,
  # and web's is also what warms `next dev`'s first compile.
  $COMPOSE up -d --build --wait

  echo "Applying migrations..."
  DATABASE_URL="postgresql+asyncpg://${PLATFORM_DB_USER}:${PLATFORM_DB_PASSWORD}@localhost:${SMOKE_PG_PORT}/${PLATFORM_DB_NAME}" \
    uv run --directory apps/api alembic upgrade head

  echo "Granting dataagent_app its login..."
  $COMPOSE exec -T platform-pg \
    psql -v ON_ERROR_STOP=1 \
    -U "$PLATFORM_DB_USER" \
    -d "$PLATFORM_DB_NAME" \
    -v app_password="$APP_DB_PASSWORD" \
    <ops/sql/app_role.sql >/dev/null

  echo "Seeding the pizza fixture..."
  uv run ops/seed/seed_pizza.py
fi

echo
echo "Driving http://localhost:${SMOKE_WEB_PORT} in Chromium..."

# `seed-pizza-pg` is how the **API** reaches the database — a compose service
# name on the smoke project's own network. The browser never resolves it.
if SMOKE_BASE_URL="http://localhost:${SMOKE_WEB_PORT}" \
  SMOKE_DB_HOST=seed-pizza-pg \
  SMOKE_DB_PORT=5432 \
  SMOKE_DB_PASSWORD="$SEED_PIZZA_READONLY_PASSWORD" \
  pnpm --dir apps/web exec playwright test --config playwright.compose.config.ts "$@"; then
  exit 0
fi

# **Say what the containers saw, here rather than in the caller.** The trap below
# takes the stack down on the way out, so anything that wanted these logs after
# the fact would find nothing left to ask. A browser-side failure is very often a
# server-side one seen from the far end.
echo
echo "--- api, last 100 lines -------------------------------------------------"
$COMPOSE logs --tail 100 api || true
echo "--- web, last 50 lines --------------------------------------------------"
$COMPOSE logs --tail 50 web || true
exit 1
