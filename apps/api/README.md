# apps/api — data-agent API

FastAPI service, Python 3.12, fully async. This one deployable contains the agent
runtime, the DAL, connectors, catalog, knowledge and semantic packages — modules,
not microservices (arch Part 2.2).

## Layout

```
src/dataagent/
  main.py      app factory + GET /healthz
  config.py    pydantic-settings; the only sanctioned way to read configuration
  health.py    the liveness probe
  db/          models, engine, alembic migrations
tests/         pytest; ASGI in-process, plus db/ against a real Postgres
```

Packages arrive with their phase: `tenancy/` (P1), `auth/` (P2), `connectors/`
and `datasources/` (P3), `catalog/` (P4), `dal/` (P5), `llm/` (P6), `agent/` and
`runs/` (P7+). The full target tree is in [architecture.md](../../docs/architecture.md)
Part 13.6.

## Migrations

```bash
make migrate                      # alembic upgrade head
make migration m="add widgets"    # autogenerate a revision, then read it
make migrate.down                 # roll back one revision
```

Revisions live in `src/dataagent/db/alembic/versions/`. Always read a generated
revision before committing it — autogenerate is a first draft, not an author.
A test asserts that models and migrations do not drift, so a model edit without a
migration fails the build.

Tests under `tests/db/` need a PostgreSQL server. They skip when none is
reachable so `make test.api` still works without Docker; CI sets `REQUIRE_DB=1`,
which turns that skip into a failure.

## Local development

```bash
uv sync                 # from apps/api — creates .venv with dev tooling
make api.dev            # from the repo root — uvicorn with reload on :8000
curl localhost:8000/healthz
```

`make lint.api typecheck.api test.api fmt.api` cover the gates from the repo root.

## Container

Two targets, both non-root:

```bash
docker build --target dev  -t dataagent-api:dev  apps/api
docker build --target prod -t dataagent-api:prod --build-arg GIT_SHA=$(git rev-parse HEAD) apps/api
```

`prod` is the image that ships. From Phase 2 it also physically excludes the dev
token issuer — `BUILD_ENV=prod` is baked in and asserted at startup (plan §3.1).

## Import direction

`routes → services → {agent | catalog | knowledge | semantic | dal} → connectors → drivers`

One way only (arch Part 0.2.9). Anything the model can influence reaches customer
data through `dal/` and nowhere else.
