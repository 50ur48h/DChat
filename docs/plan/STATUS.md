# STATUS — data-agent build

Current position: Phase 3 in progress. WP3.1 done. Next: WP3.2
Merge policy: ASK
Blocked on user: nothing. WP3.2 needs no user input — it connects to the local
                 seed databases the compose stack already provides
Last updated: 2026-08-12 by Claude Code

## Phase 0 — Bootstrap & walking skeleton (M0)
- [x] WP0.1 Repo, docs, tracking files, branch protection
- [x] WP0.2 API skeleton (FastAPI, /healthz, tooling, Dockerfile)
- [x] WP0.3 Web skeleton (Next.js, health page, tooling, Dockerfile)
- [x] WP0.4 Compose stack + Makefile + pizza seed v0
- [x] WP0.5 CI v1 (lint/type/test/build, gitleaks, TODO check)   ← gate PR
- [x] GATE: compose up → page calls API; CI green on main; user sign-off
      — signed off 2026-08-11: page showed Healthy, CI green on main

## Phase 1 — Platform DB + tenancy (M1)
- [x] WP1.1 SQLAlchemy models + alembic + core tables
      — also added CI's Postgres service + `DATABASE_URL` + `REQUIRE_DB=1`,
      the migration up/down test deferred from WP0.5 (DECISIONS D-006, done)
- [x] WP1.2 RLS migration + tenancy session + base repository
- [x] WP1.3 RLS proof tests + migration up/down in CI            ← gate PR
- [x] GATE: cross-org read provably blocked; user sign-off
      — signed off 2026-08-11

## Phase 2 — AuthN/AuthZ (M2)
- [x] WP2.1a JWT validation + JWKS cache + dev issuer (guarded, excluded from prod)
- [x] WP2.1b Request context resolution + role guards + audited denials
      — WP2.1 split in two (plan §1.1): authentication and authorization are
      separately reviewable, which matters more than usual in a security phase
- [x] WP2.2 Orgs/users/invitations APIs + bootstrap + audit events
- [x] WP2.3 Web auth (MSAL) + /me + invite UI + role matrix tests ← gate PR
- [x] GATE: signup→org→invite Reader; Reader 403 audited; user sign-off
      — signed off 2026-08-12 on a real Entra External ID tenant: sign-in,
      org creation, invite link, single-use redemption all confirmed in the
      browser, and the audited 403 confirmed on the demo org: a Reader's
      attempt on an Admin route recorded as auth.denied / insufficient_role
      in that organization's own audit_log (B-010 closed).

## Phase 3 — Data source connectors (M3)
- [x] WP3.1 SecretsProvider (local backend) + datasources CRUD + sanitizer
      — also closed **B-009** (`users.email` is nullable; a missing claim is
      recorded as missing rather than as `<subject>@unknown.invalid`), and
      taught coverage about greenlets, which had been hiding half of what the
      async service tests actually execute
- [ ] WP3.2 Connector protocol + Postgres connector + test-connection
- [ ] WP3.3 SQL Server connector + compose profile + dialect tests ← gate PR
- [ ] GATE: seed DBs registered; creds never echoed; user sign-off

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
- [ ] WP6.2 Azure OpenAI + Anthropic impls + fallback + live smoke  ← gate PR
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
- [ ] **B-005 (P1) must be closed before this phase starts** — the seed window
      must stop drifting before evals are written against it
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
- [ ] GATE: trend question → rendered chart; smoke green; sign-off

## Phase 12 — Azure deploy + hardening (M12)  ⚠ human review on every PR
- [ ] WP12.1 Bicep modules + env params + what-if in CI
- [ ] WP12.2 OIDC deploy workflow → dev env + Key Vault backend + smoke
- [ ] WP12.3 Observability wiring + quotas hard-stop + alerts
- [ ] WP12.4 Prod env + ASVS-lite checklist + restore drill + v1.0 tag ← gate
- [ ] GATE: arch Part 14 acceptance; nightly evals on; user sign-off

---

## Next step

Phase 3 / **WP3.2 — Connector protocol + Postgres connector**
(`p3.2-postgres-connector`). Read plan §6 Phase 3 and architecture Part 5.1
first. It brings:

- `connectors/base.py` — the arch 5.1 protocol, and the `ValidatedQuery` type
  **defined now** even though the DAL does not exist until Phase 5: `execute`
  must be unable to accept anything else, so the type gate exists before there
  is anything to gate.
- `connectors/postgres.py` on asyncpg — read-only session, per-call statement
  timeout, introspection from `information_schema`/`pg_catalog` templates.
- The real `POST …/test`: connect with the stored credentials **and** prove they
  cannot write (an attempted `CREATE TEMP TABLE`/`INSERT` inside a rolled-back
  probe must fail), then record `readonly_verified` and `last_verified_at` on
  the row and move its `status` to `verified` or `error`.

What WP3.1 left for it, deliberately:

- `POST /v1/orgs/{o}/data-sources/{d}/test` today answers only "is the address
  reachable from here" — a TCP connect, no credential on the wire. That is the
  half that needs no driver, and it stays as the first check; WP3.2 layers the
  credential and read-only verification on top rather than replacing it.
- `data_sources.status` can only be `registered` until WP3.2 can honestly set
  the other two values. `last_verified_at` and `readonly_verified` are not
  columns yet — they arrive with the code that fills them (the D-006 rule).
- `connectors/sanitizer.py` exists and is proven against a corpus of realistic
  driver errors; WP3.2 is the first caller with real exceptions to pass through
  it. Every connector error must go through `sanitize_exception`, with the host,
  username and database passed as `known=` — the patterns are the second layer,
  not the first.
- **B-006** (P2) should be closed in WP3.2: the pizza fixture ships only its
  owner login, and `readonly_verified` cannot be true for a role that can write.

## Notes

- **Customer credentials have exactly one home.** They go to the
  `SecretsProvider` and nowhere else: the platform database holds a
  `secret_ref`, responses have no field that could carry one, and audit rows
  record that a rotation happened rather than what it rotated to. Locally the
  backend is a Fernet-encrypted file under `ops/.secrets/` (D-001) whose key
  comes from `LOCAL_SECRETS_KEY` — `make secrets.key` prints one, and a
  production build refuses to start with this backend at all.

- **UI follows docs/design.md.** Tokens live in `apps/web/src/app/globals.css`;
  primitives in `src/components/ui/`. A raw hex value anywhere else is a bug,
  and adding a component library needs a DECISIONS entry first.

- **Every new tenant table, in every later phase, must be added to
  `TENANT_TABLES` and given an RLS policy in the same PR.** This is not left
  to memory: `test_no_tenant_table_can_be_added_without_protecting_it` asks
  the database which tables carry `org_id` and fails on any that are
  undeclared or unprotected.

- **WP0.1** shipped as the single allowed direct push to `main` (plan §6, Phase 0).
  Its checkbox was flipped to `[x]` in the first follow-up PR (WP0.2), because a WP
  is only marked done through a PR (plan §1.3).
- **WP0.2 and WP0.3 were built in parallel** off `main` and merged as #1 and #2;
  the second was rebased onto the first, unioning the root `Makefile`.
- **WP0.4** ships the local stack; **WP0.5** ships CI. The Phase 0 GATE is the
  only thing left in this phase and it is yours: run `make up && make seed`,
  open http://localhost:3000, confirm the page reports the API healthy, then
  sign off and the gate checkbox flips in the first Phase 1 PR.
- Branch protection now requires `hygiene`, `api` and `web` — the D-003
  follow-through, applied once those jobs existed.
