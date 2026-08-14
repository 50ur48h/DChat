# data-agent — V1 Implementation Plan (Claude Code Playbook)

**Companion to:** `docs/architecture.md` (the full architecture & design document).
**Audience:** Claude Code, executing the build phase by phase, plus the human owner who reviews PRs and supplies credentials.
**Precedence when documents disagree:** `docs/architecture.md` → this plan → code comments. Any deviation requires a `DECISIONS.md` entry (see §1.6).

---

# §0 — How to use this guide (operating protocol)

This plan turns the architecture's milestones **M0–M12** into 13 phases (**Phase N = Milestone MN**, same numbering, same order). Each phase is broken into **work packages (WPs)**. One WP = one branch = one PR. WPs are executed **sequentially** unless a WP is explicitly marked parallel-safe.

**The rules of engagement:**

1. **`docs/plan/STATUS.md` is the single source of truth for position.** Every session starts by reading it and ends by updating it. Never infer progress from the git log alone.
2. **All work goes through PRs into `main`.** `main` is protected. No direct pushes after Phase 0 WP0.1.
3. **A WP is "done" only when its PR is merged with CI green** and its checkbox in STATUS is `[x]`.
4. **Newly discovered work is never done inline.** It goes into `docs/plan/BACKLOG.md` with an ID (see §1.5), in the same PR where it was discovered. This is how "future todos are kept safe."
5. **USER INPUT checkpoints are hard stops.** When a phase lists required user inputs (keys, tenant IDs, subscriptions), ask the user for exactly those items and do not fake, stub-and-forget, or commit placeholder secrets. Stubs are allowed only when this plan explicitly says so (e.g., FakeLLM, dev issuer, local secrets backend).
6. **Phase gates require human sign-off.** The last PR of each phase is the *gate PR*. It must be reviewed by the user. Phases 5 (DAL) and 12 (Azure/security) require human review on **every** PR, not just the gate.
7. **The architecture doc is binding.** Section references like "arch Part 7.1" point into `docs/architecture.md`. Build what it says; if reality forces a change, record it (§1.6) and update the architecture doc in the same PR so it never lies.

**Session loop (short form; full ritual in §7):** read STATUS → address open PR review comments first → pick the next `[ ]` WP → branch → build with tests → update STATUS/BACKLOG in the same branch → open PR → CI green → merge per policy → repeat.

---

# §1 — Ground rules and working agreement

## 1.1 Branching and PR flow

- **Model:** trunk-based. One long-lived branch: `main`. Everything else is a short-lived WP branch.
- **Branch naming:** `p{phase}.{wp}-{slug}` for planned work (e.g., `p3.2-postgres-connector`), `fix-{slug}` for bug fixes, `b{id}-{slug}` for backlog items (e.g., `b014-mysql-quoting`).
- **PR titles:** `[P3.2] Postgres connector + capability descriptor` — the `[P{phase}.{wp}]` or `[B-{id}]` prefix is mandatory (CI does not check it; discipline does).
- **Merge method:** **squash merge only.** Keeps `main` linear, one commit per WP.
- **PR size:** target ≤ ~600 changed lines of hand-written code (generated lockfiles/migrations excluded). If a WP grows past that, split it and record the split in STATUS.
- **Merge policy:** configured in `CLAUDE.md` as `MERGE_POLICY: ASK | AUTO`.
  - `ASK` (default): open the PR, request the user's review, stop work on that WP until reviewed. You may start the next WP **only** if it does not depend on the open one.
  - `AUTO`: enable `gh pr merge --squash --auto` so it merges when CI passes — **except** gate PRs and all Phase 5 / Phase 12 PRs, which always wait for human review.
- **Conflicts:** rebase the WP branch on `main` (`git rebase origin/main`), never merge `main` into the branch.

## 1.2 Commit convention

Conventional Commits, enforced socially not mechanically:

```
feat(dal): reject denied columns anywhere in the AST
fix(connectors): mssql TOP vs LIMIT transpile
chore(ci): cache uv downloads
test(agent): budget exhaustion terminates loop
docs(plan): mark P4.2 done
refactor(catalog): extract profiler budget guard
```

Scope = top-level package or area (`web`, `api`, `dal`, `agent`, `connectors`, `catalog`, `infra`, `ci`, `plan`). Squash merge means the PR title becomes the main-line commit — write PR titles with the same care.

## 1.3 Definition of Done (every PR)

A PR may be opened as draft any time, but is ready for review only when **all** of these hold:

1. `make lint` clean (ruff + eslint), `make typecheck` clean (pyright + tsc).
2. Tests: new behavior has tests; `make test` green locally; CI green.
3. Coverage gates hold (see §4.4) — never lower a gate to pass; raise a BACKLOG item and ask instead.
4. No secrets in the diff (gitleaks is in CI, but check before pushing; `.env` files never committed — only `.env.example`).
5. Every new `TODO` in code has a backlog ID: `# TODO(B-017): ...` — CI enforces this (§4.3).
6. `docs/plan/STATUS.md` updated in this PR: this WP's checkbox → `[x]` (or `[~]` with a note if intentionally partial).
7. If anything was discovered but not done → `docs/plan/BACKLOG.md` entries added in this PR.
8. If the implementation deviates from `docs/architecture.md` → `docs/plan/DECISIONS.md` entry **and** the architecture doc edited to match, both in this PR.
9. User-visible behavior changes → 1–3 lines appended to `CHANGELOG.md` under "Unreleased".
10. Migration PRs: `alembic upgrade head` and a downgrade of the new revision both proven by the migration test (§4.4).

## 1.4 Secrets policy

- Secrets live in exactly three places, ever: developer-local `.env` (gitignored), GitHub Actions **secrets/environments**, and (from Phase 12) **Azure Key Vault**. Nowhere else — not in code, compose files, docs, PR bodies, STATUS, or test fixtures.
- Every configurable secret has a line in `.env.example` with a fake value and a comment saying which phase needs it.
- The API reads secrets only via `config.py` (pydantic-settings) or the `SecretsProvider` interface (arch Part 7.3). Customer data-source credentials go **only** through `SecretsProvider` — never into the platform DB (only `secret_ref` strings are stored, per arch Part 5).
- Error paths never echo secrets: the connector error sanitizer (arch Part 5.1) is mandatory from Phase 3 and has its own tests.
- If a secret ever lands in git history: rotate it immediately, then ask the user before any history rewrite.

## 1.5 TODO and backlog protocol ("keep future todos safe")

- **`docs/plan/BACKLOG.md` is the only home for deferred work.** Ideas, discovered bugs, "later" items, V1.1/V2 thoughts that come up mid-build — all of them, same file, same format (§2.3).
- IDs are `B-001, B-002, …`, append-only, never renumbered, never deleted (status becomes `done` or `dropped` instead).
- In-code `TODO`s must reference a backlog ID: `TODO(B-023)`. CI fails on orphan TODOs (§4.3). FIXME/HACK/XXX markers are banned outright — file a backlog item and write honest code.
- Pulling a backlog item into active work: set its status to `planned`, add it to STATUS under the current phase as an extra line (`- [ ] B-023 …`), then treat it like a WP.
- Optional mirror to GitHub Issues (`gh issue create`) is allowed for the user's convenience, but the file remains canonical.

## 1.6 Deviation rule (architecture changes)

When the architecture doc's design does not survive contact with reality:

1. Stop; do not silently build something else.
2. Write `docs/plan/DECISIONS.md` entry `D-###`: context → options → decision → consequences (5–15 lines).
3. Edit `docs/architecture.md` so it describes what is now true.
4. Both edits ship in the same PR as the code, and the PR description calls the deviation out in its own paragraph.
5. Security-relevant deviations (anything touching arch Parts 6–7: auth, tenancy, DAL, secrets) additionally require `MERGE_POLICY: ASK` for that PR regardless of global policy.

## 1.7 What Claude Code must never do

