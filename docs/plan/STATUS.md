# STATUS — data-agent build

Current position: **Phases 0–4 complete and signed off.** Phase 5 in progress:
                  **B-019**, **B-016** and **WP5.1** done
Next step:        `p5.2-dal-executor` — the half of the DAL that touches data.
                  Still the security boundary: human review on every PR. The
                  brief is at the end of this file, and WP5.1's output is the
                  input, so read `dal/validator.py`'s docstring first.
Merge policy: ASK
Blocked on user: nothing
Last updated: 2026-08-13 by Claude Code

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
- [x] WP3.3 SQL Server connector + compose profile + dialect tests
      — pyodbc behind the same async protocol, `sys.*` introspection, and the
      same two-part read-only verification. Both dialects' templates live in one
      `introspection` module so `SANCTIONED_VALIDATORS` stays at two names.
      Raised **B-016**: the `api` job has no SQL Server, so it measures this
      connector at 27% and the total at 88% — harmless until §4.4's ratchet
- [x] WP3.4 Data sources screen (register, test, rotate, remove)     ← gate PR
      — added 2026-08-12 from **B-012**, accepted by the owner: the phase's exit
      criterion said "registered via the API", which meant a curl command for
      the first thing a new organization must do. The gate demo is now in the
      browser. WP3.3 keeps its work and hands the gate marker to WP3.4.
      Also closed **B-008**: a Reader is no longer offered controls the API will
      refuse, here and on the members screen
- [x] GATE: both seed DBs registered **from the browser**; creds never echoed;
      read-only verified; a wrong password fails with a sanitized message;
      a Reader sees no admin controls; user sign-off
      — signed off 2026-08-12. Both demo databases registered through the screen
      and left `verified` in `data_sources`: `seed-pizza-pg:5432/pizza` and
      `mssql:1433/pizza`, each connecting as `pizza_readonly`. The wrong-password
      source was registered, failed with a sanitized message, and removed again.
      Run as the Gmail account, which had to be promoted to Admin directly in the
      database first — see **B-017**

## Phase 4 — Discovery & catalog (M4)
- [x] WP4.1 Schema discovery → catalog tables + refresh + incremental hash
      — revision 0007 and four tenant tables; **DECISIONS D-012** settles what a
      snapshot is, and a refresh that finds no change writes nothing at all,
      which `test_a_refresh_that_finds_no_change_writes_nothing` asserts by
      counting rows. Verified against both live seeded databases: 6 tables /
      33 columns / 4 joins from PostgreSQL in 0.14s, 7 / 36 / 4 from SQL Server
      in 0.22s, and **no `orders → menu_items` edge on either**
- [x] WP4.2 Profiler (budgets/timeouts) + sensitivity classifier + auto-mask
      — revision 0008; **DECISIONS D-013** splits a *profile* (belongs to a
      snapshot) from a *policy* (belongs to a column by name, and survives every
      refresh). Samples are masked on the way in, proved by a test that dumps
      the whole platform database as text and greps it for the planted
      addresses. Live: `customers.email`, `customers.phone`,
      `customers.full_name` and `staff.full_name` auto-masked on both engines in
      ~0.2s — and profiling the real database is what caught a classifier bug
      that read every ISO date as a phone number
- [x] WP4.3 Table cards + search + catalog APIs/UI + column policy  ← gate PR
      — cards are prose built from catalog rows only, so their examples are the
      masked ones; `card_tsv` is a **generated** column, so the index cannot
      disagree with the text it indexes. Search is lexical (**B-018** carries
      embeddings). Reading the first real card caught two false numbers: a row
      count taken from the sample cap, and PostgreSQL's `reltuples = -1`
      clamped to zero — "unknown" now stays unknown
- [x] GATE: pizza DB discovered ≤2 min; email auto-masked; user sign-off
      — signed off 2026-08-13, and the platform database holds the whole of it.
      Both demo sources carry an **active version 1** catalog, profiled to
      `complete`: 33 columns from PostgreSQL and 36 from SQL Server, **4 of each
      flagged sensitive**. Discovery took 0.14s and 0.22s against a two-minute
      budget.
      The eight `column_policies` rows are the best evidence in the phase,
      because they show both halves of D-013 at once. Seven are `mask` decided
      **automatically** — `customers.full_name`, `customers.phone` and
      `staff.full_name` on both sources, and `customers.email` on the SQL Server
      one — with nobody having reviewed them. The eighth is `customers.email` on
      the PostgreSQL source, `mask` decided by **a person** during the demo and
      still standing after a later profiling pass, which is the thing that must
      never be silently overwritten.

## Phase 5 — DAL + SQL policy engine (M5)  ⚠ human review on every PR
- [x] **B-019** CI keeps STATUS.md from losing its checklist
      — raised and taken on 2026-08-13, before any Phase 5 code. `hygiene` now
      checks the header fields, one heading per phase 0–12 with a GATE line
      each, that the file did not lose a fifth of its lines against the base
      branch, and that nothing already `[x]` came back as anything else.
      Replayed against #24, the PR that gutted this file: 43 findings, where
      today it went green. The guard has a `--selftest` that CI runs first, so
      a check that has stopped catching anything fails instead of passing
      everything
