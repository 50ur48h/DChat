# STATUS — data-agent build

Current position: Phase 3 in progress. WP3.1–3.2 and B-013 done. Next: WP3.3
Merge policy: ASK
Blocked on user: nothing. WP3.3 pulls the SQL Server image (~1.5 GB) on first
                 `make up.mssql`; nothing else is needed
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
- [x] WP3.2 Connector protocol + Postgres connector + test-connection
      — also closed **B-006** (`make seed` now creates `pizza_readonly`, so the
      demo database can be registered with credentials that genuinely cannot
      write), and defined `ValidatedQuery` with its grant, four phases before
      the DAL that will hold it
- [x] **B-013** TLS to a customer database is a setting with a safe default
      — pulled forward from Phase 12 by the owner on 2026-08-12. Not a work
      package: a backlog item taken between WP3.2 and WP3.3 so the SQL Server
      connector is written against the settled policy instead of retrofitted.
      See DECISIONS **D-011**; the residue (per-source CA material) is **B-015**
- [ ] WP3.3 SQL Server connector + compose profile + dialect tests
- [ ] WP3.4 Data sources screen (register, test, rotate, remove)     ← gate PR
      — added 2026-08-12 from **B-012**, accepted by the owner: the phase's exit
      criterion said "registered via the API", which meant a curl command for
      the first thing a new organization must do. The gate demo is now in the
      browser. WP3.3 keeps its work and hands the gate marker to WP3.4.
- [ ] GATE: both seed DBs registered **from the browser**; creds never echoed;
      read-only verified; a wrong password fails with a sanitized message;
      a Reader sees no admin controls; user sign-off

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

Phase 3 / **WP3.3 — SQL Server connector** (`p3.3-mssql-connector`). Read plan
§6 Phase 3 and architecture Part 3, 5.1. It brings:

- pyodbc + msodbcsql18 in the API image, wrapped in `asyncio.to_thread` behind
  the same async protocol WP3.2 defined; the driver install goes in the
  Dockerfile and is the reason this WP touches the image at all.
- `connectors/sqlserver.py`: introspection from the `sys.*` views, capabilities
  declaring dialect `tsql` and `limit_syntax="top"`, and the same two-part
  read-only verification — privilege introspection (`HAS_PERMS_BY_NAME`, the
  `db_datawriter`/`db_owner` role memberships) plus one attempted write inside a
  rolled-back transaction, on a connection that is **not** read-only.
- Compose `mssql` profile (already present) + `ops/seed/seed_pizza_mssql.sql`,
  same schema and still no `order_items`; a smaller row count is fine.
- A path-filtered CI job with an mssql service, because that image is heavy.

What WP3.2 leaves for it, deliberately:

- `connectors/factory.py` refuses `mssql` today with a message naming this work
  package. Adding the connector means adding one entry to `SUPPORTED_ENGINES`
  and removing one line from `_NOT_YET`.
- The `Connector` protocol is complete for what exists: `capabilities`,
  `test_connection`, the three `list_*` calls, `execute` and `aclose`. Arch 5.1
  also lists `sample`, `profile` and `explain`; those arrive with the profiler
  in Phase 4 and the DAL in Phase 5, each with the caller that needs it.
- `Caps.statement_timeout_mechanism` exists precisely because SQL Server does
  not have `SET statement_timeout`: it needs the driver's query timeout instead.
  WP3.3 is the first code to read that field rather than assume Postgres.
- The read-only *shape* of verification is settled and should not be
  re-invented: never ask a read-only session to prove the credentials are
  read-only, because it proves only that the session setting works.
- The TLS policy is settled too (B-013, D-011). The connector receives a
  `tls_mode` and does not choose one, so WP3.3's job is the ODBC mapping —
  `Encrypt` and `TrustServerCertificate` — plus reading back what was actually
  negotiated (`sys.dm_exec_connections.encrypt_option`) into the same
  `TlsStatus`. Note that the compose SQL Server *does* serve a self-signed
  certificate, so unlike Postgres its local connection will really be encrypted
  and unverified; the evidence line must say so rather than round it up.

Then **WP3.4** (`p3.4-datasources-ui`) closes the phase with the browser screen
and the gate demo — see the Phase 3 checklist above and plan §6 WP3.4.

## Notes

- **`readonly_verified` is a claim, so it is earned.** It is false until this
  service has evidence: the engine's privilege catalog says the role cannot
  write, *and* an attempted write on a connection with normal session settings
  was refused. Asking a read-only session to fail a write proves only that we
  can configure our own driver, which is why the probe opens its own connection.
  A rotation or a change of address retires the verification — a green tick must
  describe the credentials the row holds now.

- **Encryption to a customer database is decided by its address, not by the
  connector.** `TLS_MODE` (which accepts only modes that encrypt) covers every
  host that is not loopback or listed in `TLS_LOCAL_HOSTS`; compose declares its
  own two databases local because they serve no certificate that any name could
  match. A data source may tighten its mode and never loosen it, in prod nothing
  counts as local, and a test reports what the *server* says happened — the
  local stack answers "prefer — this connection is NOT encrypted", which is the
  truth and is meant to be visible. `require` encrypts without checking the
  certificate; only the verify modes authenticate the far end (D-011).

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
