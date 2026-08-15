# STATUS — data-agent build

Current position: **Phases 0–5 signed off. Phase 6 merged, its gate partially
                  met and deliberately unticked. Phase 7 is one work package
                  from its gate:** WP7.1 (#36), WP7.2a (#37), WP7.2b (#38),
                  WP7.2c (#39), B-040 (#40) and B-039 (#41) are merged, and
                  **WP7.3a is open for review**. WP7.3 was split in two (plan
                  §1.1): 7.3a is the API the UI is written against — a
                  conversation names its data source (D-022, revision 0014), the
                  evidence route that opens a citation (B-034), and the phase's
                  end-to-end test. **The whole path now works over HTTP against a
                  real database, with the model as the only stub.**
Next step:        Phase 7 / **WP7.3b** (`p7.3b-chat-ui`) — the chat UI.
                  **This is the Phase 7 gate PR**, so it ends with a manual test
                  script. Everything it needs from the API exists as of 7.3a;
                  the build spec is in the "Next step" section near the end of
                  this file.
Merge policy: ASK
Blocked on user: WP7.3a is open and MERGE_POLICY is ASK, so 7.3b waits on that
                 review — it is built directly on 7.3a's routes. An Anthropic API
                 key would close B-029 and the Phase 6 gate; it blocks no Phase 7
                 work.
Last updated: 2026-08-15 by Claude Code (WP7.3a)

---

## ⚠ Session-end handoff — read this before starting anything

This session took Phase 7 from an empty schema to a working product: WP7.1
through WP7.2c, plus two P1 backlog items the work itself uncovered. It ended
deliberately, with nothing in flight — no open PR, no branch, no dirty tree.

1. **Session ritual (plan §7.1) as normal.** `git fetch --all`, `gh pr list`.
   Both should be quiet. `main` is at #41.
2. **Both of WP7.3's non-UI decisions are now made, in WP7.3a.** The evidence
   route exists (**B-034**, closed) and a conversation names its data source
   (**D-022**), so the demo org's two sources are no longer a blocker. What is
   left of WP7.3 is UI, and it is **WP7.3b**.
3. **WP7.3b is the gate PR**, so it ends with a **manual test script**: numbered
   steps, what the user should see at each, and the failure case. That is a hard
   rule (CLAUDE.md), and the Phase 7 gate is *"July-orders question answered in
   the UI with a real citation"* — so the script has to walk the browser.
4. **A new route directory under `apps/web/src/app/` needs the web container
   restarted.** The bind mount delivers the files; `next dev` does not notice a
   directory that appeared after it started, and serves a 404 that looks exactly
   like a routing bug. This has cost time before.
5. **Two P1 items are closed but their lessons are live.** No test may call a
   real provider (**B-040**) — keep the e2e on the FakeLLM, and if a new test
   ever trips the guard, the guard is right. And a table is findable by its own
   name (**B-039**) — search now works for every table rather than for the lucky
   ones, so build against it with confidence.
6. **Two P1 items remain open, and neither blocks WP7.3.** **B-029** — a second
   real provider — is what closes the Phase 6 gate, and needs an Anthropic key
   from the owner. **B-005** — the seed dataset's fixed end date — blocks the
   *start of Phase 9*, not Phase 7, and is the one to raise before evals are
   written against a window that drifts.

One trap in this very file, which cost a red CI run to find. **A backlog id that
appears as a checkbox in two phases is one key to the STATUS guard, and the last
occurrence wins.** B-039 was `[x]` in Phase 7 and `[ ]` in Phase 8's "must be
closed before this phase starts" line, so the guard correctly read it as having
been un-ticked and failed every PR raised after B-039 merged. Both lines now say
`[x]`. **`B-005` in Phase 9 has exactly the same shape**, so when it is closed,
tick *both* lines in the same PR.

Two working habits this session earned the hard way, both worth keeping:

- **Run `ruff check . --no-cache` before pushing.** A warm ruff cache passed
  locally while CI failed on the same tree, after a file moved and its import
  classification changed with it.
- **A patch script must assert its edit matched before writing.** One printed
  "ok" having changed nothing, which is the exact failure the note below this
  section warns about. Prefer an edit that fails loudly.

## Standing notes for a new session

1. **Do not re-verify the LLM provider by calling it.** The live smoke spends
   real money. It has already been run and its evidence is recorded under Phase
   6 below. Re-run it only when something about the provider actually changes.
2. **The Phase 6 gate is not closed.** It is partially met and its checkbox is
   deliberately empty. Do not tick it, and do not reword the criterion, when
   Phase 7 work happens to pass — closing it needs a second real provider
   (**B-029**, P1). Phase 6 cannot be signed off before then; Phase 7 proceeds
   regardless, because nothing in it needs two providers.
3. **Local machine state that a fresh clone will not have.** `.env` here carries
   a working `OPENAI_API_KEY` plus `LLM_PROVIDERS`/`LLM_MODELS`/`LLM_ROLE_MAP`/
   `LLM_PRICES`/`LLM_RUN_COST_LIMIT_USD`. `ANTHROPIC_API_KEY` exists but is
   **empty**. `.env.example` documents all of it without the secrets. A new
   machine needs `make env` and a key before `make llm.smoke` will do anything.
4. **When the Anthropic key arrives**, verify its three model ids against the
   account with `GET /v1/models` *before* writing them into `LLM_MODELS` — the
   same check done for OpenAI. A pricing page says what exists, not what a key
   may call. That habit is B-027, still unautomated.

One process note worth carrying forward: a patch script that reports success
without asserting its edit matched will lie to you. This file's header silently
went un-updated for exactly that reason, and was caught only by reading it back.
Prefer an edit that fails loudly over one that prints "done".

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
- [x] WP5.2a Executor (read-only, timeouts, LIMIT) + result masking
      — WP5.2 split in two (plan §1.1: the whole was ~1,100 lines against a
      ~600 target, in the phase where review quality matters most). The split
      is by *what could go wrong*: this half is everything that decides what a
      caller receives, so no version of `main` ever returns an unmasked value
      through the DAL; **WP5.2b** is everything that records it, landing with
      the migration it writes to.
      The row cap is applied twice — written into the SQL so the engine stops
      early, and again as the fetch bound — and the validator is what emits it,
      because emitting SQL is what holding the grant means. Masking works from
      a per-position `Projection` map rather than from column names, so
      `SELECT c.email, s.email` is unambiguous; `UPPER(email)` is masked and
      `COUNT(email)` is not, which is the distinction that makes `mask` more
      useful than `deny`. An Admin's `mask_type` is now honoured — `browse` was
      dropping it. 84 DAL tests, `dal/` at **98%**
- [x] WP5.2b Query execution records + artifacts + audit hook
      — revision **0010**: `query_executions` and `result_artifacts`, both
      tenant tables, both with an RLS policy, both in `TENANT_TABLES`, and the
      rls_proof suite extended to seed and forge rows in each. `status` has
      three values, not two: **refused** is the row this half exists for, since
      a query that never reached an engine leaves no other trace anywhere — no
      connection, no server log, no latency graph. A CHECK constraint makes a
      refusal without a `violation_code` impossible.
      `dal.run` is the front door and records on every path, so there is no
      call that gets data without leaving a row. Artifacts go to an
      `ArtifactStore` (local files now, Blob in Phase 12 behind the same
      interface) whose keys are org-prefixed and checked, `..` included.
      Verified against the live pizza database through the container: real
      counts returned, **real customer emails masked** to `k***@e***.com`, all
      three outcomes recorded, and no unmasked value in either table or in any
      stored file. `dal/` at **97%**
- [x] WP5.3 Adversarial corpus per dialect + property tests + 90% gate ← gate PR
      — 64 corpus cases, **112 assertions across both dialects**, each naming
      the `ViolationCode` it must produce and each run through the executor with
      a connector that fails the test if it is asked to run anything: every case
      proves both that it was refused and that **nothing was sent**. Writing it
      corrected the corpus rather than the code in eight places, and corrected
      the code in two — `EXPLAIN ANALYZE` now says so instead of reporting a
      parse failure of a fragment nobody wrote, and a quoted `"EMAIL"` is
      reported as the unknown column it is rather than as `unresolvable`.
      Hypothesis covers what nobody thought of: every case mixture of a denied
      column, Cyrillic homoglyphs, and arbitrary identifiers — one of which
      corrected a property I had stated too broadly (`SELECT 0` is a literal,
      not an identifier). `test_property_table.py` transcribes arch 7.5 as a
      map from property to proof and **fails if a named test stops existing**.
      `make test.dal` gates `dal/` at 90%; it stands at **97%**. Adding that
      step also broke the combined coverage number — it wrote to the same data
      file as the full run and replaced the shard with a DAL-only one, taking
      the total from 96% to 63% — which **B-016's combine job caught on this
      very PR**. The gate now measures into its own file
- [x] GATE: arch Part 7.5 property table proven in tests; user sign-off
      — signed off 2026-08-13, and the sign-off included the two steps that
      matter most: the owner tampered with a corpus expectation and saw the case
      fail naming both codes, and renamed a test in the property map and saw the
      gate refuse to pass. The evidence was tested, not just read.
      What the phase leaves behind: one entry point (`dal.run`), 418 DAL tests,
      64 adversarial cases over both dialects, `dal/` at **97%** against a 90%
      gate that CI now enforces in its own step, and a `query_executions` row
      for every attempt — including the ones refused before any engine saw them.
      **B-019** and **B-016** were cleared first, and both earned their place
      during the phase: the STATUS guard now protects this very file, and the
      coverage combine caught the DAL gate clobbering the shard on the PR that
      introduced it.

## Phase 6 — LLM abstraction (M6)
- [x] WP6.1 LLMProvider protocol + FakeLLM + registry + usage metering
      — `llm/service.complete` is the front door and the whole design is that
      there is no other one: it resolves the role, calls the provider, meters,
      parses and repairs, and **no path spends tokens without writing a
      `usage_ledger` row** — including the failures and both halves of a repair.
      The same shape as `dal.run`, for the same reason.
      **DECISIONS D-017** (owner's call): OpenAI's own API is the primary
      provider and Anthropic the second; Azure OpenAI is deferred to Phase 12,
      and the architecture is edited to say so. **D-018**: roles are the
      architecture's six (`intake, observe, plan, sql, critic, compose`), and a
      role names a *tier*, not a model — which is why `usage_ledger` stores both.
      Model ids are configuration with **no defaults in code**: a stale default
      404s or bills for the wrong tier, so `LLM_MODELS` is required and
      resolution fails naming the provider and the missing tier.
      Revision **0011** adds `usage_ledger` — a tenant table, so an RLS policy,
      a `TENANT_TABLES` line and the rls_proof seed/forge pair, all in this PR.
      `cost_usd` is nullable and **null means unpriced, never free**, so a
      quota built on these rows cannot silently count an unpriced model as zero.
      68 new tests; the LLM package sits at 96–100% per module. Raised
      **B-025**–**B-028**
- [x] WP6.2 OpenAI + Anthropic impls + fallback + live smoke  ← gate PR
      — **OpenAI only.** `llm/anthropic.py` is not written; the owner's call on
      2026-08-14 was to ship the one provider whose credits expire soonest and
      carry the second-provider proof as **B-029** rather than hold the phase.
      The three model ids were verified against the live account with
      `GET /v1/models` *before* being written into configuration, because a
      pricing page is not proof that a key can use a model — that check is
      B-027's, done by hand here and still worth automating.
      `llm/openai.py` is thin on purpose (send, receive, report usage, set
      `retryable`); everything a second provider could come to disagree about
      lives outside it — `retry.py` (8.5's three attempts, jitter injected so
      the backoff is tested without a clock), `fallback.py` (walk the chain, and
      **only** on retryable failures — a 400 is our bug and would be our bug at
      the next provider too), `structured.py`, `meter.py`.
      **D-019**: a hard per-run spend ceiling (`LLM_RUN_COST_LIMIT_USD`) checked
      before each call against that run's own ledger rows, because the owner
      asked for eval and gate runs to be capped before real money was behind a
      real key. An unpriced model is **refused** under a ceiling rather than
      counted as zero — a ceiling that cannot see its spend is not one — and
      exhaustion raises its own error type so Phase 8 can compose from
      findings-so-far per 8.5 instead of apologising. B-025 still owns the
      org-level quotas.
      Verified live against the real account: the request shape built from the
      provider's docs was accepted first time, structured output round-tripped
      into a pydantic model, and the call left a `usage_ledger` row costing
      **$0.000048**. 45 new tests; `llm/` at **95%**. Raised **B-029**–**B-031**
      and strengthened **B-028** with two more sightings
- [ ] GATE: same suite passes on both providers; tokens metered; sign-off
      — **PARTIALLY MET, and deliberately left unticked.** Proven: the contract
      suite passes against the FakeLLM in CI and against **OpenAI** live; tokens
      are metered on every path including failures and both halves of a repair;
      an injected 429 walks the chain to a second provider and a 400 does not;
      keys arrive only through config and never appear in a repr, an error or a
      request body. Not proven: *"both providers"* — there is one. The criterion
      is unchanged and this box stays empty until a second real API passes the
      same suite (**B-029**, P1). What one provider cannot demonstrate is the
      only thing two providers are for: that the `LLMProvider` shape survives
      contact with an API that disagrees with the first about system prompts,
      structured output and usage reporting

## Phase 7 — Single-shot Q&A (M7)
- [x] WP7.1 Conversations/runs/events schema + routes + run status
      — revision **0012**: `conversations`, `agent_runs`, `messages`,
      `agent_events`, `findings`. Five tenant tables, so five RLS policies, five
      `TENANT_TABLES` lines and five seed/forge pairs in `test_rls_proof.py` —
      the rule has now bitten four times and the guard has caught it every time.
      `agent_events` is **append-only by grant**, the same lock `audit_log` has
      carried since revision 0002, because 10.3 makes it the single source of
      truth for how an answer was reached: a trace that could be edited
      afterwards would be a story rather than a record.
      **This is the WP that gave `query_executions.run_id` and
      `usage_ledger.run_id` their foreign key**, five and six revisions after the
      columns appeared. `ON DELETE SET NULL` on both, per D-016: a row that
      records an act outlives the thing it was about. The cost is stated in the
      migration and was paid on this machine — the five ledger rows written by
      WP6.1's and WP6.2's smoke scripts named runs that never existed, so their
      `run_id` is now NULL. Every row survived, costs intact, **including the
      real `$0.000048` OpenAI call**; what was cleared was a pointer to nothing.
      **D-020**: a run is ordered by when it was *asked* (`created_at`), not by
      when it started — a queued run has no `started_at`, and those are precisely
      the runs a user is watching. Architecture 10.1 edited to match, along with
      `messages.idempotency_key` and `findings.created_at`.
      `seq` is gap-free per run, assigned under the run row's own
      `SELECT … FOR UPDATE`, which is what makes `?after=seq` a complete replay
      contract rather than a hopeful one — proved by three concurrent writers
      coming out 1, 2, 3 instead of one of them losing to the unique index.
      A conversation belongs to the person who started it (6.2 grants "view
      **own** conversations"), enforced in the service layer where RLS cannot
      help, and refused as 404 rather than 403. The role matrix gained its seven
      new routes, all `allow` for every role, with the probe now formatted per
      role so an ownership 404 can never be recorded as a role decision.
      40 new tests; `runs/` at **99%**. Raised **B-034**–**B-037**.
      **Not split, against plan §1.1's ~600-line target, and recorded here
      rather than passed over.** The three new modules are ~1,230 lines and 349
      statements — this codebase's comment density, not unusual density of
      logic. The split that was considered (schema + `EventWriter`, then service
      + routes) was rejected because it separates a schema from the only code
      that proves it works: the first half would ship five tables, an
      append-only grant and a data-nulling migration with no consumer, and the
      second half would still be over the target. The two parts a reviewer
      should spend time on are small and self-contained — revision 0012, and the
      ownership check in `runs/service.py`. Say so if you would rather have had
      two PRs; the next WP can be cut differently
- [x] WP7.2a Context builder + tool registry + core tools
      — WP7.2 split in two (plan §1.1), **by risk, at the owner's direction**:
      this half is everything that decides *what the model is given and what it
      may call*, WP7.2b is everything that decides *what it may do with the
      answer*. The failure modes are different — 7.2a leaks or misleads, 7.2b
      loops or spends — and they are worth reviewing separately.
      `agent/context.py` builds architecture 4.8's six layers. L4 is framed as
      **data, not instructions**, and sits below the rules, because 7.4 assumes a
      customer's column comment may be hostile. Truncation is deterministic and
      its order is a decision: cards shrink to headlines **before** any is
      dropped, since a model that cannot see a table will not ask about it; L0
      and L5 are never candidates, and a budget too small for those raises rather
      than quietly losing a safety rule.
      `agent/tools/` is 4.6's registry doing 4.6's five jobs. A tool the caller
      may not use is **indistinguishable from one that does not exist** — same
      code, same message — because "exists, but not for you" is a fact worth
      withholding from a prompt that may be probing. Arguments are validated at
      the gate, never in a handler that could forget. Every result is an
      envelope including the failures, so the runner has one shape at every call
      site rather than a value and an exception.
      `run_sql` is deliberately thin and has no parameter that softens the DAL.
      A hallucinated column comes back as a *repairable* envelope carrying the
      violation's own code — the path WP7.2b's single repair attempt exists for —
      and the refusal is on `query_executions` before anyone decides whether to
      retry.
      **`dal/` was widened, and needs human review:** `Execution` gained
      `execution_id`, filled by `dal.run` after it records. A finding may only
      cite a real `query_executions` row (arch 4.2), and the executor cannot
      supply the id because it does not write the row. The alternative was the
      agent re-finding its own row by hash, which is a guess dressed as a
      reference.
      45 new tests; `agent/` at **95%**. Raised **B-038** and **B-039** — the
      second is **P1** and was found by this suite
- [x] WP7.2b Planner-lite + runner + repair-or-refuse
      — WP7.2's second third. `agent/planner.py` is one `sql`-role call with a
      closed schema; `agent/runner.py` is the single-shot state machine:
      context -> plan -> `run_sql` -> **at most one** repair -> finalize, with a
      hard cap of 3 model calls enforced in the controller and never in the
      prompt (4.4).
      Four rules, each against a specific way an agent goes wrong. **The repair
      happens once and only for what rewriting can fix** — `ToolResult.repairable`
      is the flag and the runner never second-guesses it, so a database that is
      down does not buy a second billed call. **A refusal is an ending, not a
      failure**: a run that could not answer *completes* with `answered=false`,
      and `failed` is reserved for the platform breaking — conflating them would
      hide real outages among honest refusals. **Citations are verified before
      they are stored**: the model may only cite executions this run produced,
      anything else is dropped and the trace says so, because an unresolvable
      citation looks like evidence while being none. **Every exit ends the run
      exactly once**, in a `finally`, because a dangling run has no symptom until
      somebody notices a page spinning.
      State is checkpointed at every step boundary per architecture 0.2.4, so an
      interrupted run is explicable and Phase 8 has something to resume from.
      The runner takes ids and never touches a request — the constraint that
      keeps the V1.5 move behind a worker free.
      17 new tests; `runner.py` at **99%**, `agent/` at **96%**. `fake_llm` and
      `llm_fixture` moved to the tests root so the agent suite and the LLM suite
      share one definition rather than drifting apart
- [x] WP7.2c Background execution + orphan sweep + route wiring + live smoke
      — a queued run now actually starts. `POST …/messages` schedules an
      **in-process background task** and still answers 202 immediately
      (architecture 0.2.4, owner's decision 2026-08-15). No queue.
      **The data source is never guessed at.** One registered source is used;
      anything else refuses and names the choices, because a silently wrong
      database produces a confident, correctly-cited answer about somebody
      else's data — the worst output this product can generate, and the only one
      with nothing about it that looks wrong. The demo org has two sources, so
      asking there refuses until a later WP lets the conversation carry one;
      `scripts/agent_smoke.py` takes `--source` for exactly that reason.
      **A restart leaves no run claiming to run.** The lifespan sweeps anything
      left `queued`, `running` or `validating` to **`interrupted`** — never
      `failed` — with a reason that says the service restarted and that nothing
      was wrong with the question, so nobody goes hunting for a bug in their SQL.
      A per-org semaphore (2, arch 8.4) stops questions being a way to spawn
      unbounded tasks, and the scheduler keeps strong references to its tasks
      because asyncio does not — a collected task is a run that simply stops.
      19 new tests, including the first true end-to-end: **a question over HTTP
      returns 202 and the answer arrives on the run**. Raised **B-040 (P1)**,
      which this suite found by spending the owner's money
- [x] **B-040 (P1)** No test may call a real model, and **B-032** with it
      — taken before B-039 at the owner's direction: a suite that can spend real
      money is not something to carry into Phase 9's eval harness, which will run
      many more of these. A session-scoped guard wraps `registry.get_provider`
      for the whole run. A non-stub provider **raises**, so nothing leaves the
      machine, *and* is recorded so a per-test check fails the test afterwards.
      The second half is the one that matters: replaying the original failure
      with the workaround removed shows the agent runner **swallowing the raise
      into a `failed` run** — a guard that only raised would have gone green
      having proved nothing. Opt-out is `@pytest.mark.live_provider`, carried by
      exactly one test that wants a non-stub on purpose. Four tests exercise the
      guard itself, because a guard that has never fired is one nobody has tested
- [x] **B-039 (P1)** Table cards are findable by their own name
      — pulled into Phase 7 by the owner on 2026-08-15 and closed before Phase 8,
      because `menu_items` was one of the tables that could not be found and it
      is the table Phase 8's flagship refusal demo is about: the M8 gate would
      have passed while demonstrating nothing, which is worse than failing.
      **The fix was smaller than the plan assumed, because the tokenizer was
      measured rather than guessed at.** A *bare* `menu_items` already splits to
      `'menu'`+`'item'` — PostgreSQL only makes a host token out of the
      *qualified* `public.menu_items`. So no underscore-splitting was needed:
      `build_card` opens `shops (public.shops) is a table …` and that is the
      whole change. Putting the name first also ranks it higher, since
      `ts_rank_cd` weighs proximity.
      Revision **0013** rewrites cards written before it — a guarded SQL
      transformation rather than a regeneration, because a migration that
      imports `build_card` stops meaning the same thing the moment that code
      moves on. `card_tsv` is generated, so writing `card_text` rebuilt the
      index, which is what revision 0009 introduced it for.
      Live: **6 of 13** findable by their own name became **13 of 13**, and
      "menu items" now returns exactly the two `menu_items` tables and nothing
      else. Three migration tests cover real rows, because an empty database
      proves nothing about a data migration
- [x] WP7.3a Conversation names its data source + evidence route + e2e
      — WP7.3 split in two (plan §1.1): the whole was well past the ~600-line
      target, and the split is by *what it is*: this half is the API the UI will
      be written against, the other half is the UI and the gate. The gate stays
      at the end where it belongs rather than landing in the first PR.
      **A conversation names the database it is about** (revision **0014**,
      DECISIONS **D-022**). WP7.2c made the scheduler refuse rather than guess
      with more than one source registered, which meant every question in the
      demo org refused — and the Phase 7 gate is a question answered in the
      browser. The column is on the *conversation*, not the message: a follow-up
      must reach the same source as the question it follows, or two answers in
      one thread come from two databases with nothing saying so. **The refusal is
      kept, not replaced** — what closed the ambiguity is a caller saying which
      database it means, never permission to guess, so a thread that names none
      still refuses and still lists the choices.
      The foreign key is **not** the tenant check: a constraint check does not
      consult row-level security, so another organization's source id satisfies
      the database perfectly well. The source is looked up through the org
      session and refused as 404, proved by a test that registers a second
      organization's source and hands its id to the first.
      **B-034 is closed**: `GET …/runs/{r}/executions/{q}` turns a citation into
      something a person can open — canonical SQL, tables and columns, row count,
      duration, and up to 50 already-masked sample rows. A **refused** execution
      has no artifact and answers with its violation code and the statement that
      earned it, rather than an empty result that would read as "your data has no
      answer". The execution is read **through** the run — `run_id` is in the
      WHERE clause, not just the path — so one belonging to another run is not
      found, and there is no second access rule to drift from the run's own.
      **The e2e is the piece with the most in it and the least stubbed.** A real
      conversation naming a real source, a real discovered catalog, the real
      validator and executor against real rows, the real routes, and the FakeLLM
      as the only substitution (B-040 — CI needs no key). The composing script is
      a **callable** that reads the execution id out of its own prompt and cites
      it, because a constant would prove the plumbing while assuming away the one
      property 4.2 rests on. Tampering with the expected count fails it on the
      database's own number, which is what separates this from a mock.
      13 new tests; `runs/routes.py` at **100%**, `runs/service.py` at **99%**,
      1046 passing overall at **94%**. **B-020 was decided rather than deferred
      by omission** — see its backlog row: the planner now asks for aliased
      projections, which is a mitigation, and the deterministic naming pass in
      `dal/` is still owed in its own reviewed PR.
      **On size, recorded rather than passed over** (plan §1.1). Hand-written
      source is **383 lines**, comfortably inside the ~600 target; tests are 815
      more, so the whole is over it. Splitting again was considered and rejected:
      the cut would be "data source" and "evidence route", and the e2e exercises
      both — it would have to go with one half and stop proving the other, which
      is the one test in this PR worth the most. Say so if you would rather have
      had two, and 7.3b can be cut differently
- [ ] WP7.3b Chat UI with citation + manual test script              ← gate PR
- [ ] GATE: "orders in July?" answered with citation; user sign-off

## Phase 8 — Research loop + trace (M8)
- [x] **B-039 (P1) was this phase's precondition, and it is closed** (#41)
      — the menu-items refusal demo is this phase's flagship, and the run would
      have refused because it could not *find* `menu_items` rather than because
      no join path exists. A gate that passes for the wrong reason is worse than
      one that fails. Taken in Phase 7 on the owner's call and verified live:
      "menu items" now returns exactly the two `menu_items` tables, so what this
      phase demonstrates will be the capability check rather than a search miss
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

Phase 7 / **WP7.3b — the chat UI** (`p7.3b-chat-ui`).
Plan §6 Phase 7, architecture Part 10.2 and 3.1. **This is the Phase 7 gate PR.**

Build:

- Web: a conversation page — message list, composer, run status, and an answer
  card with expandable evidence (the SQL, a rows preview, the execution). It
  polls `GET …/runs/{id}` and `GET …/runs/{id}/events`, and opens a citation with
  `GET …/runs/{r}/executions/{q}`. All three routes exist and are tested.
- Starting a conversation must let the user **pick the database** — the demo org
  has two, and a conversation that names none refuses by design (D-022). The
  picker is `GET …/data-sources`, which the data sources screen already uses, and
  the chosen id goes in the `POST …/conversations` body.
- **A manual test script** — numbered steps, what the user should see at each,
  and the failure case. Every gate PR ends with one (CLAUDE.md).

Three things worth knowing before drawing anything:

- **A new route directory under `apps/web/src/app/` needs the web container
  restarted** (`docker compose restart web`). The bind mount delivers the files;
  `next dev` does not notice a directory that appeared after it started, and
  serves a 404 that looks exactly like a routing bug. This has cost time twice.
- **`_col_1` may appear as a column name in the evidence panel** (**B-020**). The
  planner now asks the model to alias its projections, which handles the common
  case and is explicitly *not* the fix; the deterministic pass in `dal/` is still
  owed. If it shows up in the gate demo, that is the known item, not a new bug.
- **The refusal path is worth showing, not hiding.** A conversation that names no
  database, in an org with two, completes with a readable message listing them.
  It reads well and it is the honest half of D-022.

What WP7.3a hands it:

- **A conversation carries its data source** (D-022), returned as
  `data_source_id` and `data_source_name` on every conversation read.
- **A citation opens.** `GET …/runs/{r}/executions/{q}` returns the SQL, the
  tables and columns, the row count, the duration and up to 50 already-masked
  rows — and for a refused execution, the violation code instead.
- **The whole path is proved end to end on every commit**
  (`tests/agent/test_single_shot_e2e.py`), so the UI is built on behaviour that
  is already known to work rather than on hope.

What Phase 7 hands it:

- **The whole path works headlessly.** `POST …/messages` schedules a run, the
  runner answers or honestly refuses, and the answer, findings and trace are on
  the run. WP7.3 renders what is already there rather than adding behaviour.
- **`search_tables` now finds a table by its own name** (B-039), so the UI is
  built against search that works rather than search that worked for `orders`.
- **No test may call a real model** (B-040). Keep it that way: the e2e scripts
  the FakeLLM, and CI has no key.

What Phase 6 hands it:

- **`llm.complete` is the only way to call a model**, and it already resolves the
  role, enforces the run's spend ceiling, walks the provider chain, meters every
  attempt and repairs structured output once. Phase 7's planner calls it and does
  none of that itself.
- **A role is a tier, not a model** (D-018). Phase 7 code should name `sql`,
  `plan` and `compose`, never a model id. If a role needs to be cheaper, that is
  `LLM_ROLE_MAP` in an env file, not an edit to the agent.
- **The demo runs small.** `LLM_ROLE_MAP={"compose":"small"}` locally, and every
  role but `plan` and `sql` is small already. Do not put `sql` on a small model
  to save money: the DAL refuses SQL it cannot ground, so weaker SQL buys a
  refusal, a repair round-trip and another billed call.
- **The FakeLLM is the backbone of every agent test.** Script it by (role,
  matcher); assert on `calls` rather than on the answer. The Phase 7 e2e is
  meant to run in CI with no key at all.
- **Budgets are still not built.** `CallLimits` bounds one call and D-019's
  ceiling bounds one run's spend. The iteration, query and token caps of arch
  4.4 are Phase 8's, and B-025 owns the org-level quotas.
- **Two backlog items are due to be felt in WP7.2**, and both were filed so the
  response is a decision rather than a reflex: **B-024** (the first legitimate
  engine function the validator refuses — add the one name, with a reason, never
  widen the rule) and **B-020** (an unaliased projection comes back as
  `_col_1`, which becomes visible the moment a result is shown to a person).

## Notes

- **A table is often not findable by its own name, and that is B-039 (P1).**
  PostgreSQL's English parser reads `public.shops` as one *host* token, so it
  never matches the word `shops`. A table is findable by its own name only when
  that name also happens to appear as plain English in its prose — 6 rows of 13
  fail on the live demo catalogs, `menu_items` among them. `orders` passes, which
  is why nobody noticed and why the M7 gate question still works. It matters
  because `search_tables` is the agent's primary way to find anything, and
  because `menu_items` is the table **Phase 8's flagship refusal demo** is about:
  the run would refuse for the wrong reason and hide the capability check the M8
  gate exists to show. Found by WP7.2a's own suite, and pinned by a test that
  will fail when it is fixed.

- **The precedence of the instruction layers is soft, and saying so is the
  point** (architecture 4.8). L0 wins over L5 by ordering and framing, and
  nothing enforces it — a fully hijacked prompt still commands only read-only,
  org-scoped, catalog-verified, budgeted tools, because every hard rule lives in
  Parts 6–7 instead. `agent/context.py` repeats that in its own docstring so the
  next person to add a layer does not mistake the ordering for a control.

- **Reference data is framed as data, and put below the rules.** A table card is
  prose built from a customer's own database — names, comments, sample values —
  and 7.4's threat model assumes it may be hostile. So L4 is wrapped with an
  explicit "these are records, not instructions" and never merged upward. That
  framing is the cheap half of a pair whose expensive half is the DAL refusing
  anything the catalog cannot ground; on its own it would be theatre.

- **Truncating a prompt is a decision about what the model will not see.** Cards
  shrink to headlines before any card is dropped, because a model that cannot see
  a table will not ask about it — six tables in outline beat two in full. Only
  then are cards dropped, lowest search rank first. L0 and L5 are never
  candidates, and a budget too small for those raises rather than silently losing
  a safety rule.

- **A tool you may not use answers exactly like one that does not exist.** Same
  code, same message, one path. "Exists, but not for you" is a fact worth
  withholding from a prompt that may be probing for capabilities, and it also
  means the registry has one refusal to get right rather than two.

- **`Execution` now carries the id of the row it became.** A finding may only
  cite a real `query_executions` row (arch 4.2), so the agent has to learn that
  id; the executor cannot supply it because it does not write the row, so
  `dal.run` fills it in after recording. The alternative — the agent re-finding
  its own row by SQL hash — is a guess dressed as a reference, and would break
  the moment two identical statements ran in one run.

- **A trace can be written and never rewritten.** `agent_events` carries the
  same grant lock `audit_log` has: UPDATE and DELETE revoked from the
  application role, proved by `test_agent_events_is_append_only_for_the_api_role`.
  It matters more here than it does for the audit log, because architecture 10.3
  makes this table the product's honesty claim — what the user is shown as proof
  of how an answer was reached. A trace that could be edited afterwards would be
  a story with a timestamp on it.

- **`?after=seq` is a promise, and `seq` is what keeps it.** Gap-free and 1-based
  within a run, so "everything I have not seen" is answerable: a gap would make a
  reconnecting client wait forever for a number that never arrives, and a
  duplicate would make it skip a step. `UNIQUE (run_id, seq)` turns a race into
  an error rather than a mangled trace, and the run row's own `FOR UPDATE` is
  what stops the race happening — three concurrent writers come out 1, 2, 3
  instead of two of them colliding and one event being lost.

- **A conversation belongs to one person, and row-level security cannot enforce
  that.** Two members of the same organization share a tenant, so isolation here
  is the layer-2 ownership check architecture 6.2 describes, in
  `runs/service.py`. It refuses with **404 rather than 403**, because a member
  told "forbidden" has learned that a conversation with that id exists. The
  consequences are real and are **B-037**: no Admin oversight, no support path,
  and a departed user's conversations readable by nobody. That is the safe
  default, not necessarily the final answer.

- **A retried send is the same question.** `POST …/messages` requires an
  idempotency key (arch 10.2) and a repeat returns the run that already exists,
  proved both sequentially and with two sends genuinely in flight at once. With
  D-019's ceiling behind a real provider key, a double-tapped send button is
  otherwise a doubled bill.

- **The two dangling `run_id` columns finally point somewhere** (revision 0012).
  Phases 5 and 6 each wrote one with a comment saying a constraint pointing at a
  table that does not exist is not a constraint; the table now exists, and both
  are `ON DELETE SET NULL` per D-016 — a row that records an act outlives the
  thing it was about. Adding the constraint required nulling every `run_id` that
  named no run, which on this machine was the five rows the WP6.1 and WP6.2
  smoke scripts left behind. The rows and their costs are untouched; what went
  was a pointer to nothing. A fresh database sees a no-op.

- **The Phase 6 gate is partially met, and that is written down rather than
  smoothed over.** The criterion — *same suite passes on both providers* — is
  unchanged and its checkbox is empty. One provider is live and proven; the
  second is **B-029** and is P1. The distinction matters because a gate quietly
  reworded to match what was built stops being a gate: the whole point of two
  providers is to find out where the abstraction leaks, and one provider cannot
  report that.

- **A model id is verified against the account before it is configuration.**
  The three OpenAI ids were checked with `GET /v1/models` on the real key before
  being written to `.env.example`. A pricing page lists what exists; it does not
  say what your organization may call. Do the same for every id added later
  (B-027 wants this automated as a startup or health check).

- **Spending has a ceiling, and the ceiling refuses what it cannot count**
  (D-019). `LLM_RUN_COST_LIMIT_USD` is checked before each call against that
  run's own ledger rows. A model with no price in `LLM_PRICES` records a NULL
  cost, which would sail past every check — so under a ceiling such a call is
  refused rather than waved through. Unset the ceiling and nothing changes;
  that is the right default for a person asking one question and the wrong one
  for an eval sweep.

- **Test settings are hermetic on purpose, and both halves are load-bearing.**
  `build_settings` passes `_env_file=None` *and* every LLM field explicitly,
  because pydantic-settings **deep-merges** dict-typed fields across sources:
  an explicit `llm_role_map={}` is merged with whatever `.env` holds rather than
  replacing it, and `llm_models` silently gains every real provider a developer
  has configured. This was found the hard way — six tests changed their answers
  the moment real configuration landed in `.env`.

- **Nothing calls a model except through `llm.complete`.** It is the only entry
  point and it meters on every path — the answer, the provider failure, and both
  halves of a parse-then-repair. Calling a provider directly would spend a
  customer's tokens without a `usage_ledger` row, which is the LLM package's
  version of the rule `dal.run` holds for customer data. A later phase that needs
  something the door does not offer should widen the door.

- **A role names a tier, not a model** (D-018). Callers say `plan` or `critic`;
  configuration decides that `plan` is `strong` and that `strong` is some model
  id. Architecture 8.3 calls tiering the biggest cost lever in the product, and a
  lever is only a lever if it is in one place — so a model id anywhere outside
  `LLM_MODELS` is a bug. The ledger stores the role *and* the tier because the
  map between them changes, and "what did moving observe to small actually save"
  is a question about history.

- **This build ships no default model ids, and that is the design.** A model id
  compiled into a release is stale within months: it either 404s or bills for the
  wrong tier, and the second failure is silent. `LLM_MODELS` is required, and
  resolution fails naming the provider and the tier that is missing. The same
  reasoning applies to `LLM_PRICES` — an unpriced model records `cost_usd = NULL`,
  which means **unpriced, never free**, and a quota must treat it as unknown
  rather than summing zeros into a total someone enforces a limit from.

- **A FakeLLM in production would not fail — it would fabricate.** That is the
  worst failure mode this product has: confident, evidenced-looking answers with
  nothing behind them. `ProviderCaps.is_stub` marks such a provider and the
  registry refuses to hand one out in a production build or environment, the same
  pair the auth-mode and secrets-backend assertions use.

- **An audit row outlives the thing it is about** (D-016, owner's call). Deleting
  a data source sets `query_executions.data_source_id` to NULL rather than
  cascading the history away — so every reader must handle a null there, and a
  screen that groups by source needs an "unregistered" bucket. Catalog rows
  still cascade, because they describe a source rather than record an act.

- **Two guards built this phase have already paid for themselves, on the very
  PRs that introduced the problems they catch.** The STATUS check (B-019) now
  protects this file; the coverage combine (B-016) caught the new DAL gate
  overwriting the whole suite's shard, and said what the number was made of.
  Neither would have been noticed by a person reading a green tick. When a guard
  fires, read what it says before assuming it is wrong.

- **Nothing reads customer data except through `dal.run`.** It is the only
  entry point, and it records on every path — success, engine failure, and
  refusal. Calling `executor.execute` directly would get data without leaving a
  row, which is the one thing architecture 8.2 does not allow; if a later phase
  needs something the front door does not offer, widen the front door.

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

- **The local demo environment, as this session left it.** Four containers up
  (`platform-pg`, `seed-pizza-pg`, `api`, `web`) — **`mssql` is stopped**, so 20
  SQL Server tests skip locally; `make up.mssql` starts it and CI's `mssql` job
  runs them regardless. Platform database at revision **0013**. Rebuild the `api` image after any dependency change or the
  container and the host disagree about what exists.
  The demo organization `ebfe8139-…` now carries evidence from three phases, and
  none of it is fixtures — a reseed does not recreate any of it. Two verified
  data sources with an active version 1 catalog and one hand-set column policy
  (Phase 3–4). Four `query_executions` rows from WP5.2b: two `ok`, one
  `refused`, one `error`. Four `usage_ledger` rows from WP6.1's FakeLLM check —
  a priced call, both halves of a repair, and a provider failure costing `NULL`
  — **plus real rows from WP6.2's live OpenAI calls**, which cost actual money
  (a fraction of a cent) and are the only proof in the repository that the
  provider works end to end.
  `make up && make seed`, `make up.mssql && make seed.mssql` and `make db.setup`
  rebuild the fixtures from nothing; they do not rebuild the evidence above.
  **The demo org has no conversations, runs or events.** WP7.1's HTTP check wrote
  some and they were removed at the owner's request on 2026-08-15, along with the
  `alice`/`bob` test users — WP7.1's evidence was the schema, not rows. The four
  `query_executions` and five `usage_ledger` rows are untouched, and their
  `run_id` is NULL because revision 0012's foreign key cleared uuids that named
  no run.
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