- [x] **B-016** Combine coverage across the `api` and `mssql` jobs
      — each job now keeps its own `.coverage.<shard>`, uploads it, and a
      `coverage` job combines them into the number CI reports, with plan §4.4's
      overall floor of 70 applied there rather than in a job that can only see
      half the suite. A run with no SQL Server shard says so in the log instead
      of quietly reporting a total that is six points low. Cleared before WP5.1
      so WP5.3's `--cov-fail-under=90` on `dal/` lands on a true measurement
- [x] WP5.1 sqlglot validator + policy pipeline + catalog grounding
      — `dal/validator.py` enforces architecture 7.1's pipeline in order, and
      `dal/policy.py` loads what it judges against (catalog + column policies +
      `Caps`) on a 30-second, org-keyed cache that the policy setter drops on
      write, so a denial takes effect on the next query rather than in half a
      minute. **DECISIONS D-015**: a function sqlglot cannot type is refused for
      being unrecognised — the deny list only buys a clearer message — and two
      ceilings (20,000 characters, 50 levels of nesting) close a hole found in
      testing, where 300 nested parentheses raised a bare `RecursionError`
      before any rule ran. 187 tests, both dialects, `dal/` at **97%** against
      WP5.3's 90% gate. Verified against the two live demo catalogs: the same
      six statements refused on both engines with the same codes, `SELECT *`
      over `customers` expanded and its three masked columns recorded, and
      nothing in `dal/` branching on an engine name. Raised **B-020**
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
      — carries **B-017**: recovery when an org has no Admin who can sign in
      (owner's call 2026-08-12, moved forward from Phase 12)
- [ ] GATE: trend question → rendered chart; smoke green; sign-off

## Phase 12 — Azure deploy + hardening (M12)  ⚠ human review on every PR
- [ ] WP12.1 Bicep modules + env params + what-if in CI
- [ ] WP12.2 OIDC deploy workflow → dev env + Key Vault backend + smoke
- [ ] WP12.3 Observability wiring + quotas hard-stop + alerts
- [ ] WP12.4 Prod env + ASVS-lite checklist + restore drill + v1.0 tag ← gate
- [ ] GATE: arch Part 14 acceptance; nightly evals on; user sign-off

---

## Next step

Phase 5 / **WP5.2 — executor + masking + audit** (`p5.2-dal-executor`).
**⚠ Still the security boundary.** Human review on every PR. WP5.1 decided what
may run; this is the half that opens a connection, and it is where the promises
made in `readonly_verified` and in the column policies are either kept or not.

Build (plan §6 WP5.2, architecture Part 7.1 steps 4–7 and Part 10.1):

- A migration for `query_executions` and `result_artifacts` — **both are tenant
  tables**, so both need an RLS policy, a line in `TENANT_TABLES` and an
  extension of the rls_proof suite, in this same PR.
- `dal/executor.py`: takes a `Validated`, clamps or injects a LIMIT on the
  outermost SELECT (default 1 000, hard cap per policy — `Caps.limit_syntax`
  already says whether that is `LIMIT` or `TOP`), executes through the connector
  with a statement timeout and a byte guard, and normalises to a `ResultFrame`.
- `dal/masking.py`: applies the `mask` policies to the values coming back.
  `Validated.masked` already names the columns; that list is the input, not
  something to re-derive from the catalog.
- `dal/audit_hook.py`: a `query_executions` row and an `audit_log` row for every
  attempt — success, failure **and** refusal. `Validated.tables` /
  `.columns` / `.touches_sensitive` are what those rows are made of, which is
  why the validator returns them.
- The single entry point `DAL.run(org_ctx, ds_id, sql) -> Execution`. Internal
  only: no public route in this WP.

What WP5.1 hands it, and what it must not re-do:

- **`validate(sql, source=…) -> Validated`.** `query` is the `ValidatedQuery` a
  connector accepts; `tables`, `columns` and `masked` are the analysis. The
  canonical SQL is already fully qualified and star-expanded, so the executor
  knows its result columns before it runs anything.
- **`source_policy(org_id, ds_id)`** loads catalog + policies + `Caps` on a
  30-second org-keyed cache. Use it; do not read `catalog_columns` again.
- **Refusals are `PolicyViolation`, and only that.** `validate` raises nothing
  else, including on input built to blow the stack — so the executor's error
  handling has exactly two shapes to think about: a violation, and a
  `ConnectorError` from the far end.
- **Masking of *results* has not happened.** Catalog samples were masked at
  write time (D-013), which says nothing about the rows a query returns. This is
  the WP where `customers.email` in an answer is `a***@d***.com` or is a leak.

WP5.3 is then the adversarial corpus per dialect, the property table from arch
7.5 transcribed as a test map, and the 90% gate — the Phase 5 gate PR.

## Notes

- **The validator is strict on purpose, and that has a running cost.** A
  function sqlglot cannot type is refused for being unrecognised (D-015), so an
  engine-specific function a real question needs will be refused until somebody
  adds it to `_ALLOWED_UNTYPED_FUNCTIONS` — deliberately, in a PR that says why.
  Phase 7 is where this will first be felt. The right response is a decision
  about that one function, never a widening of the rule.

- **This file is now checked by CI, and the check is on your side.** It protects
  the shape — the header fields, a heading per phase 0–12 with a GATE line each,
  no losing a fifth of the lines, and nothing that was `[x]` coming back as
  anything else. Growing it, rewording it and adding items are all free, so the
  only edit it refuses is the one nobody meant to make. `make check.status` runs
  it locally, against the base branch's copy, exactly as `hygiene` does (B-019).

- **Coverage is measured in pieces, and only the combined number is real.** The
  suite is split by which database a job can reach — `api` has Postgres, `mssql`
  has SQL Server, neither has both — so each writes its own shard and the
  `coverage` job combines them. Read the total there, never inside one job. If a
  future job runs part of the suite, it must upload a shard too, or the
  connectors it exercises will look untested (B-016).

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

- **The two engines are not symmetric, and the difference is in the code.**
  Postgres has `default_transaction_read_only`, so a write is refused by the
  session before privileges are consulted. SQL Server has no equivalent —
  `ApplicationIntent=ReadOnly` is about availability groups and ODBC's access
  mode is advisory — so `connectors/sqlserver.py` never commits instead:
  `autocommit` is off and every execution ends in a rollback in a `finally`.
  A write that reached it would be undone rather than refused, which is weaker,
  and is why `readonly_verified` carries more weight on that engine. The same
  asymmetry shows up in TLS: `sys.dm_exec_connections` needs VIEW SERVER STATE,
  which a read-only login does not have, so its encryption is reported by the
  driver and labelled as such rather than passed off as the server's word.

- **Customer credentials have exactly one home.** They go to the
  `SecretsProvider` and nowhere else: the platform database holds a
  `secret_ref`, responses have no field that could carry one, and audit rows
  record that a rotation happened rather than what it rotated to. Locally the
  backend is a Fernet-encrypted file under `ops/.secrets/` (D-001) whose key
  comes from `LOCAL_SECRETS_KEY` — `make secrets.key` prints one, and a
  production build refuses to start with this backend at all.

- **A control nobody may use is worse than no control** (B-008, closed in
  WP3.4). Screens read the caller's role from `/v1/me` and hide what the API
  would refuse, failing closed while the role is unknown. This decides what to
  *render* and is not a permission check: the guard is server-side, and it still
  refuses and audits regardless of what the browser believes.

- **`ops/scripts/set_role.sh` is an operator escape hatch, not a feature.**
  Roles change through the API, which audits every one and refuses to let the
  last Admin demote themselves. This script edits `org_memberships` directly,
  for the case the API cannot help with: nobody who can sign in holds Admin —
  an identity-provider problem, not an authorization one. Added 2026-08-12 when
  the Entra External ID account that created the demo org (`sourabh@rereed.com`)
  stopped being findable at sign-in and the Phase 3 gate had no Admin. It writes
  its own `member.role_changed` row with a **null actor**, because "someone
  edited the database" is the honest description. If it is ever reached for
  anything but a locked-out tenant, that is a missing product feature.

- **UI follows docs/design.md.** Tokens live in `apps/web/src/app/globals.css`;
  primitives in `src/components/ui/`. A raw hex value anywhere else is a bug,
  and adding a component library needs a DECISIONS entry first.

- **The local demo environment, as this session left it.** Five containers up
  (`platform-pg`, `seed-pizza-pg`, `mssql`, `api`, `web`); platform database at
  revision **0009**; the demo organization `ebfe8139-…` holds two verified data
  sources, each with an active version 1 catalog, and one hand-set column policy.
  `make up && make seed` and `make up.mssql && make seed.mssql` rebuild the
  fixtures from nothing; `make db.setup` brings a fresh database to head.
  `ops/scripts/set_role.sh` is the escape hatch that exists because the Entra
  account which created that organization can no longer sign in (**B-017**).

- **A card is prose, and its numbers must be true.** The first card built from
  the real pizza database said "about 5,000 rows" about a 71,798-row table,
  because the row count came from the sampling cap; the fix took it from the
  engine's own estimate, and taught it that PostgreSQL's `reltuples = -1` means
  *unknown* rather than zero. A card is read by something that cannot tell a
  wrong number from a right one, so every figure in one is either the engine's
  or absent.

- **Masking happens on the way in, and a policy outlives the catalog** (D-013).
  A sample that reaches `catalog_columns` is already masked, so there is no
  unmasked original anywhere in the platform database to leak later. What an
  Admin decided lives in `column_policies`, keyed by column *name*, and
  discovery never touches it — a refresh that reset somebody's masking decision
  would be a leak caused by a routine operation, with nothing failing to draw
  attention to it.

- **A catalog is a snapshot, and a snapshot is only made when something
  changed** (D-012). One active snapshot per data source, enforced by a partial
  unique index rather than by remembering; the previous one is superseded and
  kept, because a run that started against it is entitled to finish against it.
  A crawl whose `structural_hash` values all match writes no rows at all — which
  is what makes the nightly refresh WP4.2 will want cheap enough to have.

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
