# data-agent

An AI-native data analysis platform. You connect your own databases (PostgreSQL,
SQL Server); the platform discovers and profiles the schema; a bounded agent
answers business questions in plain language, shows the queries that produced
each number, and says so honestly when the schema cannot answer.

The differentiator is not chat and not text-to-SQL. It is **iterative
investigation under deterministic safety controls**: the model proposes,
deterministic code disposes — validating SQL against an AST allowlist, grounding
every identifier in the discovered catalog, scoping every row to a tenant,
masking sensitive columns, and enforcing budgets with counters rather than
prompts.

> **Core principle:** the LLM is never the security boundary. The deterministic
> Data Access Layer is. Every hard rule has a deterministic enforcer —
> see [docs/architecture.md](docs/architecture.md) Part 7.

## Status

**Phase 0 — bootstrap.** The repository is being built phase by phase against a
written plan; there is no runnable application yet. Current position is always in
[docs/plan/STATUS.md](docs/plan/STATUS.md).

## What is here

| Path | What it is |
|---|---|
| [docs/architecture.md](docs/architecture.md) | **What** we build. The binding design: agent core, DAL, tenancy, connectors, Azure shape. |
| [docs/plan/implementation-plan.md](docs/plan/implementation-plan.md) | **How** we work. 13 phases, work packages, PR flow, CI, gates. |
| [docs/plan/STATUS.md](docs/plan/STATUS.md) | **Where** we are. Single source of truth for progress. |
| [docs/plan/BACKLOG.md](docs/plan/BACKLOG.md) | Deferred work, append-only IDs (`B-###`). |
| [docs/plan/DECISIONS.md](docs/plan/DECISIONS.md) | Deviations from the architecture, with reasons. |
| [CLAUDE.md](CLAUDE.md) | Working agreement, loaded automatically by Claude Code. |

Code lands under `apps/api` (FastAPI + agent runtime + DAL + connectors),
`apps/web` (Next.js), `ops/` (compose, seed data, evals) and `infra/` (Bicep) as
the phases deliver them.

## Quickstart

```bash
make up          # .env from .env.example, then platform Postgres + demo DB + api + web
make db.setup    # migrate, and give the API's unprivileged role its login
make seed        # build the pizza demo dataset (~72k orders, about 5 seconds)
make secrets.key # print a LOCAL_SECRETS_KEY line for .env, then restart the api
```

Then open <http://localhost:3000>: the page reports the API's health, version and
build commit. `make down` stops everything and keeps the data; `make down.hard`
throws the volumes away. `make logs` follows all services, `make ps` shows status.

`LOCAL_SECRETS_KEY` is only needed once you register a data source: the API
encrypts customer database credentials with it and keeps the ciphertext in
`ops/.secrets/` — never in the platform database, and never in a response. The
key is generated, never chosen, and production refuses this backend outright in
favour of Key Vault ([DECISIONS](docs/plan/DECISIONS.md) D-001).

Connections to a customer's database are encrypted unless its address is on this
machine. The demo databases run in the compose network and serve no certificate,
so compose names them in `TLS_LOCAL_HOSTS` and they connect with `prefer`;
anything else gets `TLS_MODE`, which cannot be set to a mode that allows
plaintext. Each data source reports the mode it uses, and a connection test says
whether the server actually encrypted it (D-011).

| Service | Where | What it is |
|---|---|---|
| web | http://localhost:3000 | Next.js app |
| api | http://localhost:8000/healthz | FastAPI service |
| platform-pg | localhost:5432 | the platform's own database (pgvector, row-level security) |
| seed-pizza-pg | localhost:6543 | stands in for a *customer's* database |
| mssql | localhost:1433 | a second customer database, on demand — see below |

`seed-pizza-pg` holds a generated 18-month pizza-chain dataset. It is a fixture
with deliberate properties — no `order_items` table, so item-level questions are
genuinely unanswerable, and a ~12% revenue decline in the final eight weeks that
comes entirely from one store's delivery orders. Both exist to exercise the
agent's honesty and its research loop later on; `ops/seed/seed_pizza.py`
documents them and checks them on every run.

The SQL Server container stays out of `make up` because its image is ~1.5 GB and
it idles on about 2 GB of memory:

```bash
make up.mssql    # start it (the pull takes a while the first time)
make seed.mssql  # the same schema in T-SQL, with a smaller dataset
```

It exists so a second dialect is exercised end to end rather than assumed to
work — the same tables, the same missing join, and its own read-only login. The
agent's questions and the evals are asked of the PostgreSQL dataset.

### Prerequisites

Docker + Compose, Python 3.12 via [uv](https://docs.astral.sh/uv/)
(`uv python install 3.12` provisions it — no system Python needed), Node 22 via
[pnpm](https://pnpm.io/), and GNU **make**, which is how every command in this
repo is invoked. macOS and Linux have it; on Windows:

```powershell
winget install ezwinports.make    # then restart the shell
```

`make help` lists the current targets.

## Contributing

Everything goes through a pull request into a protected `main`; one work package
per branch per PR; `docs/plan/STATUS.md` is updated in the same PR as the work.
The rules that matter are in [CLAUDE.md](CLAUDE.md) and, in full, in
[implementation-plan.md](docs/plan/implementation-plan.md) §1.

## License

None yet — see backlog item **B-001**. Until a license is chosen, the default
applies: all rights reserved.
