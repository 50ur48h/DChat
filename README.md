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

Not yet available — the local stack arrives in **WP0.4**. Once it does:

```bash
cp .env.example .env     # no real secrets required for the local stack
make up                  # platform Postgres + seed databases + api + web
make seed                # build the pizza demo dataset
# open http://localhost:3000
```

Prerequisites for that quickstart: Docker + Compose, Python 3.12 via
[uv](https://docs.astral.sh/uv/), Node 22 via [pnpm](https://pnpm.io/).

## Contributing

Everything goes through a pull request into a protected `main`; one work package
per branch per PR; `docs/plan/STATUS.md` is updated in the same PR as the work.
The rules that matter are in [CLAUDE.md](CLAUDE.md) and, in full, in
[implementation-plan.md](docs/plan/implementation-plan.md) §1.

## License

None yet — see backlog item **B-001**. Until a license is chosen, the default
applies: all rights reserved.