- Push directly to `main` (after WP0.1), force-push `main`, or delete branches with unmerged work.
- Commit secrets, real customer data, or unmasked sample rows into fixtures.
- Lower a coverage gate, skip/xfail a failing security test, or broaden the SQL validator allowlist to make a test pass.
- Give the agent (the product's LLM) any code path around the DAL — arch Part 7 is non-negotiable.
- Mark a WP `[x]` while its PR is unmerged or red.
- Invent values for USER INPUT items (tenant IDs, keys, subscription IDs).
- Start Phase N+1 before Phase N's gate PR is merged and signed off, unless STATUS records an explicit user instruction to overlap.

---

# §2 — The tracking system (files and exact formats)

All tracking lives in the repo so it is versioned, reviewed, and survives any tool or session loss.

```
CLAUDE.md                      # auto-loaded working agreement for Claude Code
CHANGELOG.md                   # human-readable, "Unreleased" section on top
docs/architecture.md           # the design (binding)
docs/plan/implementation-plan.md   # this file
docs/plan/STATUS.md            # position + checkboxes (single source of truth)
docs/plan/BACKLOG.md           # deferred work, append-only IDs
docs/plan/DECISIONS.md         # deviations from the architecture (ADR-lite)
.github/pull_request_template.md
```

## 2.1 `CLAUDE.md` (create verbatim in WP0.1, then maintain)

```markdown
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

## Environment quirks
- Python via uv (apps/api); Node via pnpm (apps/web).
- pip inside containers only; local host uses `uv sync`.
- SQL Server test container is heavy: `make up.mssql` starts it on demand.
```

## 2.2 `docs/plan/STATUS.md` (create in WP0.1 with the full checklist below)

Format rules: one line per WP; states `[ ]` todo, `[~]` in progress/partial (with note), `[x]` merged, `[-]` skipped (with DECISIONS ref). The header block is always current.

```markdown
# STATUS — data-agent build

Current position: Phase 0 / WP0.1 (in progress)
Merge policy: ASK
Blocked on user: —
Last updated: <date> by Claude Code

## Phase 0 — Bootstrap & walking skeleton (M0)
- [ ] WP0.1 Repo, docs, tracking files, branch protection
- [ ] WP0.2 API skeleton (FastAPI, /healthz, tooling, Dockerfile)
- [ ] WP0.3 Web skeleton (Next.js, health page, tooling, Dockerfile)
- [ ] WP0.4 Compose stack + Makefile + pizza seed v0
- [ ] WP0.5 CI v1 (lint/type/test/build, gitleaks, TODO check)   ← gate PR
- [ ] GATE: compose up → page calls API; CI green on main; user sign-off

## Phase 1 — Platform DB + tenancy (M1)
- [ ] WP1.1 SQLAlchemy models + alembic + core tables
- [ ] WP1.2 RLS migration + tenancy session + base repository
- [ ] WP1.3 RLS proof tests + migration up/down in CI            ← gate PR
- [ ] GATE: cross-org read provably blocked; user sign-off

## Phase 2 — AuthN/AuthZ (M2)
- [ ] WP2.1 JWT validation, dev issuer (guarded), context, role guards
- [ ] WP2.2 Orgs/users/invitations APIs + bootstrap + audit events
- [ ] WP2.3 Web auth (MSAL) + /me + invite UI + role matrix tests ← gate PR
- [ ] GATE: signup→org→invite Reader; Reader 403 audited; user sign-off

## Phase 3 — Data source connectors (M3)
- [ ] WP3.1 SecretsProvider (local backend) + datasources CRUD + sanitizer
- [ ] WP3.2 Connector protocol + Postgres connector + test-connection
- [ ] WP3.3 SQL Server connector + compose profile + dialect tests
- [ ] WP3.4 Data sources screen (register, test, rotate, remove)     ← gate PR
- [ ] GATE: both seed DBs registered **from the browser**; creds never echoed;
      read-only verified; user sign-off

## Phase 4 — Discovery & catalog (M4)
- [ ] WP4.1 Schema discovery → catalog tables + refresh + incremental hash
- [ ] WP4.2 Profiler (budgets/timeouts) + sensitivity classifier + auto-mask
- [ ] WP4.3 Table cards + search + catalog APIs/UI + column policy  ← gate PR
- [ ] GATE: pizza DB discovered ≤2 min; email auto-masked; user sign-off

## Phase 5 — DAL + SQL policy engine (M5)  ⚠ human review on every PR
- [ ] WP5.1 sqlglot validator + policy pipeline + catalog grounding
- [ ] WP5.2 Executor (read-only, timeouts, LIMIT) + masking + audit hook
- [ ] WP5.3 Adversarial corpus per dialect + property tests + 90% gate ← gate PR
- [ ] GATE: arch Part 7.5 property table proven in tests; user sign-off

## Phase 6 — LLM abstraction (M6)
- [ ] WP6.1 LLMProvider protocol + FakeLLM + registry + usage metering
- [ ] WP6.2 OpenAI + Anthropic impls + fallback + live smoke  ← gate PR
- [ ] GATE: same suite passes on both providers; tokens metered; sign-off

## Phase 7 — Single-shot Q&A (M7)
- [ ] WP7.1 Conversations/runs/events schema + routes + run status
- [ ] WP7.2 Context builder + planner-lite + core tools + repair-or-refuse
- [ ] WP7.3 e2e vs seed DB + minimal chat UI with citation          ← gate PR
- [ ] GATE: "orders in July?" answered with citation; user sign-off

## Phase 8 — Research loop + trace (M8)
- [ ] WP8.1 ResearchState + bounded loop + budgets + duplicate/progress rules
- [ ] WP8.2 Capability check (join-graph) + honest refusal path
- [ ] WP8.3 SSE streaming + durable replay + trace UI               ← gate PR
- [ ] GATE: pizza scenario ≤8 iters; menu-items → honest refusal; sign-off

## Phase 9 — Critic + composer + evals (M9)
- [ ] WP9.1 Deterministic critic + LLM checklist + bounded re-entry
- [ ] WP9.2 Composer (citations/limitations) + eval harness v1      ← gate PR
- [ ] GATE: seeded-wrong-draft caught; 20 golden evals pass; sign-off

## Phase 10 — Knowledge + semantic layer (M10)
- [ ] WP10.1 Docs ingest/chunk/embed/retrieve under RLS + APIs
- [ ] WP10.2 Semantic definitions + verified queries + critic enforcement ← gate
- [ ] GATE: uploaded policy changes generated SQL; isolation test; sign-off

## Phase 11 — Charts + polish (M11)
- [ ] WP11.1 Chart tool (validated Vega-Lite) + client renderer
- [ ] WP11.2 History/catalog/members polish + Playwright smoke      ← gate PR
      — carries **B-017**: recovery when an org has no Admin who can sign in
      (owner's call 2026-08-12, moved forward from Phase 12)
- [ ] GATE: trend question → rendered chart; smoke green; sign-off

## Phase 12 — Azure deploy + hardening (M12)  ⚠ human review on every PR
- [ ] WP12.1 Bicep modules + env params + what-if in CI
- [ ] WP12.2 OIDC deploy workflow → dev env + Key Vault backend + smoke
- [ ] WP12.3 Observability wiring + quotas hard-stop + alerts
- [ ] WP12.4 Prod env + ASVS-lite checklist + restore drill + v1.0 tag ← gate
- [ ] GATE: arch Part 14 acceptance; nightly evals on; user sign-off
```

## 2.3 `docs/plan/BACKLOG.md`

```markdown
# BACKLOG — deferred work (append-only)
| ID | Date | Found during | Title & detail | Suggested phase | Prio | Status |
|----|------|--------------|----------------|-----------------|------|--------|
| B-001 | 2026-08-11 | P0.4 | Example: compose healthcheck flaky on cold start — add retry | P0 | P2 | open |
```

Rules: `Prio` ∈ P1 (blocks V1) / P2 (should fix before V1 ships) / P3 (V1.1+). `Status` ∈ open / planned / done (PR#) / dropped (reason). Never renumber. V2 ideas from arch Part 12 may be pre-seeded here.

## 2.4 `docs/plan/DECISIONS.md`

```markdown
# DECISIONS — deviations & choices made during the build
## D-001 — Local secrets backend before Key Vault (pre-approved)
Date: 2026-08-11 · Phase: 3 · PR: #NN
Context: Arch M3 lists Key Vault as a dependency, but Azure arrives in Phase 12.
Decision: Implement SecretsProvider with an encrypted local-file backend
(Fernet, key from .env) for dev; KeyVaultSecretsProvider lands in WP12.2
behind the same interface. Prod images refuse to start with the local backend.
Consequences: zero Azure cost until Phase 12; interface proven early.
```

## 2.5 `.github/pull_request_template.md`

```markdown
## [P_._ / B-___] Title

**What & why** (2–5 lines)

**How it maps to the plan/architecture:** phase/WP, arch Part refs.

**Tests added:**

**Checklist**
- [ ] lint + typecheck + tests green locally
- [ ] no secrets in diff; .env.example updated if new config
- [ ] STATUS.md updated (this WP)
- [ ] BACKLOG entries added for anything deferred (or "none")
- [ ] DECISIONS + architecture doc updated if deviating (or "no deviation")
- [ ] CHANGELOG "Unreleased" updated if user-visible
```

---

# §3 — Environments and the secrets matrix

## 3.1 Environments

| Env | Where | Auth | Secrets backend | LLM | Exists from |
|---|---|---|---|---|---|
| `local` | docker compose | dev issuer (guarded) or Entra | local encrypted file | FakeLLM default; real keys optional | Phase 0 |
| `ci` | GitHub Actions | dev issuer only | ephemeral env vars (fake) | FakeLLM only | Phase 0 |
| `dev` (Azure) | Container Apps | Entra External ID | Key Vault + managed identity | real, budget-capped | Phase 12 |
| `prod` (Azure) | Container Apps | Entra External ID | Key Vault + managed identity | real, quota-enforced | Phase 12 |

Dev-issuer guardrails (WP2.1): enabled only when `AUTH_MODE=dev` **and** the image is not built with `BUILD_ENV=prod`; the prod Dockerfile target physically excludes the dev issuer module; startup asserts and refuses otherwise. This is arch M2's "dev issuer excluded from prod image" requirement.

## 3.2 Secrets & inputs matrix — what to ask the user, and when

| Item | Needed from | Stored in | Notes |
|---|---|---|---|
| GitHub org/repo name, visibility, license, MERGE_POLICY | **Phase 0, WP0.1** | — | Also: user must have `gh` authenticated in the Claude Code environment |
| `LOCAL_SECRETS_KEY` (generated, not asked) | Phase 3 | local `.env` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| Entra External ID: tenant ID, SPA client ID, API app ID/audience | **Phase 2, WP2.3** (backend can ship on dev issuer first) | `.env` / web env / GH env | User task card in Phase 2 has the exact clicks |
| Embedding provider key (OpenAI) | Phase 4 (optional — search degrades to lexical) → **required Phase 10** | `.env`, GH secret `EMBEDDINGS_*` | Same account as the chat models (D-017) |
| OpenAI API key + the model ids to use per tier | **Phase 6, WP6.2** (live smoke; CI stays on FakeLLM) | `.env` (`OPENAI_API_KEY`, `LLM_MODELS`), GH secrets | **Primary provider** (D-017 — not Azure OpenAI). At least ONE real provider needed |
| Anthropic API key + its model ids per tier | Phase 6, WP6.2 | `.env` (`ANTHROPIC_API_KEY`, `LLM_MODELS`), GH secret | Second provider proves the abstraction |
| Azure subscription ID, region, resource-group naming OK | **Phase 12, WP12.1** | GH environment vars | |
| Entra app registration for GitHub OIDC (federated credential) | Phase 12, WP12.2 | GH env `AZURE_CLIENT_ID/TENANT_ID/SUBSCRIPTION_ID` | No client secrets in CI — OIDC only |
| Budget alert email, monthly cap | Phase 12, WP12.3 | Bicep params | |
| Custom domain (optional) | Phase 12, WP12.4 | Bicep params | Skippable |

When a checkpoint is reached, ask for **exactly** the rows due, set `Blocked on user:` in STATUS, and work on any non-dependent WP meanwhile (or stop if none).

---

# §4 — CI/CD blueprint

CI grows with the phases; never build pipeline for components that don't exist yet.

## 4.1 `ci.yml` — introduced WP0.5, extended later

```yaml
name: ci
on:
  pull_request:
  push: { branches: [main] }
concurrency: { group: ci-${{ github.ref }}, cancel-in-progress: true }

jobs:
  changes:                       # path filters keep PRs fast
    runs-on: ubuntu-latest
    outputs:
      api: ${{ steps.f.outputs.api }}
      web: ${{ steps.f.outputs.web }}
    steps:
      - uses: actions/checkout@v4
      - id: f
        uses: dorny/paths-filter@v3
        with:
          filters: |
            api: ['apps/api/**', 'ops/**', '.github/**']
            web: ['apps/web/**', '.github/**']

  hygiene:                       # always runs
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: gitleaks/gitleaks-action@v2
      - run: bash scripts/check_todos.sh

  api:
    needs: changes
    if: needs.changes.outputs.api == 'true'
    runs-on: ubuntu-latest
    services:
      pg:
        image: pgvector/pgvector:pg16
        env: { POSTGRES_PASSWORD: test, POSTGRES_DB: dataagent_test }
        ports: ['5432:5432']
        options: >-
          --health-cmd "pg_isready -U postgres" --health-interval 5s
          --health-timeout 5s --health-retries 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv sync --frozen
        working-directory: apps/api
      - run: make lint.api typecheck.api
      - run: make test.api          # includes migration up/down from Phase 1
        env: { DATABASE_URL: postgresql+asyncpg://postgres:test@localhost:5432/dataagent_test }
      # Phase 5 adds:  make test.dal   (separate job step, 90% coverage gate)
      # Phase 3 adds a conditional mssql service job for connector tests

  web:
    needs: changes
    if: needs.changes.outputs.web == 'true'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22, cache: pnpm, cache-dependency-path: apps/web/pnpm-lock.yaml }
      - run: pnpm install --frozen-lockfile && pnpm lint && pnpm typecheck && pnpm test && pnpm build
        working-directory: apps/web

  docker:                        # build (not push) to prove images; push added Phase 12
    needs: [api, web]
    if: always() && github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t api:ci apps/api && docker build -t web:ci apps/web
```

Later additions (do not pre-build): **Phase 3** — mssql service job, path-filtered to `connectors/**` + `dal/**`; **Phase 5** — `test.dal` step with `--cov=dataagent.dal --cov-fail-under=90`; **Phase 9** — `make evals` (FakeLLM, deterministic) as a required check + `nightly-evals.yml` (`schedule` + `workflow_dispatch`, real keys, hard token cap); **Phase 12** — `deploy.yml` (below) and `bicep what-if` on infra PRs.

## 4.2 `deploy.yml` — introduced WP12.2 (shape only; details in Phase 12)

```yaml
name: deploy
on:
  push: { branches: [main] }
  workflow_dispatch:
permissions: { id-token: write, contents: read }
jobs:
  deploy-dev:
    environment: dev            # holds AZURE_* vars; no secrets — OIDC login
    steps: [checkout, azure/login@v2 (OIDC), acr build+push (tag = git sha),
            az deployment group create (bicep, dev params), smoke suite]
  deploy-prod:
    needs: deploy-dev
    environment: prod           # environment protection rule = manual approval
    steps: [same, prod params, smoke suite]
```

## 4.3 `scripts/check_todos.sh` — introduced WP0.5

```bash
#!/usr/bin/env bash
set -euo pipefail
# Any TODO in source must reference a backlog ID: TODO(B-123). FIXME/HACK/XXX banned.
viol=$(grep -rnP 'TODO(?!\(B-\d+\))' --include='*.py' --include='*.ts' --include='*.tsx' \
       apps/ ops/ 2>/dev/null | grep -v node_modules || true)
bans=$(grep -rnE 'FIXME|HACK|XXX' --include='*.py' --include='*.ts' --include='*.tsx' \
       apps/ ops/ 2>/dev/null | grep -v node_modules || true)
if [[ -n "$viol$bans" ]]; then
  echo "Orphan TODOs or banned markers found — add BACKLOG.md entries (§1.5):"
  echo "$viol"; echo "$bans"; exit 1
fi
```

## 4.4 Test & coverage gates (ratchet up, never down)

| Gate | From | Rule |
|---|---|---|
| api unit+integration | P0 | `pytest` green; pg service in CI |
| migrations | P1 | empty-DB `upgrade head` → `downgrade -1` → `upgrade head` in CI |
| RLS proof | P1 | dedicated tests that a buggy repo call still can't cross orgs — **may never be skipped** |
| api coverage | P5 | overall `--cov-fail-under=70`; **dal package ≥90** in its own step |
| adversarial SQL corpus | P5 | every corpus case rejected; corpus only grows (append-only file) |
| web | P0 | eslint, tsc, vitest; build must pass |
| evals (FakeLLM) | P9 | 20 golden questions deterministic in CI; required check |
| evals (real LLM) | P9 | nightly, token-capped, failure notifies — not a merge blocker |
| Playwright smoke | P11 | login (dev issuer) → ask → answer path in compose |
| post-deploy smoke | P12 | health, authd request, one real single-shot run against seed source |

## 4.5 Branch protection (run once in WP0.1, after first push)

> **Amended in WP0.1 (DECISIONS D-003):** the `required_status_checks` block below
> is applied as `null` in WP0.1 and set to the real contexts in **WP0.5**, when the
> `hygiene` / `api` / `web` jobs first exist. A required check that has never
> reported blocks every PR as "Expected — waiting for status", which would make the
> Phase 0 PRs unmergeable without an admin bypass. Everything else in this block —
> PR-required, linear history, no force-push, no deletion, squash-only — is applied
> in WP0.1 exactly as written.

```bash
gh api -X PUT "repos/$OWNER/$REPO/branches/main/protection" --input - <<'JSON'
{ "required_status_checks": {"strict": true, "contexts": ["hygiene", "api", "web"]},
  "enforce_admins": false,
  "required_pull_request_reviews": {"required_approving_review_count": 0},
  "restrictions": null,
  "allow_force_pushes": false, "allow_deletions": false,
  "required_linear_history": true }
JSON
gh repo edit "$OWNER/$REPO" --enable-squash-merge --enable-rebase-merge=false --enable-merge-commit=false
```

Note: review count stays 0 so `MERGE_POLICY: AUTO` can work; ASK mode and the Phase 5/12 human-review rule are enforced by this plan's discipline, and the user may raise the count to 1 any time for hard enforcement.

---

# §5 — Phase overview (the map)

| Phase | Name | PRs | User inputs due | Hard exit criterion |
|---|---|---|---|---|
| 0 | Bootstrap & walking skeleton | 5 | repo details, merge policy | compose up → web page shows API health; CI green |
| 1 | Platform DB + tenancy | 3 | — | cross-org read blocked despite buggy repo call |
| 2 | AuthN/AuthZ | 3 | Entra IDs (can trail) | Reader gets audited 403 on admin route |
| 3 | Connectors + secrets | 4 | — (key auto-generated) | seed DBs registered from the browser; creds never echoed |
| 4 | Discovery & catalog | 3 | embedding key (optional) | pizza DB discovered ≤2 min; email auto-masked |
| 5 | **DAL + policy engine** | 3 | — | arch 7.5 property table fully proven; dal ≥90% cov |
| 6 | LLM abstraction | 2 | ≥1 real LLM key | same tests pass on both providers; fallback works |
| 7 | Single-shot Q&A | 3 | — | July-orders answered with execution citation |
| 8 | Research loop + trace | 3 | — | pizza scenario ≤8 iters; **honest refusal works**; SSE replay |
| 9 | Critic + evals | 2 | — | wrong-draft caught; 20 golden evals in CI |
| 10 | Knowledge + semantic | 2 | embedding key (required) | uploaded policy changes generated SQL |
| 11 | Charts + polish | 2 | — | trend → validated Vega-Lite chart; Playwright green |
| 12 | **Azure + hardening** | 4 | subscription, OIDC, budget | dev+prod live via Bicep; quota hard-stop; drill done |

Dependency shape is linear except: WP3.3 (MSSQL) may float later if the user wants a faster demo (record as `[-] moved` in STATUS + backlog item) — WP3.4 then becomes the gate PR with Postgres alone; Phase 6 depends only on Phase 0 and may be built while waiting on Phase 2 user inputs.

---

# §6 — The phases in detail

Format per WP: **Branch → Build → Tests → Accept** (accept = commands/checks that must pass before the PR is ready). Arch references are to `docs/architecture.md`.

---

## Phase 0 — Bootstrap & walking skeleton (M0)

**Goal:** a monorepo that boots end to end with CI, so every later phase lands on rails.

> **USER INPUT (before WP0.1):** GitHub org + repo name; private or public; license (suggest MIT or "none/proprietary"); `MERGE_POLICY` ASK or AUTO; confirm `gh auth status` works in this environment.

### WP0.1 — Repo, docs, tracking, protection — `p0.1-repo-bootstrap`
- `gh repo create <org>/<repo> --private --clone` (adjust to answers). Single direct push to `main` is allowed for this WP only.
- Commit: `README.md` (what this is, quickstart pointer), `LICENSE`, `.gitignore` (python, node, .env, .venv, dist), `.editorconfig`, `CHANGELOG.md` (empty Unreleased), `CLAUDE.md` (§2.1 verbatim, fill MERGE_POLICY), `docs/architecture.md` (copy the architecture file in), `docs/plan/implementation-plan.md` (this file), `docs/plan/STATUS.md` (§2.2 full checklist), `docs/plan/BACKLOG.md`, `docs/plan/DECISIONS.md` (seed with D-001 from §2.4), `.github/pull_request_template.md`.
- Apply branch protection + squash-only (§4.5).
- **Accept:** repo exists; protection active (`gh api repos/$R/branches/main/protection` returns rules); STATUS shows WP0.1 `[x]` via a follow-up PR — from here on, everything is a PR.

### WP0.2 — API skeleton — `p0.2-api-skeleton`
- `apps/api`: `uv init`; `pyproject.toml` pinning python 3.12; deps: fastapi, uvicorn[standard], pydantic-settings (~~orjson~~ — dropped, see DECISIONS D-005); dev: ruff, pyright, pytest, pytest-asyncio, httpx, pytest-cov.
- `src/dataagent/main.py` (app factory), `config.py` (pydantic-settings, `.env` support), `GET /healthz` → `{status, version, git_sha}`.
- Multi-stage `Dockerfile` (uv sync → slim runtime, non-root user); targets `dev` and `prod`.
- Root `Makefile` first cut: `lint.api typecheck.api test.api fmt.api api.dev`.
- **Tests:** healthz unit test via httpx ASGI client. **Accept:** `make lint.api typecheck.api test.api` green; `docker build apps/api` succeeds.

### WP0.3 — Web skeleton — `p0.3-web-skeleton`
- `apps/web`: `pnpm create next-app` (TS, App Router, no src alias surprises), strict tsconfig, eslint, vitest + testing-library, `.nvmrc`/engines node 22.
- One page `/` calling `GET {NEXT_PUBLIC_API_URL}/healthz` and rendering status; typed fetch helper in `lib/api-client/` (hand-rolled now; OpenAPI-generated client is B-seeded for Phase 7).
- `Dockerfile` (standalone output). Makefile: `web.dev lint.web typecheck.web test.web`.
- **Tests:** component test for the health widget (mocked fetch). **Accept:** `pnpm build` green; page renders against local API.

### WP0.4 — Compose + seed v0 — `p0.4-compose-seed`
- `ops/docker-compose.yml`: `platform-pg` (image `pgvector/pgvector:pg16`, volume), `seed-pizza-pg` (postgres:16, port 6543), `api` (build, env from `.env`), `web` (build); healthchecks + `depends_on: condition: service_healthy`. Profile `mssql`: `mcr.microsoft.com/mssql/server:2022-latest` (used from Phase 3).
- `ops/seed/seed_pizza.py` + `pizza_schema.sql` v0 — **deliberately per arch Part 9.2**: tables `stores, customers, staff, menu_items, orders (order_date, store_id, customer_id, channel, total_amount, status), payments` and **NO `order_items` table** (this powers the honest-refusal test in Phase 8). Generate ~18 months of data with a seeded RNG (fixed seed → reproducible), an embedded ~12% revenue decline in the last 8 weeks concentrated in one store + delivery channel (powers the Phase 8 demo), and a `customers.email` column (powers Phase 4 auto-masking).
- Root `.env.example`; `make up down seed logs`.
- **Accept:** `make up && make seed` → web page at :3000 shows API healthy; `psql` count on `orders` > 50k; reseed produces identical row counts (fixed seed).

### WP0.5 — CI v1 — `p0.5-ci` *(gate PR)*
- `.github/workflows/ci.yml` per §4.1 (jobs: changes, hygiene, api, web, docker); `scripts/check_todos.sh` (§4.3, `chmod +x`); `.gitleaks.toml` if defaults need tuning.
- Update branch protection required checks to the real job names.
- **Accept:** CI green on the PR; intentionally add `TODO no-id` in a scratch commit → hygiene fails → remove (prove the guard); **GATE:** demo `make up` result to user, get sign-off, flip gate checkbox.

---

## Phase 1 — Platform DB + tenancy plumbing (M1)

**Goal:** the platform schema exists with RLS so tenant isolation is structural before any feature code. Schema source: arch Part 10.1.

### WP1.1 — Models + alembic + core tables — `p1.1-db-core`
- Deps: sqlalchemy[asyncio] 2.x, asyncpg, alembic. `db/models.py` + `db/alembic/` (async template).
- Revision 0001: extensions (`pgcrypto`, `vector`), tables `organizations, users, memberships, invitations, audit_log` exactly per arch 10.1 (UUID PKs, `org_id` on every tenant-scoped table, timestamps, enums as CHECK constraints).
- `audit_log` is append-only: no UPDATE/DELETE grants to the app role (enforced in the RLS revision next WP).
- **Tests:** migration up/down test fixture (creates temp DB, upgrades, downgrades, re-upgrades) wired into `make test.api` and CI. **Accept:** `make migrate` clean on fresh compose DB.

### WP1.2 — RLS + session + base repository — `p1.2-rls`
- Revision 0002: `CREATE ROLE dataagent_app NOLOGIN` pattern → actually: connect role `dataagent_app` (no BYPASSRLS, not owner); `ALTER TABLE ... ENABLE ROW LEVEL SECURITY; FORCE ROW LEVEL SECURITY`; policy `USING (org_id = current_setting('app.org_id')::uuid)` on every tenant table (arch 10.1 DDL); revoke UPDATE/DELETE on `audit_log` from app role.
- `tenancy/session.py`: async session factory that wraps every transaction in `SET LOCAL app.org_id = :org_id` taken from request context; refuses to hand out a session without an org (except an explicit, separately-audited `system_session()` for bootstrap/admin jobs).
- `tenancy/base_repo.py`: thin repository base that *also* filters by org_id in SQL — RLS is the net, not the only filter (defense in depth per arch Part 6.3).
- Local compose grants: create the `dataagent_app` login in seed script for platform-pg; API connects as it (never as postgres superuser).
- **Tests:** unit tests for session refusal without org context.

### WP1.3 — RLS proof + CI wiring — `p1.3-rls-proof` *(gate PR)*
- The RLS proof suite (arch M1 acceptance): create org A and org B rows in every tenant table; open a session as org A; execute a **deliberately unfiltered** raw `SELECT * FROM <table>`; assert only org A rows return. Repeat for INSERT with wrong org_id (must fail). Mark suite `@pytest.mark.rls_proof` and assert in CI that the marker ran (guards against accidental deselection).
- **Accept/GATE:** proof suite green in CI; user sign-off. From now on, every new tenant table added in any phase must extend this suite in the same PR (add to DoD mentally — it is part of "tests for new behavior").

---

## Phase 2 — AuthN/AuthZ (M2)

**Goal:** real logins, three roles, guarded routes, audited denials. Design: arch Part 6.1–6.2 (diagrams 3–4).

> **USER TASK (needed by WP2.3; WP2.1–2.2 proceed on dev issuer):** create Entra External ID tenant → App registration **spa-web** (platform SPA, redirect `http://localhost:3000`, later the Azure URL) → App registration **api** (expose scope `access_as_user`, note Application ID URI) → grant spa-web the api scope → send me: tenant ID, spa client ID, API audience/app ID URI. (I will provide click-by-click when we get there.)

### WP2.1 — Token validation + dev issuer + guards — `p2.1-auth-core`
- `auth/jwt.py`: OIDC discovery + JWKS cache, validate iss/aud/exp/nbf/sig; claims → `Principal` (sub, email, name).
- `auth/dev_issuer.py`: local RS256 issuer + `/dev/token?sub=&email=` route — **only mounted when `AUTH_MODE=dev`**; prod image target excludes the module (Dockerfile build arg + import guard + startup assertion). Test that the prod settings combination raises at boot.
- `auth/context.py`: request context (principal, org_id, role) resolved per request; `auth/guards.py`: `require_role(admin|contributor|reader)` dependencies implementing the arch 6.2 role matrix; every 401/403 writes an `audit_log` row (actor, route, decision, reason).
- **Tests:** JWT validation unit matrix (bad sig/aud/iss/expired), guard unit tests.

### WP2.2 — Orgs, bootstrap, invitations — `p2.2-orgs-invites`
- Routes per arch 10.2: `GET /v1/me` (principal + memberships), `POST /v1/orgs` (bootstrap: first login may create org → creator becomes Admin), `POST /v1/orgs/{id}/invitations` (Admin; email + role; signed token, 7-day expiry), `POST /v1/invitations/accept`, `GET/PATCH /v1/orgs/{id}/members` (role change, remove; last-Admin protection).
- Audit events: org.created, invitation.created/accepted, member.role_changed/removed, auth.denied.
- **Tests:** integration flow signup→org→invite→accept across two orgs; last-admin cannot demote self; RLS proof extended to `invitations`.

### WP2.3 — Web auth + role matrix proof — `p2.3-web-auth` *(gate PR)*
- Web: MSAL (`@azure/msal-browser/react`) with runtime config; dev-mode toggle that instead fetches a dev-issuer token (so local UX works before Entra IDs arrive — same guard rules as API); authenticated fetch wrapper injecting bearer; `/me` page; minimal members+invite screen (Admin only).
- Role-matrix integration test (API level): for each (role × representative route class) assert allow/deny per arch 6.2 table; snapshot the matrix so changes are loud.
- **Accept/GATE (arch M2):** with Entra IDs configured: real signup → create org → invite Reader (second test user) → Reader hits an Admin route → 403 → audit row visible. If Entra IDs still pending: run the same flow on dev issuer, mark gate `[~] pending Entra smoke`, set `Blocked on user`, and continue to Phase 3 (allowed overlap — record in STATUS).

---

## Phase 3 — Data source connectors + secret storage (M3)

**Goal:** register external databases safely; credentials handled only by the SecretsProvider; connectors speak through one protocol. Design: arch Part 5.1.

### WP3.1 — SecretsProvider + datasources CRUD + sanitizer — `p3.1-secrets-datasources`
- `secrets/base.py` (protocol: `put/get/delete(secret_ref)`), `secrets/local.py` (Fernet-encrypted JSON file under `ops/.secrets/`, key from `LOCAL_SECRETS_KEY`; refuses to load when `ENV=prod` — D-001), `secrets/factory.py`.
- `datasources/` routes per arch 10.2: CRUD + `POST /v1/data-sources/{id}/test`. On create: store creds via SecretsProvider → save only `secret_ref` (`ds/{org_id}/{ds_id}/credentials`) in `data_sources` table (revision 0003; RLS + proof-suite extension). Responses never include credentials, ever — pydantic response models simply have no such field.
- `connectors/sanitizer.py`: error-message scrubber (drops connection strings, passwords, hosts by pattern) applied to every connector exception before it can reach logs or API responses. Unit-test with nasty realistic driver errors.
- **Tests:** CRUD integration; secret round-trip; sanitizer corpus; grep-style test asserting no response schema exposes credential fields.

### WP3.2 — Connector protocol + Postgres connector — `p3.2-postgres-connector`
- `connectors/base.py`: the arch 5.1 protocol — `capabilities() -> CapabilityDescriptor` (dialect, max_identifier_len, features), `test_connection()`, `list_schemas/tables/columns/foreign_keys()`, `execute(validated: ValidatedQuery, limits) -> ResultFrame`. The `ValidatedQuery` type is defined **now** (opaque, constructible only by the DAL validator — enforced by module-private constructor) so the type gate exists before the DAL does; until Phase 5, only discovery-internal introspection queries run, built from fixed templates.
- `connectors/postgres.py` on asyncpg: read-only session (`default_transaction_read_only=on`), statement_timeout applied per call, introspection via `information_schema`/`pg_catalog` templates.
- Registration verification: `test` endpoint checks connectivity **and** verifies the supplied role cannot write (attempt `CREATE TEMP TABLE`/`INSERT` inside a rolled-back probe; must fail) — arch M3 "read-only verified". Result stored on the data_source row (`last_verified_at`, `readonly_verified`).
- **Tests:** integration vs compose `seed-pizza-pg` (register, verify, introspect FK graph); failure-path tests through the sanitizer.

### WP3.3 — SQL Server connector — `p3.3-mssql-connector` *(may float later — see §5 note)*
- Driver decision per arch Part 3: pyodbc + msodbcsql18 in the API image, wrapped with `asyncio.to_thread` behind the same async protocol; document driver install in Dockerfile.
- `connectors/sqlserver.py`: introspection via `sys.*` views; capability descriptor marks dialect `tsql` (TOP-not-LIMIT etc. — consumed by DAL in Phase 5); read-only probe uses an attempted write in a rolled-back transaction.
- TLS follows the policy settled in **B-013 / D-011**: the connector is *given* a `tls_mode` and maps it to `Encrypt` / `TrustServerCertificate` on the ODBC connection string, then reads back what was actually negotiated (`sys.dm_exec_connections`) into the same `TlsStatus` the Postgres connector returns. Unlike the Postgres container, SQL Server serves a self-signed certificate, so its local connection is genuinely encrypted and genuinely unverified — the evidence must say both.
- Compose `mssql` profile + `ops/seed/seed_pizza_mssql.sql` (same schema/no order_items; smaller row count is fine); CI job for connector tests with mssql service, path-filtered.
- **Accept:** both seed DBs registerable and verifiable through the API; a forced connector error surfaces sanitized.

### WP3.4 — Data sources screen — `p3.4-datasources-ui` *(gate PR)*
> Added 2026-08-12 from **B-012**, accepted by the owner. WP3.1 shipped the full
> CRUD API and the phase's exit criterion was written as "registered via the
> API", which in practice meant a curl command. Registering a database is the
> first thing a new organization must do, so it gets a screen — and the gate
> demo moves into the browser, where the product actually is.

- Admin-only screen at `/orgs/{orgId}/data-sources`, built from the existing primitives in `src/components/ui/` and the tokens in `globals.css` (docs/design.md; a raw hex value anywhere else is a bug). List, register, test, rotate credentials, remove.
- The form is the one place in the product where a customer credential is typed. `type="password"`, never echoed back into the field from a response, never written to component state that outlives the submit, and never in a URL. The response has no such field to render (WP3.1's schema guard), so the screen shows `username_last4` and `host_display` instead.
- Test result rendered honestly: reachable / verified / failed with the **sanitized** message the API returned, and the row's `status` and `last_verified_at`. A failure is a normal outcome with a next step, not a red toast.
- Each source shows its `tls_mode`, and a test result shows `tls_encrypted` / `tls_detail` (B-013, D-011). "Encrypted" and "verified" are different claims and the screen must not merge them: the demo databases connect with `prefer` and are **not** encrypted, and that has to be readable rather than buried.
- **B-008** (P3) is closed here rather than in Phase 11: a Reader must not be shown Register/Test/Remove at all. `role` is already on `/v1/me` and on the members list, so hiding admin-only controls costs one condition — and the members screen gets the same treatment while the pattern is fresh.
- **Tests:** vitest for the screen's states (empty, listing, submitting, sanitized failure, forbidden-for-Reader); a test asserting no rendered DOM and no fetch body outside the submit ever contains the typed password.
- **Accept/GATE (arch M3):** in the browser, as an Admin: register the pizza Postgres and the SQL Server seed, both report `readonly_verified=true`, a deliberately wrong password shows a sanitized failure with no host or DSN in it, and a Reader sees neither form nor buttons. Credentials appear only in the secrets store. User sign-off.

---

## Phase 4 — Discovery & catalog (M4)

**Goal:** point at a database → searchable, profiled, sensitivity-classified catalog with table cards. Design: arch Part 5.2–5.3.

> **USER INPUT (optional now):** embedding key (`EMBEDDINGS_PROVIDER/ENDPOINT/KEY/MODEL`). Without it, card search runs lexical-only (tsvector) and WP4.3 leaves embedding backfill as a flagged, idempotent job for later — no blocking.

### WP4.1 — Discovery pipeline — `p4.1-discovery`
- Revision **0007**: `catalog_snapshots, catalog_tables, catalog_columns, catalog_relationships` per arch 10.1 (+ RLS + proof extension). **Superseded by DECISIONS D-012** on two points: `catalog_snapshots` is also the run record, so there is no `discovery_runs`; and there is no `catalog_schemas` until something is stored *about* a schema.
- `catalog/discovery.py`: full crawl via connector introspection; structural hash per table, and a crawl whose hashes all match writes **nothing at all** — no new snapshot, no rows — so incrementality is a row-level property rather than a claim. A change builds a new snapshot and supersedes the previous one, which is kept for runs still reading it.
- Routes: `POST /v1/orgs/{org}/data-sources/{id}/refresh` (Contributor+), `GET /v1/orgs/{org}/data-sources/{id}/catalog` browse (tables → columns → FKs). Org-scoped, like every other route since WP2.2. The metadata pass is seconds, so it runs inline; WP4.2's profiling is the work that needs a background runner and a pollable status.
- **Tests:** golden catalog snapshot of the pizza DB (deterministic seed makes this stable); re-run with no schema change touches zero rows (incremental proof); FK graph includes `orders→stores/customers` and **no path `orders↔menu_items`** (assert! Phase 8 depends on it).

### WP4.2 — Profiler + sensitivity — `p4.2-profiler-sensitivity`
- `catalog/profiler.py`: per-column stats per arch 5.2 (null %, distinct estimate, min/max, top-k for low-cardinality, sampled) under a **profiling budget**: row-sampling caps, per-query statement_timeout, per-source wall-clock budget; partial results are fine and recorded.
- `catalog/classify.py`: rule-based sensitivity (name patterns + value regex on the *sample*: email, phone, national-id-ish, payment-ish) → writes `column_policies` (revision 0005) with `mask` as the auto default for detected PII; samples stored in catalog are masked **at write time** (arch M4 security).
- Route: `PATCH /v1/catalog/columns/{id}/policy` (Admin: allow|mask|deny + reason, audited).
- **Tests:** classifier corpus (true/false positives), budget-stops-profiling test (huge synthetic table, budget forces partial), masked-at-write test proving raw emails never reach the platform DB.

### WP4.3 — Cards + search + UI — `p4.3-cards-search` *(gate PR)*
- `catalog/cards.py`: table cards per arch 5.3 (compact natural-language summary of table, columns, keys, relationships, row counts — the exact text the agent will later consume); stored + tsvector column; embeddings written when key configured (pgvector), else queued flag.
- `catalog/search.py`: lexical (websearch_to_tsquery) + optional vector rerank; route `GET /v1/catalog/search?q=`.
- Web: data sources page (list/register/test/refresh with run status) + catalog browser (tables, columns, sensitivity badges, policy editor for Admin).
- **Accept/GATE (arch M4):** fresh `make seed` → register → refresh completes ≤2 min; `customers.email` shows `mask` policy automatically; searching "revenue" returns `orders` card first; user sign-off.

---

## Phase 5 — DAL + SQL policy engine (M5) ⚠

**The security boundary. Human review on every PR. Highest test density in the repo.** Design: arch Part 7.1 (diagram 6), 7.5 property table.

### WP5.1 — Validator + policy pipeline — `p5.1-dal-validator`
- `dal/validator.py` on sqlglot: parse in the connector's dialect → walk AST → enforce, in order (arch 7.1 pipeline): single statement; statement type ∈ {SELECT, EXPLAIN}; no DML/DDL/tx-control anywhere (including inside CTEs/subqueries); no system schemas (`pg_catalog, information_schema, sys, INFORMATION_SCHEMA`, dialect-aware); no functions on the deny list (e.g., `pg_read_file`, `pg_sleep`, `xp_*`, `openrowset`); every table/column identifier resolves against the org's catalog (grounding — unknown → structured error naming the unknown identifier); denied columns rejected **anywhere** in the AST (select list, WHERE, JOIN ON, ORDER BY, subqueries, CTE bodies); star-expansion resolved against catalog before column checks.
- Output: the opaque `ValidatedQuery` (canonical SQL, tables/columns touched, applied dialect) — the only object connectors will execute. Structured `PolicyViolation` errors (machine-readable code + human message; message safe to show the LLM per arch 7.4).
- `dal/policy.py`: per-org context loader (column policies, per-source row/limit caps) with a small in-process TTL cache.
- **Tests:** exhaustive unit suite per rule; both dialects; error-code snapshots.

### WP5.2 — Executor + masking + audit — `p5.2-dal-executor`
- Revision 0006: `query_executions`, `result_artifacts` per arch 10.1 (+RLS+proof).
- `dal/executor.py`: takes ValidatedQuery → injects/clamps LIMIT (default 1 000, hard cap per policy) when absent on the outermost SELECT → executes via connector with statement timeout + max-bytes guard → normalizes to a typed ResultFrame → `dal/masking.py` applies `mask` policies to result values (format-preserving where cheap: `a***@d***.com`) → persists artifact (parquet or JSON to local disk now, Blob in Phase 12 — same `ArtifactStore` interface) → `dal/audit_hook.py` writes `query_executions` (org, ds, sql_hash, canonical SQL, rows, ms, status) + `audit_log` row. Failures audited too.
- Internal API only (no public route): `DAL.run(org_ctx, ds_id, sql) -> Execution` — the single entry point the agent will ever get.
- **Tests:** LIMIT injection/clamping matrix; timeout kill proven against `pg_sleep` on a *scratch* connection fixture (the deny list blocks it via DAL — test executor timeout with a slow seeded query instead); masking end-to-end (email masked in results AND artifact); audit row written on success/failure/violation.

### WP5.3 — Adversarial corpus + gates — `p5.3-dal-adversarial` *(gate PR)*
- `apps/api/tests/dal/adversarial_corpus.yaml`: append-only attack list (starter set in Appendix C — multi-statement, comment tricks, DML-in-CTE, system catalog probes, unknown/denied identifiers in every clause position, UNION smuggling, casing/quoting/unicode homoglyph identifiers, dialect-specific `TOP`/`OFFSET` abuse, function deny-list hits). Runner asserts **every** case → PolicyViolation, never execution.
- Property-based tests (hypothesis): generated identifier casings/quotings never bypass grounding.
- CI: dedicated `test.dal` step, `--cov=dataagent.dal --cov-fail-under=90`; corpus file marked append-only in review checklist.
- **Accept/GATE (arch M5):** the arch 7.5 property table transcribed as a test map — every row has a named passing test; both human sign-offs (this gate is the release valve for everything after it).

---

## Phase 6 — LLM abstraction + metering (M6)

**Goal:** provider-agnostic, metered, fallback-capable LLM calls; the FakeLLM test harness is born. Design: arch Part 4 (LLMProvider), Part 8.3. Depends only on Phase 0 — build it while blocked, if useful.

> **USER INPUT (WP6.2):** at least one real provider — an **OpenAI** API key (platform.openai.com) and/or an **Anthropic** API key, plus the model ids to use for the small/mid/strong tiers, since this build ships no default model ids. Both providers is better (proves the abstraction). CI never uses them. Azure OpenAI is deferred to Phase 12 — DECISIONS **D-017**.

### WP6.1 — Protocol + FakeLLM + registry + meter — `p6.1-llm-core`
- `llm/base.py`: `LLMProvider.complete(request) -> Completion` (structured output via JSON-schema-constrained call where supported, else parse+repair once — the repair lives in `llm/structured.py` so every provider inherits it); roles per arch 4.9: `intake, observe, plan, sql, critic, compose`, each mapped to a **tier** (`small|mid|strong`) in `llm/registry.py` from config (`LLM_ROLE_MAP`), and tiers mapped to model ids per provider (`LLM_MODELS`). DECISIONS **D-018**.
- `llm/fake.py`: deterministic FakeLLM — scripted responses keyed by (role, matcher), buildable from plain data so a fixture file needs no parser here; records every call for assertions; this is the backbone of all agent tests and CI evals.
- `llm/service.py`: the front door — resolve, call, meter, parse, repair once. The only place a model is called, for the same reason `dal.run` is the only place customer data is read.
- `llm/meter.py` + revision **0011** `usage_ledger` (+RLS+proof): tokens in/out, model, role, tier, org, run_id, cost estimate; every call metered no matter the provider, failures included.
- **Tests:** registry resolution, schema-output repair path, meter rows written, FakeLLM determinism.

### WP6.2 — Real providers + fallback — `p6.2-llm-providers` *(gate PR)*
- `llm/openai.py`, `llm/anthropic.py` (httpx, retries w/ jitter on 429/5xx per arch 8.5); `llm/fallback.py`: static ordered fallback per role (arch Part 4) — on provider-level failure after retries, next provider in the chain `registry.resolve` already returns, annotate completion with `provider_used`.
- `scripts/llm_smoke.py`: manual/live smoke (simple structured call per provider) — run locally with real keys, never in CI.
- **Accept/GATE (arch M6):** one contract test suite passes against FakeLLM in CI and (manually) against both real providers; injected-429 test triggers fallback; `usage_ledger` rows present; keys only via config; sign-off.

---

## Phase 7 — Single-shot Q&A (M7)

**Goal:** the product skeleton — question → grounded SQL → DAL → cited answer. No loop yet. Design: arch Part 4 (context, tools), Part 10.2–10.3.

### WP7.1 — Conversations, runs, events plumbing — `p7.1-runs-schema`
- Revision 0008: `conversations, messages, agent_runs, agent_events, findings` per arch 10.1 (+RLS+proof). `agent_events` append-only (same grant lock as audit_log).
- Routes per arch 10.2: create conversation, post message → creates `agent_run` (status queued→running→…), `GET run`, `GET run/events` (poll now; SSE in Phase 8). Events written through one `EventWriter` (arch 10.3 event types) — everything the UI will ever show flows through it from day one.
- **Tests:** run lifecycle integration; event ordering; RLS proof extension.

### WP7.2 — Context + planner-lite + tools — `p7.2-single-shot`
- `agent/context.py`: build the L0–L5 layered prompt (arch Part 4) — platform rules, org context, relevant table cards (catalog search on the question), column policies summary, user question; token-budgeted assembly with deterministic truncation order.
- `agent/tools/`: `registry.py` + `search_tables`, `describe_table`, `run_sql` (thin wrapper over DAL — **the only data path**), `finalize`. Tool I/O strictly pydantic.
- `agent/runner.py` single-shot mode: plan (one `sql_author` call with structured output) → run_sql → on `PolicyViolation(unknown identifier)` exactly **one** repair attempt with the violation fed back (arch M7 "repaired-or-refused") → finalize with answer + citation (execution id, SQL, row count) → events + findings written.
- **Tests (FakeLLM):** happy path; hallucinated column → repair; repair fails → clean refusal with the violation surfaced; budget: max 3 LLM calls enforced.
- **Live smoke script** (`scripts/agent_smoke.py`, real key, local only): "How many orders were placed in July 2026?" against seed DB.

### WP7.3 — e2e + minimal chat UI — `p7.3-chat-ui` *(gate PR)*
- Web: conversation page — message list, composer, run status, answer card with expandable "evidence" (SQL + rows preview + execution link). Poll events.
- e2e test (API level, FakeLLM scripted to produce the known-good SQL): full HTTP flow user→answer with citation asserting the DAL execution really ran against seed data.
- **Accept/GATE (arch M7):** July-orders question answered in the UI with a real citation; hallucinated-column path demonstrably repairs-or-refuses; full-path audit rows (message → run → execution → audit) verified in one test; sign-off.

---

## Phase 8 — Research loop, budgets, trace (M8)

**Goal:** the differentiator — bounded multi-step research with honest refusal and a live trace. Design: arch Part 4.4 (diagram 5), Part 11.2.

### WP8.1 — State + loop + budgets — `p8.1-research-loop`
- `agent/state.py`: `ResearchState` pydantic model per arch 4.4 (question, plan, hypotheses, executed queries w/ hashes, findings, iteration, budget counters); checkpointed to `agent_runs.state_json` every transition (crash-resumable).
- `agent/loop.py`: the bounded for-loop state machine — understand → plan → act(tool) → observe → reflect — with defaults from arch 4.4 (8 iterations, 10 queries, 20 LLM calls, 150k tokens, 240s wall) in `agent/budget.py` (org-overridable caps, hard-stop semantics); duplicate-query hash rejection; monotone-progress rule (no new finding in 2 consecutive iterations → forced move to finalize); **guaranteed finalize-with-caveats** on any exhaustion (never a dangling run).
- **Tests (FakeLLM):** scripted 4-iteration run is deterministic and replayable from a checkpoint; each budget individually exhausted → finalize-with-caveats; duplicate blocked; progress rule fires.

### WP8.2 — Capability check + honest refusal — `p8.2-capability-check`
- `agent/capability.py` per arch Part 4: map question entities → catalog tables (via cards/search), then **deterministic reachability over `catalog_relationships`** — if required tables have no join path, produce a machine-readable `CapabilityGap` (missing link, e.g., `orders ↔ menu_items`).
- Loop integration: gap found at plan time → skip research → finalize with the honest refusal template (states what's missing and what data would unlock it — arch 11.x wording). Gap discovered mid-loop → same, with partial findings kept.
- **Tests:** the flagship — **"Which menu items sell best?" against the seed DB (no order_items) → refusal names the missing link, zero queries executed**; control question with a valid path proceeds; unit tests on graph reachability incl. multi-hop paths.

### WP8.3 — SSE + trace UI — `p8.3-sse-trace` *(gate PR)*
- `runs/sse.py`: `GET /v1/runs/{id}/events?after=<seq>` as SSE; events come from the durable `agent_events` table (write→notify; replay = read from `after`), so refresh/reconnect replays cleanly (arch Part 10.3).
- Web trace UI: live step timeline (plan, tool calls with SQL + status, findings, budget meter), collapses into the final answer card; conversation stays usable during a run.
- **Accept/GATE (arch M8):** the pizza revenue-decline scenario (arch 11.2) reproduced live ≤8 iterations with real LLM locally (scripted FakeLLM version asserted in CI); duplicate-query block visible in trace; **menu-items refusal demo**; mid-run browser refresh → full trace replays; sign-off.

---

## Phase 9 — Critic, composer, eval harness (M9)

**Goal:** validated, cited answers and a regression net. Design: arch Part 4 (hybrid critic), M9.

### WP9.1 — Hybrid critic — `p9.1-critic`
- `agent/critic.py` deterministic half (runs first, free): every numeric claim in the draft maps to a finding/execution; date ranges in SQL match the question's stated range; filters required by applicable semantic definitions present (hook now, definitions arrive Phase 10); row-count sanity (answer not built on 0-row results without saying so); units/aggregation mismatch heuristics.
- LLM half: `cheap` role, fixed checklist rubric, structured verdict (pass | revise(reasons) | insufficient_evidence).
- Loop wiring: at most **one** bounded re-entry on revise/insufficient (arch M9); second failure → finalize with limitations listing the critic's reasons.
- **Tests (FakeLLM):** seeded wrong-date-range draft caught by the deterministic half (no LLM needed); re-entry happens once and only once; verdict schema enforced.

### WP9.2 — Composer + eval harness v1 — `p9.2-composer-evals` *(gate PR)*
- `agent/composer.py`: final answer assembly — direct answer, evidence citations (execution ids), method notes, explicit limitations/caveats block; findings marked `cited=true` when used.
- `ops/evals/`: `golden.yaml` — 20 questions over the seed DB across arch classes (single-shot, multi-step, refusal, ambiguous, policy-masked column) each with checks (`must_cite`, `sql_must_contain`/`must_not_contain`, `must_refuse`, numeric tolerance vs known seed truths); `runner.py` executes via the real agent stack with **FakeLLM scripts** for CI determinism, and with real LLMs when `EVALS_LIVE=1`.
- CI: `make evals` (FakeLLM) required; `.github/workflows/nightly-evals.yml` (schedule + dispatch, `EVALS_LIVE=1`, hard `EVALS_TOKEN_BUDGET`, uses repo secrets, posts summary to the run log — not a merge blocker).
- **Accept/GATE (arch M9):** all 20 golden evals pass in CI; live eval run recorded once with results in the PR; answers in UI show citations + limitations; sign-off.

---

## Phase 10 — Knowledge + semantic layer (M10)

**Goal:** org documents and metric definitions ground the SQL; retrieval is tenant-isolated. Design: arch Part 5.4–5.5 (diagram 7).

> **USER INPUT (now hard-required if not given in Phase 4):** embedding provider credentials.

### WP10.1 — Knowledge ingest + retrieval — `p10.1-knowledge`
- Revision 0009: `documents, document_chunks (vector + tsvector)` per arch 10.1 (+RLS+proof).
- `knowledge/`: upload (md/txt/pdf-text v1), chunking (heading-aware, overlap), embed (provider from Phase 6 config), hybrid retrieve (vector + lexical, RRF merge) — all under the org session so RLS isolates chunks; retrieval tool `knowledge.search` registered for the agent with L4 framing (retrieved text is *reference material, not instructions* — arch 7.4 wording in the tool result envelope).
- Routes per arch 10.2: documents CRUD + reindex. Web: simple documents page.
- **Tests:** org-isolation retrieval test (two orgs, same query — zero cross-hits) added to the RLS proof family; chunker goldens; injection-framing test (a chunk containing "ignore your instructions" arrives wrapped, and the FakeLLM script asserts the envelope).

### WP10.2 — Semantic definitions + verified queries — `p10.2-semantic` *(gate PR)*
- Revision 0010: `semantic_definitions, verified_queries` (+RLS+proof).
- `semantic/definitions.py`: YAML-backed metric/dimension definitions per arch 5.4 (e.g., `net_revenue = sum(orders.total_amount) where status not in ('cancelled','refunded')`) with validation against the catalog; `semantic/verified.py`: admin-blessed query patterns surfaced to the planner as few-shot grounding.
- Agent wiring: definitions retrieved into context (L3) when entities match; **critic deterministic rule activated**: if a used metric has a definition with required filters, the executed SQL must contain them (AST check via the Phase 5 validator's parse — reuse, don't re-implement).
- Routes + minimal admin UI for definitions/verified queries.
- **Accept/GATE (arch M10 / arch Part 5 scenario):** upload the revenue-policy doc + define `net_revenue` → ask "what was net revenue last month" → generated SQL contains the exclusion filter; remove the filter in a seeded draft → critic blocks; isolation tests green; sign-off.

---

## Phase 11 — Charts + product polish (M11)

**Goal:** demo-complete UX. Design: arch Part 3 (Vega-Lite client render), M11.

### WP11.1 — Chart tool + renderer — `p11.1-charts`
- `agent/tools/chart.py`: takes a finding's result frame + intent → emits a **Vega-Lite spec validated against a strict schema subset** (whitelisted marks/encodings; no external URLs — data inline by values or artifact ref; reject otherwise). Spec stored on the finding.
- Web: vega-embed renderer in the answer/trace cards with graceful fallback to table on invalid spec (defense in depth — server already validated).
- **Tests:** spec validator corpus (valid, sneaky-url, unknown-mark, oversized); FakeLLM trend question end-to-end produces a rendered-spec event.

### WP11.2 — Polish + smoke — `p11.2-polish-smoke` *(gate PR)*
- Conversation history list + rename/delete; catalog and members pages rounded out; empty/loading/error states; mobile-usable layout pass.
- **B-017 — recovery when an organization has no Admin who can sign in.** Scheduled here by the owner on 2026-08-12, moved forward from Phase 12: it is a product gap rather than a demo inconvenience, and this is already the members work package. Decide between a break-glass platform-operator role (audited, and itself a privileged surface to defend) and an ownership-transfer an Admin arms in advance; the second needs no new privilege and is the better default. Whatever ships, `ops/scripts/set_role.sh` stops being the answer and says so in its own header.
- Playwright: compose-based smoke — dev-issuer login → register seed source (idempotent) → ask July-orders → answer card visible → open trace. Wired into CI as `web-e2e` job (path-filtered, allowed ~5 min).
- **Accept/GATE (arch M11):** "show me the revenue trend by month" → chart renders; Playwright green in CI; a non-developer can run the demo from README quickstart alone; sign-off.

---

## Phase 12 — Azure deploy + hardening (M12) ⚠

**Human review on every PR.** Design: arch Part 9 (diagram 10), Part 14 acceptance.

> **USER INPUT (WP12.1–12.3, ask in one batch):** subscription ID + target region + naming approval (`rg-dataagent-dev/prod`); permission to create the GitHub-OIDC app registration + federated credential (or user creates from my exact commands); budget alert email + monthly cap; which LLM/embedding keys go to Key Vault; optional custom domain.

### WP12.1 — Bicep + what-if CI — `p12.1-bicep`
- `infra/`: modules per arch 9.1 — log analytics + app insights (daily cap), Key Vault (RBAC mode), Postgres Flexible (B-series, pgvector enabled, private access per arch), storage (artifacts container), ACR (basic), Container Apps env + apps `api`/`web` (scale-to-zero, secrets from KV via managed identity), user-assigned identity + role assignments (KV Secrets User, AcrPull, Blob Contributor). `main.bicep` + `params/dev.bicepparam`, `params/prod.bicepparam`.
- CI: on `infra/**` PRs run `az bicep build` + `what-if` against dev (read-only) using the OIDC identity once it exists (until then, `bicep build` lint only).
- **Tests/Accept:** `bicep build` clean; module parameters reviewed against arch 9.1 service-justification table (nothing extra creeps in).

### WP12.2 — Deploy pipeline + KV backend + dev env — `p12.2-deploy-dev`
- User task (exact commands provided): app registration + federated credentials for `repo:<org>/<repo>:environment:dev|prod`; GH environments `dev`, `prod` (prod: required reviewer = user) holding `AZURE_CLIENT_ID/TENANT_ID/SUBSCRIPTION_ID`.
- `secrets/keyvault.py`: KeyVaultSecretsProvider (DefaultAzureCredential) behind the Phase 3 interface; provider chosen by `SECRETS_BACKEND`; prod boot asserts `keyvault`. Artifact store gains Blob backend the same way.
- `deploy.yml` per §4.2: sha-tagged ACR builds, bicep deploy, alembic migration job (Container Apps job, runs before app revision swap), post-deploy smoke (§4.4). Deploy dev.
- **Accept:** dev URL serves the app; Entra login works against deployed redirect URI; zero secrets in workflow files/logs (OIDC only); registering a source stores creds in KV (verify via `az keyvault secret list` naming, value never surfaced).

### WP12.3 — Observability + quotas + alerts — `p12.3-obs-quotas`
- OTel wiring (FastAPI + httpx + asyncpg instrumentation) → App Insights; log policy per arch 8.1: sql_hash-only in traces, no prompt bodies, sampling on, daily cap set in Bicep.
- `quotas/`: org monthly token/spend quota (from `usage_ledger`) with **hard-stop** — runs refuse to start over quota, mid-run checks fail-closed to finalize-with-caveats; Admin quota view route. Azure: budget alert (email) + App Insights availability + failure-rate alerts in Bicep.
- **Tests:** quota hard-stop integration (seed ledger near cap → run blocked with clear error, audited); log-scrubbing unit test (no SQL text/prompt body in emitted telemetry attributes).

### WP12.4 — Prod + hardening + v1.0 — `p12.4-prod-hardening` *(gate PR)*
- Deploy prod (approval-gated); custom domain if provided.
- Hardening pass with committed evidence in `docs/hardening-v1.md`: OWASP ASVS-lite checklist per arch M12 (authn, access control, injection, secrets, logging); dependency audit (`pip-audit`, `pnpm audit`) triaged (fix or BACKLOG with justification); **restore drill** — restore Postgres to a scratch server from backup, run migration check + RLS proof against it, destroy; rate-limit sanity (slowapi or CA ingress rules per arch); nightly evals enabled against dev with token cap.
- Tag `v1.0.0`, move CHANGELOG Unreleased → 1.0.0.
- **Accept/GATE (= arch Part 14 acceptance):** dev+prod live via Bicep only; managed identity everywhere (zero secrets in pipeline); quota hard-stop proven in dev; drill documented; checklist signed by user; STATUS shows every phase `[x]`.

---

# §7 — Session rituals and safety valves

## 7.1 Session start
1. `git fetch --all && git checkout main && git pull`.
2. Read `docs/plan/STATUS.md` header + current phase; read open PRs (`gh pr list`) — **address review comments and red CI before new work.**
3. Confirm the next WP's user inputs are in hand; if not, ask now (exact items from §3.2), set `Blocked on user:`, pick a non-dependent WP or stop.
4. Re-read this plan's section for the WP + the arch Parts it cites. Branch. Build.

## 7.2 Session end (or context getting long)
1. Commit + push the branch (even if incomplete — never leave work only local).
2. STATUS: WP `[~]` with a one-line "next step" note if unfinished; BACKLOG for anything discovered; open/refresh the PR (draft ok).
3. Update `Last updated` + `Current position`. Stop cleanly — the next session must be able to resume from files alone.

## 7.3 Stuck protocol
Timebox ~45 min on one blocker → then: write the blocker into STATUS under the WP (`BLOCKED: <what I tried, what I need>`), file a BACKLOG item if it implies future work, and ask the user one specific question (not "it doesn't work" — include error, attempts, proposed options A/B). If blocked on a third-party quirk, a DECISIONS-recorded workaround beats a heroic yak-shave.

## 7.4 Reviewer checklist (for the user, per PR)
Does the PR: match its WP scope; keep the DAL/security rules intact (no new data path, no widened allowlist); include tests that would fail without the change; update STATUS/BACKLOG/DECISIONS honestly; avoid secrets? For Phase 5/12: read the diff line by line — these two phases are the product's trustworthiness.

---

# §8 — Appendices

## A. Kickoff prompt (paste into Claude Code to start or resume)

```
You are building the data-agent project. Ground rules and state live in the repo:
1) Read CLAUDE.md, then docs/plan/STATUS.md, then the current phase section of
   docs/plan/implementation-plan.md, plus the architecture Parts it references.
2) Resume at "Current position". Follow the session ritual (plan §7.1).
3) Work one WP per branch/PR. Update STATUS and BACKLOG in the same PR.
4) Stop and ask me only at USER INPUT checkpoints or per the stuck protocol.
Begin.
```

## B. Command cheat sheet

```
make up / down / seed / migrate        # local stack
make lint typecheck test               # umbrella targets (api+web)
make test.dal                          # DAL suite with 90% gate (P5+)
make evals                             # FakeLLM eval harness (P9+)
gh pr create --fill --draft            # open PR from branch
gh pr merge --squash --auto            # AUTO policy only, non-gate PRs
gh run watch                           # follow CI
uv run pytest -k rls_proof             # isolation proof on demand
```

## C. Adversarial corpus — starter cases (WP5.3 seeds; append-only thereafter)

`DELETE FROM orders` · `SELECT 1; DROP TABLE orders` · `WITH x AS (UPDATE orders SET total_amount=0 RETURNING *) SELECT * FROM x` · `SELECT * FROM pg_catalog.pg_tables` · `SELECT * FROM information_schema.columns` · `SELECT * FROM sys.objects` · `SELECT pg_sleep(60)` · `SELECT pg_read_file('/etc/passwd')` · `EXEC xp_cmdshell 'dir'` · `SELECT * FROM openrowset(...)` · `SELECT * FROM orders_backup` (unknown table) · `SELECT ssn FROM customers` (unknown column) · `SELECT email FROM customers` (denied column, select list) · `SELECT id FROM customers WHERE email LIKE '%x%'` (denied in WHERE) · `SELECT * FROM customers c JOIN stores s ON c.email = s.name` (denied in JOIN) · `SELECT * FROM (SELECT email FROM customers) t` (denied in subquery) · `SELECT 1 UNION SELECT email FROM customers` (UNION smuggle) · `SELECT "EMAIL" FROM "CUSTOMERS"` (case/quoting probe) · `SELECT еmail FROM customers` (unicode homoglyph) · `SELECT * INTO new_t FROM orders` (tsql SELECT INTO) · `BEGIN; SELECT 1; COMMIT` (tx control) · `EXPLAIN ANALYZE DELETE FROM orders` (DML under EXPLAIN) · oversized `LIMIT 999999999` (clamp check).

## D. Golden eval questions — starter 20 (WP9.2; tune to seed truths)

1 total orders July 2026 · 2 revenue last full month (uses net_revenue once P10 lands; numeric before) · 3 orders by channel share · 4 top store by revenue · 5 slowest revenue week · 6 revenue trend 6 months (chart) · 7 AOV by channel · 8 cancelled-order rate · 9 **best-selling menu items → must_refuse (missing order_items)** · 10 customer repeat rate · 11 revenue decline question → multi-step, must cite ≥2 executions · 12 which store drove the decline · 13 was the decline channel-specific · 14 busiest day of week · 15 staff count per store (simple join) · 16 question naming a masked column → answer without exposing raw values · 17 ambiguous "how are we doing" → bounded diagnostic with limitations · 18 date range respected ("between Mar 1 and Mar 15") · 19 empty-result honesty (future date range) · 20 duplicate of 11 phrased differently → verifies deterministic path stability.

## E. Ten commandments (print, pin)

1 STATUS is truth · 2 one WP, one PR · 3 tests are the feature · 4 the DAL is the boundary · 5 RLS proofs never skip · 6 secrets never in git · 7 todos get IDs · 8 deviations get DECISIONS · 9 gates get humans · 10 when unsure, ask with options.

— End of plan. Build well.
