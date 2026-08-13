# data-agent — developer entry points.
#
# Targets grow with the phases; nothing here refers to a component that does not
# exist yet (plan §4). Run `make` or `make help` for the current list.
#
# Requires GNU make. On Windows run it from Git Bash.
#
# Every recipe here is a SINGLE line, deliberately. GNU make hands a recipe to
# the shell with its backslash-newline continuations intact, and on Windows it
# falls back to cmd.exe whenever sh.exe is not on PATH — cmd.exe does not
# understand `\` continuation, so a multi-line recipe silently runs truncated
# (`docker build --target prod \` alone, which fails with an unhelpful
# "requires 1 argument"). Anything too long for one line belongs in
# ops/scripts/*.sh, invoked as `bash ops/scripts/thing.sh`.
#
# SHELL is set below when a POSIX shell can be found, so pipelines and quoting
# behave the same on every machine.

.DEFAULT_GOAL := help

# These recipes need a POSIX shell. On Windows, make falls back to cmd.exe
# whenever sh.exe is not on PATH -- the normal state when make is run from
# PowerShell, because Git puts cmd\ on PATH but not usr\bin\. Under cmd.exe
# `printf` does not exist, and a bare `bash` resolves to WSL's bash, which
# cannot reach the Docker CLI or this drive the same way. Both fail late and
# confusingly, so probe once and stop with an instruction instead.
SHELL_PROBE := $(shell sh -c "echo yes" 2>&1)
ifneq ($(SHELL_PROBE),yes)
$(error No POSIX shell found, so these recipes cannot run. Use Git Bash, or add Git's usr\bin to PATH first: $$env:PATH = 'C:\Program Files\Git\usr\bin;' + $$env:PATH)
endif
SHELL := sh

GIT_SHA := $(shell git rev-parse HEAD 2>/dev/null)

API_DIR := apps/api
UV_API  := uv run --directory $(API_DIR)

WEB_DIR := apps/web
PNPM_WEB := pnpm --dir $(WEB_DIR)

COMPOSE := docker compose --env-file .env -f ops/docker-compose.yml

# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------
.PHONY: help
help: ## Show available targets
	@grep -hE '^[a-zA-Z0-9_.-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

.PHONY: env
env: .env ## Create .env from .env.example if it does not exist

.env:
	@cp .env.example .env
	@printf 'Created .env from .env.example. Local-only values; review before use.\n'

# ---------------------------------------------------------------------------
# Umbrella targets
# ---------------------------------------------------------------------------
.PHONY: install lint fmt typecheck test
install: install.api install.web         ## Install all dependencies
lint: lint.api lint.web lint.seed        ## Lint everything
fmt: fmt.api                             ## Format everything in place
typecheck: typecheck.api typecheck.web   ## Type-check everything
test: test.api test.web                  ## Run all tests

# ---------------------------------------------------------------------------
# Local stack
# ---------------------------------------------------------------------------
.PHONY: up up.mssql down down.hard logs ps seed seed.mssql
up: .env ## Start the local stack (platform-pg, seed-pizza-pg, api, web)
	$(COMPOSE) up -d --build
	@printf '\n  web  http://localhost:3000\n  api  http://localhost:8000/healthz\n\n'
	@printf 'Next: make seed\n'

up.mssql: .env ## Start the SQL Server container on demand (Phase 3+)
	$(COMPOSE) --profile mssql up -d mssql

down: ## Stop the stack, keeping database volumes
	$(COMPOSE) down

down.hard: ## Stop the stack and delete its volumes (throws away seeded data)
	$(COMPOSE) down --volumes

logs: ## Follow logs from every service
	$(COMPOSE) logs -f

ps: ## Show stack status
	$(COMPOSE) ps

seed: .env ## (Re)build the pizza demo dataset in seed-pizza-pg
	uv run ops/seed/seed_pizza.py

seed.mssql: .env ## (Re)build the pizza demo dataset in the mssql container
	$(SHELL) ops/scripts/seed_mssql.sh

.PHONY: db.setup migrate migrate.down migration
db.setup: .env ## Migrate, then give dataagent_app its local login
	$(SHELL) ops/scripts/db_setup.sh

migrate: .env ## alembic upgrade head against the local platform database
	$(UV_API) alembic upgrade head

migrate.down: .env ## Roll back one revision
	$(UV_API) alembic downgrade -1

migration: .env ## Autogenerate a revision: make migration m="add widgets"
	$(UV_API) alembic revision --autogenerate -m "$(m)"

.PHONY: secrets.key
secrets.key: ## Print a fresh LOCAL_SECRETS_KEY line to paste into .env
	@$(UV_API) python -c "from cryptography.fernet import Fernet; print('LOCAL_SECRETS_KEY=' + Fernet.generate_key().decode())"

.PHONY: check.status
check.status: ## Fail if STATUS.md lost its phase checklist or signed-off work
	bash scripts/check_status.sh --selftest
	bash scripts/check_status.sh

.PHONY: truths check.truths
truths: ## Regenerate ops/seed/truths.json without touching the database
	uv run ops/seed/seed_pizza.py --truths-only

check.truths: ## Fail if truths.json and the seed generator disagree
	uv run ops/seed/seed_pizza.py --check

# ---------------------------------------------------------------------------
# apps/api
# ---------------------------------------------------------------------------
.PHONY: install.api lint.api fmt.api typecheck.api test.api test.rls test.dal api.dev build.api
install.api: ## Sync the API virtualenv from uv.lock
	uv sync --directory $(API_DIR)

lint.api: ## ruff check + format check
	$(UV_API) ruff check .
	$(UV_API) ruff format --check .

fmt.api: ## ruff format + autofix
	$(UV_API) ruff format .
	$(UV_API) ruff check --fix .

typecheck.api: ## pyright (strict)
	$(UV_API) pyright

test.api: ## pytest with coverage
	$(UV_API) pytest --cov --cov-report=term-missing

test.rls: ## Tenant-isolation proof suite on its own; fails if it collects nothing
	$(UV_API) pytest -m rls_proof

test.dal: ## The security boundary's suite, with its own coverage gate (plan §4.4)
	$(UV_API) pytest tests/dal --cov=dataagent.dal --cov-report=term-missing --cov-fail-under=90

api.dev: ## uvicorn with reload on :8000
	$(UV_API) uvicorn dataagent.main:app --host 0.0.0.0 --port 8000 --reload

build.api: ## Build the production API image
	docker build --target prod --build-arg GIT_SHA=$(GIT_SHA) -t dataagent-api:local $(API_DIR)

# ---------------------------------------------------------------------------
# ops/seed — a standalone uv script, so it has its own dependencies and is not
# part of the API's pyright run. It still obeys the same ruff rules.
# ---------------------------------------------------------------------------
.PHONY: lint.seed
lint.seed: ## ruff check the seed scripts
	$(UV_API) ruff check --config pyproject.toml ../../ops
	$(UV_API) ruff format --check --config pyproject.toml ../../ops

# ---------------------------------------------------------------------------
# apps/web
# ---------------------------------------------------------------------------
.PHONY: install.web lint.web typecheck.web test.web web.dev build.web
install.web: ## Install web dependencies from the lockfile
	$(PNPM_WEB) install --frozen-lockfile

lint.web: ## eslint
	$(PNPM_WEB) lint

typecheck.web: ## next typegen + tsc --noEmit
	$(PNPM_WEB) typecheck

test.web: ## vitest
	$(PNPM_WEB) test

web.dev: ## next dev on :3000
	$(PNPM_WEB) dev

build.web: ## Build the production web image
	docker build --target prod -t dataagent-web:local $(WEB_DIR)
