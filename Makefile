# data-agent — developer entry points.
#
# Targets grow with the phases; nothing here refers to a component that does not
# exist yet (plan §4). Run `make` or `make help` for the current list.

.DEFAULT_GOAL := help

API_DIR := apps/api
UV_API  := uv run --directory $(API_DIR)

# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------
.PHONY: help
help: ## Show available targets
	@grep -hE '^[a-zA-Z0-9_.-]+:.*?## ' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Umbrella targets — these gain a `.web` half in WP0.3
# ---------------------------------------------------------------------------
.PHONY: install lint fmt typecheck test
install: install.api      ## Install all dependencies
lint: lint.api            ## Lint everything
fmt: fmt.api              ## Format everything in place
typecheck: typecheck.api  ## Type-check everything
test: test.api            ## Run all tests

# ---------------------------------------------------------------------------
# apps/api
# ---------------------------------------------------------------------------
.PHONY: install.api lint.api fmt.api typecheck.api test.api api.dev
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

.PHONY: build.api
build.api: ## Build the production API image
	docker build --target prod \
		--build-arg GIT_SHA=$$(git rev-parse HEAD) \
		-t dataagent-api:local $(API_DIR)
