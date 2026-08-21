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

**Under construction, and runnable.** The repository is built phase by phase
against a written plan; the quickstart below works end to end today — you can
register a database, let it read the schema, ask a question and open the query
behind the answer. Deployment to Azure is Phase 12 and is not done, so there is
nothing hosted to visit: local is the only way to see it. Current position is
always in [docs/plan/STATUS.md](docs/plan/STATUS.md).

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

Getting from nothing to an answer takes about ten minutes, most of which is
Docker pulling images. **You need an OpenAI API key** — see the step below.

### 1. Start the stack

```bash
make up          # .env from .env.example, then platform Postgres + demo DB + api + web
make db.setup    # migrate, and give the API's unprivileged role its login
make seed        # build the pizza demo dataset (~72k orders, about 5 seconds)
make secrets.key # print a LOCAL_SECRETS_KEY line for .env
```

`make secrets.key` prints one line. Paste it into `.env`, replacing the empty
`LOCAL_SECRETS_KEY=`, then **recreate** the api so it reads the new value:

```bash
docker compose -f ops/docker-compose.yml --env-file .env up -d api
```

`docker compose restart api` is **not** enough and fails in a way that looks
unrelated: restarting reuses the container's existing environment, so the key
never arrives, and the first thing you do afterwards — registering a database —
returns a 500 with `LOCAL_SECRETS_KEY is not set`.

### 2. Give it a model — this is required

**The agent cannot answer anything without one, and there is no offline mode.**
That is deliberate: the product *is* the agent, and a demo that answered from a
canned script would teach you the opposite of what it exists to show. A build
that answers without a model exists for CI only, and refuses to start outside it.

Get a key from <https://platform.openai.com/api-keys>, then uncomment and fill
this line in `.env`:

```
OPENAI_API_KEY=sk-...
```

and recreate the api with the same `up -d api` as above — `restart` will not
pick it up. Nothing else needs changing: `.env.example` already names the models
and their prices.

**What it costs.** Very little. Questions in this demo run to roughly ten
thousand tokens each at the tiers `.env.example` configures — a twenty-question
evaluation run spent 223,000 tokens in total. A few questions while you look
around costs a fraction of a US cent. It is not free, which is why nothing here
spends it without you asking.

### 3. Sign in

Open <http://localhost:3000>. A fresh `.env` sets `AUTH_MODE=dev`, so the sign-in
screen asks for a name rather than redirecting you to a corporate identity
provider — type anything (`alice` is prefilled) and continue. The dev issuer
mints tokens this API accepts and **refuses to start in a production build**, so
it cannot follow you anywhere.

Create an organization when asked. You are its Admin.

### 4. Register the demo database

The organization you just created is listed under **Your organizations** with a
**Members** button — that button is the way in. From there choose **Data
sources**, and fill in the **Register a database** form at the bottom:

| Field | Value |
|---|---|
| Name | `Pizza demo` |
| Engine | PostgreSQL |
| Host | `seed-pizza-pg` |
| Port | `5432` |
| Database | `pizza` |
| Username | `pizza_readonly` |
| Password | the `SEED_PIZZA_READONLY_PASSWORD` from your `.env` |

Leave **Encryption** blank, and press **Register**.

**The host is `seed-pizza-pg`, not `localhost`.** The API reaches the database
across the Docker network, where that is its name; `localhost:6543` in the table
below is how *you* reach it from your own machine, which is a different journey.

The credentials are read-only by construction — `make seed` creates that role
with `SELECT` and nothing else — and registering tests them and says whether the
connection was encrypted.

### 5. Let it read the schema

First choose **Test connection**. This is not optional politeness: it is what
proves the credentials cannot write, and until it has passed **Refresh catalog**
refuses with *"this data source has not been proven read-only"*. A catalog is
only worth building on credentials that cannot change the database.

Then **Refresh catalog**, then **Profile columns**. The
first reads the tables and columns; the second samples them, so the agent knows
what a column actually contains and which values look sensitive. Until both have
run the agent has no catalog and will correctly refuse to guess rather than
inventing a schema.

### 6. Ask something

Press **Back** to the organization, then **Ask**. Pick `Pizza demo` in the
**Database** list, press **Start**, open the new conversation, and ask:

> show me the revenue trend by month

You should get a sentence, a chart, a line saying how the answer was reached, and
a control that opens the SQL behind it. Every number is traceable to a query you
can read.

Good follow-ups, because they show what the product is actually for:

- *"how many orders were placed in July 2026?"* — a plain count.
- *"what were our best-selling items?"* — the demo has **no** `order_items`
  table, so this is genuinely unanswerable. A good refusal names what is
  missing rather than inventing a number.
- *"why did revenue fall recently?"* — the data hides a ~12% decline that comes
  almost entirely from one store's delivery orders.

### Stopping and starting

`make down` stops everything and keeps the data; `make down.hard` throws the
volumes away. `make logs` follows all services, `make ps` shows status.

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
