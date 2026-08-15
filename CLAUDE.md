# CLAUDE.md — data-agent

AI-native data analysis platform. Two deployables: `apps/web` (Next.js) and
`apps/api` (FastAPI). Everything else is a library inside the API.

MERGE_POLICY: ASK        # ASK | AUTO — see docs/plan/implementation-plan.md §1.1

## Read before working
1. docs/plan/STATUS.md            — where we are (single source of truth)
2. docs/plan/implementation-plan.md — how we work + current phase spec
3. docs/architecture.md           — what we are building (binding design)

## Commands
make up / make down     # local stack (compose: platform-pg, seed DBs, api, web)
make seed               # (re)build the pizza demo dataset
make migrate            # alembic upgrade head against local platform-pg
make db.setup           # migrate + grant dataagent_app its local login
make api.dev / web.dev  # hot-reload dev servers
make lint / typecheck / test / fmt
make evals              # eval harness with FakeLLM (Phase 9+)

## Hard rules (full list: implementation-plan.md §1)
- PR-only into protected main; squash merge; branch p{phase}.{wp}-{slug}.
- Update docs/plan/STATUS.md in the same PR as the work.
- New deferred work → docs/plan/BACKLOG.md entry (B-###) in the same PR.
- Code TODOs must be `TODO(B-###)`; CI fails otherwise.
- Never commit secrets; .env is local-only; .env.example documents keys.
- Deviating from docs/architecture.md → DECISIONS.md entry + doc edit, same PR.
- dal/ and infra/ changes always need human review.
- The LLM is never a security boundary; all data access goes through dal/.
- The API connects as `dataagent_app` (no superuser, no BYPASSRLS, owns
  nothing). Migrations run as the owner. Never collapse the two.
- Every new tenant table needs an RLS policy in the same PR, plus a line in
  `TENANT_TABLES` and an extension of the rls_proof suite.
- **Every gate PR ends with a manual test script**: numbered steps and what
  the user should see at each, including the failure case.
- Mid-phase, verify it yourself and report the evidence. Only ask the user to
  check what you genuinely cannot: rendered browser UI, anything specific to
  their machine, and decisions. Their time is the scarce resource.

## Environment quirks
- Python via uv (apps/api); Node via pnpm (apps/web).
- pip inside containers only; local host uses `uv sync`.
- SQL Server test container is heavy: `make up.mssql` starts it on demand.
- Windows dev host: GNU make comes from `winget install ezwinports.make` and
  lands on the **user** PATH, so a shell started before the install will not see
  it. Run make from Git Bash — its recipes use `sh`, `grep` and `awk`.
- **`next dev` in the web container does not see host edits.** File-watch events
  do not cross the Windows bind mount, so the container has the new bytes and
  Turbopack never recompiles them. A **new route directory** serves a 404 that
  looks like a routing bug; an **edit to an existing file** is worse, because the
  page still works and silently runs the *old* code — this cost a gate
  (**B-044**), where a fix was reviewed, shipped and tested against a stale
  bundle. `docker compose restart web` fixes both.
  Verify what is actually being served rather than trusting the file:
  ```sh
  curl -s http://localhost:3000/<a-route> | grep -oE '/_next/static/chunks/src_[^"]+\.js' \
    | sort -u | while read c; do curl -s "http://localhost:3000$c" | grep -q '<a-token-from-your-change>' \
    && echo "$c: present" || echo "$c: ABSENT"; done
  ```
- Git Bash rewrites any argument starting with `/` into a `C:\...` path, so a
  `docker exec … sh -c '/opt/…'` arrives as nonsense. Start such command strings
  with a word (`exec /opt/…`), as `ops/scripts/seed_mssql.sh` does.

## Repo facts (this clone)
- GitHub remote: https://github.com/50ur48h/DChat — **public**. Secret hygiene is
  critical from commit one: nothing sensitive in code, docs, fixtures, or PR text.
- The GitHub repository is named `DChat`; the **project** is `data-agent`. All
  names from the plan and architecture stay as written — `apps/api`, `apps/web`,
  the Python package `dataagent`, compose service names. Nothing is renamed to
  "dchat". See docs/plan/DECISIONS.md D-002.
- WP0.1 was the one and only direct push to `main`. Everything after it is a PR.
- License: none yet (tracked as B-001). Until it is chosen, this code is
  "all rights reserved" by default — do not add license headers to files.

## Session ritual (plan §7.1, short form)
1. `git fetch --all && git checkout main && git pull`
2. Read docs/plan/STATUS.md header + current phase; `gh pr list` — address review
   comments and red CI before starting new work.
3. Confirm the next WP's USER INPUT items are in hand (plan §3.2). If not: ask,
   set `Blocked on user:` in STATUS, take a non-dependent WP or stop.
4. Re-read the plan section for the WP + the arch Parts it cites. Branch. Build.
