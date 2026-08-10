# data-agent — developer entry points.
#
# Targets grow with the phases; nothing here refers to a component that does not
# exist yet (plan §4). Run `make` or `make help` for the current list.
#
# Requires GNU make, run from a POSIX shell (Git Bash on Windows — the recipes
# use sh, grep and awk). See README for the prerequisites.

.DEFAULT_GOAL := help

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
	@grep -hE '^[a-zA-Z0-9_.-]+:.*?## ' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

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
.PHONY: up up.mssql down down.hard logs ps seed
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

# ---------------------------------------------------------------------------
# apps/api
# ---------------------------------------------------------------------------
.PHONY: install.api lint.api fmt.api typecheck.api test.api api.dev build.api
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

api.dev: ## uvicorn with reload on :8000
	$(UV_API) uvicorn dataagent.main:app --host 0.0.0.0 --port 8000 --reload

build.api: ## Build the production API image
	docker build --target prod \
		--build-arg GIT_SHA=$$(git rev-parse HEAD) \
		-t dataagent-api:local $(API_DIR)

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
