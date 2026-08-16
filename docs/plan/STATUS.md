# STATUS — data-agent build

Current position: **Phases 0–5, 7 and 8 signed off. Phase 6 merged, its gate
                  partially met and deliberately unticked.** The **Phase 8 gate
                  was signed off on 2026-08-16** (#52): the revenue-decline
                  question answered **$938.28** in two research steps against a
                  cap of eight, *"which menu items sell best?"* refused honestly
                  with zero queries, and a mid-run refresh replayed the whole
                  trace. A question is now investigated rather than answered in
                  one shot, bounded by ceilings the controller enforces, refused
                  deterministically when the schema cannot answer it, and visible
                  step by step in a record that cannot be rewritten.
                  A **second, real data source** was loaded and tried on
                  2026-08-16 — an F&B operator's 112k-row warehouse, not a
                  fixture this project designed. It found seven defects
                  (**B-054**…**B-060**), four of them P1/P2 and none visible to
                  a green suite. See "Second data source" below. Two of them
                  are already closed: **WP8.4** (#55) fixed the capability check
                  the hub table defeated, and **B-056** with it.
Next step:        **Phase 9 / WP9.2** (`p9.2-composer-evals`) — the composer's
                  citations and limitations, and eval harness v1 with the twenty
                  golden questions. **This is the Phase 9 gate PR.** The harness
                  pins `as_of` to 2026-08-16 (D-027) and reads expected answers
                  from `truths.json`, never hardcoding them. **WP9.1 is done**
                  (#58): a draft is judged before it becomes an answer, and a
                  wrong date range is caught without a model call. **B-059** is
                  the next P1 and lands in Phase 10, whose spec now requires the
                  semantic layer to *import* what a database already carries.
Merge policy: ASK
Blocked on user: Nothing blocks Phase 9. An Anthropic API key would close
                 **B-029 (P1)** and with it the Phase 6 gate; that blocks nothing
                 else.
Last updated: 2026-08-16 by Claude Code (WP9.1 — the hybrid critic)

---

## ⚠ Session-end handoff — read this before starting anything

**Phase 8 is built, walked and signed off.** Everything this session produced is
on `main`. Nothing is in flight: no branch, no open PR, no dirty tree.

### 1. Session ritual

`git fetch --all && git checkout main && git pull`, then `gh pr list` — both
quiet. The Phase 8 GATE line under Phase 8 below is `[x]` with its evidence, and
that line is the source of truth for where the build stands.

### 2. B-005 is closed (#57) — Phase 9 is unblocked

**Answered 2026-08-16, and it turned out to be a code task after all.**
Checking before deciding found the anchor problem was the product's, not the
fixture's — see **D-027**. What follows is the original entry, kept because the
reasoning that reframed it is worth reading before the next "this is just a
fixture chore" arrives.

**B-005 (P1)** was Phase 9's stated precondition and was filed as an **owner
decision, not a code task**. The seed dataset pins `END_DATE = 2026-07-31` for
reproducibility, so *"last full month"* stops meaning July as real time moves on,
and golden eval #2 is phrased relatively. Either pin every eval question to
absolute dates, or add a documented `SEED_END_DATE` override that CI fixes while
local demos track today. Ask first; an eval harness built on a window that
silently drifts is worth less than no harness.

**Tick both its checkboxes in the same PR** when it closes — Phase 9's line and
its BACKLOG row. The STATUS guard keys on the **last** occurrence of a backlog
id, and B-039 already cost a red CI run on every PR raised after it merged.

### 3. Re-prepping the demo — the stack is not left running

```
make down && make up
```

Roughly 30 seconds. `down` without `--volumes` keeps the platform database, so
the demo org keeps its two verified sources, its re-profiled catalog and its
history. Then **check what is actually being served**, not what is on disk — see
item 4. A cheap end-to-end smoke before handing a browser over is worth its
fraction of a cent:

```
docker cp scripts/agent_smoke.py dataagent-api-1:/tmp/agent_smoke.py
docker exec dataagent-api-1 sh -c "exec python /tmp/agent_smoke.py --org ebfe8139-abbb-45ee-8e21-8ed3c3b50642 --source Demo"
```

It should answer **3718**. The demo org has **two** data sources, so a
conversation must name one — "Demo" is the PostgreSQL pizza database.

### 4. The trap that cost a whole gate cycle

**`next dev` in the web container does not see host edits.** File-watch events do
not cross the Windows bind mount, so an edited file sits in the container while
the served chunk still holds the previous version (**B-045**). A *new route
directory* gives an obvious 404; an *edit to an existing file* is far worse,
because the page keeps working and silently runs the old code — a fix was
reviewed, shipped and reported as broken that way, having never executed.
`docker compose restart web` fixes it, and a full `up --build` fixes a partial
route tree. CLAUDE.md carries a one-liner that greps the **served** chunks for a
token from your change. Use it before believing anything.

### 5. Run the gate before asking anyone to

The owner's standing instruction, and it earned itself three times this session:
it found **B-041/042/043** before the Phase 7 gate, and **B-051** and **B-052**
before the Phase 8 one. Spending a cent of real credit to do it is expected, not
something to ask about. Read the run's **trace**, not just its final status — the
useful diagnosis has twice been several steps before the failure surfaced
(`context_selected {"tables": []}` was the tell for B-041). What remains
off-limits is re-running `make llm.smoke` for reassurance.

### 6. Two figures that must never come from a sample again

**B-051** was the second time this class bit — WP4.3 was the first, for row
counts. A card's range came from the profiler's sample, so the demo catalog
claimed orders ended sixteen months early and the agent refused an answerable
question: *a correct inference from a card that lied*, which is worse than a
crash because nothing about it looks wrong. **D-025** settles the rule: a figure
stated as a fact about a column comes from the engine or is absent, and a figure
that can only come from the sample must say so — as `distinct in sample` and
`examples:` already did, which is why only the range was wrong. The guard is
structural: `profile_column` has no code path from sampled values to a range, and
a test fails if that line ever comes back.

### 7. One gate criterion is covered by test, not demonstrated

**B-053 is accepted and closed** on the owner's decision: the duplicate-query
block is asserted twice in CI, both proving zero extra `query_executions` rows,
but it cannot be provoked on demand because that needs a model which repeats
itself. A scripted replay was **refused** — *a rigged demo is worse than a
recorded gap*. Do not reopen this by building the harness. The gap is written
into the Phase 8 GATE line in plain words, which is where it belongs.

### 8. What is open, and what it means

**P1** — **B-029**: a second real
provider, the only thing that closes the Phase 6 gate; needs an Anthropic key.

**P2** — **B-052**: a structured call's output ceiling can be smaller than the
schema it must fill; fixed for the planner, still true everywhere else, and
`FinalizeIn.answer` is next to hit it — which matters because Phase 9 makes the
composer work harder. **B-038**: `agent_configs` and `skills` have no store, so
L1–L3 of the layered prompt render as nothing, and it carries the per-org budget
overrides Phase 8's budgets already expect. **B-035**, **B-003**, **B-048**.

**P3 worth remembering** — **B-020**: `_col_1` is user-visible in the evidence
panel; the planner asks for aliases, which is a mitigation and explicitly not the
fix. **B-049**: the duplicate rule compares proposals, not canonical statements,
so one question written two ways still runs twice. **B-050**: the SSE tail polls
rather than using `LISTEN`/`NOTIFY`. **B-046/047**: the answer card should read as
one object.

### 9. Habits this session earned

- **jsdom is not evidence for anything timing-dependent.** A regression test for
  B-044 *passed against the broken code*; `apps/web/e2e/` exists because of that
  and runs real Chromium in CI.
- **A test that passes first time on an e2e deserves suspicion.** Tamper with the
  expectation and watch it fail before trusting it. Two tests this session passed
  vacuously until their fixture was made faithful.
- **Run `ruff check . --no-cache` before pushing.** A warm cache has passed
  locally while CI failed on the same tree.
- **A patch script must assert its edit matched** — and a `\n` inside a shell
  heredoc collapses into a real newline, silently breaking a string literal. That
  happened three times this session. Prefer the Edit tool, or write the block to
  a file and splice it, for anything containing escapes.

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
5. **There are two customer databases now, and only one of them is a fixture.**
   `Demo` is the pizza generator, whose numbers `truths.json` and the Phase 9
   evals depend on — do not touch it. `F&B demo` is a real operator's warehouse
   loaded from a SQLite file the owner supplied, which lives in `.SampleData/`
   and is **gitignored, not committed**. Rebuild it with
   `make seed.fnb SQLITE=.SampleData/<file>.sqlite`; it is idempotent and drops
   its own schema first. Test against **both**: seven defects were found in an
   afternoon against the second one that six phases against the first never
   surfaced. See "Second data source" below.

One process note worth carrying forward: a patch script that reports success
without asserting its edit matched will lie to you. This file's header silently
went un-updated for exactly that reason, and was caught only by reading it back.
Prefer an edit that fails loudly over one that prints "done".

## Second data source — the F&B trial (2026-08-16)

The demo organization now has **two** customer databases, so every run must name
one. The pizza fixture is untouched and its numbers still match `truths.json`;
the Phase 9 evals depend on that and nothing here touched it.

| | pizza | F&B |
|---|---|---|
| compose service | `seed-pizza-pg` :6543 | `seed-fnb-pg` :6544 |
| data source name | `Demo` | `F&B demo` |
| login | `pizza_readonly` | `fnb_readonly` |
| built by | `make seed` (generator) | `make seed.fnb SQLITE=…` (translator) |
| objects | 6 tables | 27 tables + 8 views, 43 foreign keys |
| rows | ~50k orders | 112,327 sales, 51,356 stock moves |

**Why it was worth doing.** The pizza fixture is a schema this project designed,
so it flatters every part of the product that has to read a schema. This one was
written by someone else: `dim_`/`fact_`/`bridge_`/`map_`/`meta_` naming, eight
tables that are empty because the business does not collect that yet, a column
called `coverage_start` holding `'opening'`, a `weighing_time` holding `'7.30
pm'`, and a `meta_gate` table listing eight open data-quality questions **about
itself**. Twelve realistic questions were run against it live.

**The load is a translation, not a fixture.** `ops/seed/load_sqlite.py` carries
tables, columns, primary keys, foreign keys, indexes and row counts across
verbatim and fails rather than dropping anything; every row count and every view
is checked back against the SQLite original. Two deliberate departures, both
recorded in the script: a `text` column becomes `date` only when *every* non-null
value is a bare ISO date (SQLite has no date type, so leaving it text would test
the interchange format rather than the customer's data), and the eight views are
hand-ported to PostgreSQL in `ops/seed/fnb_views.sql` because a half-working
automatic dialect translation puts wrong numbers in front of people. Seven
aggregates including all eight views match the original **to the cent**.

### What worked

Discovery and profiling read 35 objects and 280 columns in **under three
seconds**, and the cards are good: views are found and labelled as views, the
column roles (`[id]` / `[dimension]` / `[measure]` / `[time]`) are right, `100%
empty` is stated for the four columns that are wholly null, and a table with no
foreign keys says so in words. **Eight of twelve questions were answered
correctly to the cent** against figures computed independently, and the three
refusals were all honest and correctly reasoned — including *"break sales down by
menu category"*, refused because `dim_item.category` is 100% null, which the
profiler had flagged and the planner read.

**B-051's fix is visible working here.** Every card's `range` is right — including
`business_date range 2025-01-01 to 2025-12-31` — while every sample-derived figure
beside it on the same card is wrong (B-054). The range comes from the engine
because D-025 says it must; that is exactly the difference the decision bought.

### What broke, and what each one means

* **B-054** — the sample is the first rows on disk, so `fact_sale.row_role` is
  described as having two values when it has three, and the missing one is 80% of
  the table. Nothing lies: everything is labelled `in sample`. It is still the
  wrong picture of the data.
* **B-056** — the capability gaps handed to the planner up front are truncated
  **alphabetically**, so on 385 gaps it hears 20 about `bridge_item_ingredient`
  and **none** of the 14 about `fact_sale`. 4.3's up-front warning is noise.
* **B-057** — every table keys to a *one-row* `dim_business`, so the join graph
  says `fact_sale` and `fact_purchase` are joinable through it. That is a
  1.5-billion-row cartesian product arriving *through* the check that exists to
  prevent one.
* **B-058** — and in the same breath, the opposite: `dim_calendar.cal_date` and
  `fact_sale.business_date` join perfectly and no constraint declares it, so
  *"do we sell more on weekends?"* is **refused**. A false refusal on an
  answerable question, which `capability.py` itself calls worse than no check.
* **B-059** — the customer shipped their own semantic layer (`meta_metric`,
  `meta_gate`, `meta_assumption`, `v_data_quality_status`) and it sits in the
  catalog as ordinary data. Asked how many units of the top-selling set were
  sold, the agent answered **0** — correct SQL, business nonsense, and the
  database's own data-quality gate explains why in English one table away.
* **B-060** — the worst one. *"Which raw ingredients cost us the most to buy?"*
  asked twice picked two different tables and gave **AYAM MENTAH at RM 642,930**
  and **FRESH COCONUT WATER at RM 4,707** — the second from a filter matching 7
  rows in 51,356 — both as confident prose with no hedge. The SQL was right both
  times. What is missing is any sign that a choice was made.
* **B-055** — a view never gets a row estimate, and this source's own dictionary
  says runtime code should prefer the views.

**The pattern.** Everything that broke is a *semantics* failure, not a SQL
failure: every statement the agent wrote parsed, validated, ran and was cited
correctly. What it could not do was tell which of two defensible tables was
authoritative, or notice that a column it filtered on was undocumented, or read
the warnings the customer had already written down. **B-059 and B-060 are P1 for
that reason**, and they land on Phase 9's doorstep: a deterministic critic is
exactly the place to check that an undocumented code filter is not left
unexplained.

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
- [x] **B-041 (P1), B-042 (P1), B-043** Three reasons the gate could not have
      passed, found by running it rather than by reading it
      — taken before the chat UI, because each one on its own makes the M7 gate
      impossible and none of them is visible from a test suite that was green.
      Discovered by running `agent_smoke.py` against the demo org and reading
      the trace, three times in a row.
      **B-041: a whole question found no table at all.** `websearch_to_tsquery`
      ANDs bare words, so *"How many orders were placed in July 2026?"* asks for
      a card containing `'mani' & 'order' & 'place' & 'juli' & '2026'` — which no
      card can satisfy. `context_selected {"tables": []}`, and the model was
      asked to write SQL against nothing. It then failed to produce a valid
      `Plan` twice, which looked like a model problem and was not. The strict
      query now runs first and keeps every promise it made; **only when it
      matches nothing** are the words retried joined by OR and ranked. Live:
      the gate question returns `orders` first at 0.8, and Phase 8's flagship
      *"Which menu items sell best?"* returns `menu_items` first at 0.6.
      **B-042: the API container had no model configuration at all.** Compose
      forwarded the database, auth, secrets and TLS settings and **none** of the
      seven LLM ones, so every run scheduled by the API died at its first model
      call. It hid for a whole phase because runs happen *inside the API
      process* (D-021) while every provider test — `llm.smoke`, `agent.smoke` —
      runs on the **host**, where `.env` loads automatically. The host worked and
      the product did not.
      **B-043: a successful query died writing its result**, after it had already
      read the customer's data — `/app/ops/artifacts` was not writable, so
      `dal.run` raised `Permission denied` at the last possible moment and the
      trace stopped at `tool_called`.
      **`ops/docker-compose.yml` is an infra change and needs human review.**
      Verified end to end afterwards: *"How many orders were placed in July
      2026?"* → **"3,718 orders were placed in July 2026."**, citing execution
      `5175e4f4-…`, and `SELECT count(*)` against the seed database directly
      returns **3718**. Two model calls, a fraction of a cent
- [x] WP7.3b Chat UI with citation + manual test script              ← gate PR
      — the screen the whole build has been pointing at. A conversation names its
      database when it starts, a question goes in, and an answer comes back whose
      citation **opens** into the SQL that produced it and the rows it returned.
      **The screen adds no behaviour.** Everything it shows was already a row in
      the platform database; three routes and a poll are the whole of it, which
      is what architecture 3.1 means by a deliberately thin frontend.
      **The picker never guesses, and says why.** One registered source is
      preselected because there is nothing to choose; with several, nothing is
      selected and Start stays disabled, because choosing the first would be
      exactly the guess the scheduler refuses to make (WP7.2c, D-022). With none,
      the form is replaced by a sentence pointing at the data sources screen
      rather than a control that can only fail.
      **A refusal renders as an answer, not as an error** — a run that could not
      answer *completes* (WP7.2b), and dressing that up as a failure would send
      people hunting for a bug in their question. `failed` is the different
      thing and says something went wrong on our side.
      Three smaller decisions worth keeping. The **live run shows what it is
      doing** in words — "Reading the catalog", "Running the query" — because
      10.3's type names are ours and a person waiting two minutes should not read
      `query_executed`; the full timeline is WP8.3's. The **send button cannot
      bill twice**: a fresh idempotency key per draft, held across retries, so a
      resend replays the same question (D-019). And the **answer is not printed
      twice** — in single-shot the finding statement *is* the answer, so the card
      shows the evidence affordance and not a restatement; a test caught that.
      **The first attempt at this PR failed its own gate, and the record says
      so** (**B-044**, P1). The owner walked the script in a browser and found
      every reply rendering one message behind: the card showed a confidence
      badge and an openable citation and **no answer text**, because nothing
      re-read the thread when a run finished. The poll effect depended on the
      whole `run` object, so `setRun` inside a tick cancelled the very tick that
      was meant to reload the messages. The backend was right the whole time —
      3,718, correct SQL, correct refusal — which is what made it read as a
      rendering nicety rather than as the gate failing.
      **It then failed the gate a second time, and that is the more useful
      failure** (**B-045**). The fix was correct; the browser was running the old
      bundle. File-watch events do not cross the Windows bind mount, so the
      container had the new `conversation.tsx` and `next dev` never recompiled
      it — the served chunk still held the previous version. CLAUDE.md had warned
      about this only for *new route directories*, where the symptom is an
      obvious 404; for an **edit to an existing file** the page keeps working and
      silently runs the old code, which is how a fix got reviewed, shipped and
      tested without ever executing.
      **So the jsdom tests were retired as evidence.** Twice they were wrong in
      the same direction: the first regression test *passed against the broken
      code*, because a stubbed `fetch` resolving in a microtask beats React's
      commit; the second only bit once given artificial latency, which is a guess
      about timing rather than a measurement. `apps/web/e2e/` now drives real
      Chromium against a stub API over real HTTP — **4 tests, all failing against
      the pre-fix component and all passing against the fix**, proved by swapping
      the file and rebuilding. It runs in CI on the `web` job, needs no compose
      stack, no database and no key, and serves a **production build** rather
      than `next dev`, because on-demand compilation was itself a source of
      flake. Playwright arrives here rather than in WP11.2 for that reason; the
      wider smoke over every screen is still Phase 11's.
      26 new web tests (76 unit + 4 browser, all green), `tsc` and `eslint`
      clean, and both new routes present in the production build.
      Verified live against the seeded pizza database before writing the script,
      per the owner's standing instruction: *"How many orders were placed in July
      2026?"* → **"3,718 orders were placed in July 2026."** citing execution
      `5175e4f4-…`, against `SELECT count(*)` = **3718**; and *"Which menu items
      sell best?"* → an honest refusal naming the missing link, in one model call
- [x] GATE: "orders in July?" answered with citation; user sign-off
      — **signed off 2026-08-16**, on the third attempt, and the two failures are
      the more instructive half. Confirmed in the browser: *"How many orders were
      placed in July 2026?"* answered **"3,718 orders were placed in July 2026."**
      on its own, with no further interaction, and the citation opened into the
      SQL and the single row holding **3718** — the number `SELECT count(*)`
      returns against the seed database directly. *"Which menu items sell best?"*
      completed as an **honest refusal** naming the missing link, green rather
      than red, with nothing to expand.
      **What the gate caught that nothing else did.** Every reply rendered one
      message behind, so an answer arrived as a confidence badge and an openable
      citation with **no words** (**B-044**). The API was correct at every step —
      the right number, the right SQL, the right refusal — so no headless check
      could have seen it; only a person clicking through. Then the fix was
      reported as working while the browser still ran the **old bundle**
      (**B-045**), because file-watch events do not cross the Windows bind mount.
      Both are P1 and both are closed.
      Two habits came out of it and are worth more than the fix. **jsdom is not
      evidence for anything timing-dependent** — the first regression test passed
      against the broken code, so the suite now drives real Chromium against a
      stub API over real HTTP, in CI, proved to fail against the pre-fix
      component and pass against the fix. And **"is my code running" is a
      question to answer, not assume**: CLAUDE.md carries a one-liner that greps
      the *served* chunks for a token from your change.
      What the phase leaves behind: a person can ask a question in a browser and
      read the SQL behind the answer. `dal.run` is still the only way to customer
      data, `llm.complete` still the only way to a model, every attempt is on
      `query_executions` including the refusals, and every claim points at a row
      that exists.

## Phase 8 — Research loop + trace (M8)
- [x] **B-039 (P1) was this phase's precondition, and it is closed** (#41)
      — the menu-items refusal demo is this phase's flagship, and the run would
      have refused because it could not *find* `menu_items` rather than because
      no join path exists. A gate that passes for the wrong reason is worse than
      one that fails. Taken in Phase 7 on the owner's call and verified live:
      "menu items" now returns exactly the two `menu_items` tables, so what this
      phase demonstrates will be the capability check rather than a search miss
- [x] WP8.1a ResearchState + budgets (the rules the loop will enforce)
      — WP8.1 split in two (plan §1.1): the whole was heading past 1,200 lines,
      and unlike WP7.1's rejected split these two halves are both real on their
      own. This one is **the decisions**; WP8.1b is the control flow that obeys
      them. Nothing is wired yet, and that is the trade — but these are not a
      schema with no consumer: every rule here has behaviour and is tested
      directly, which is exactly what gets hard once a loop is driving them.
      `agent/budget.py` holds architecture 4.4's five ceilings — **8 iterations,
      10 queries, 20 LLM calls, 150k tokens, 240s wall** — and the sentence that
      matters is 4.4's own: **budgets decrement in the controller, never in the
      prompt.** A model told it has three calls left may believe it, forget it,
      or reason about it; none of those is a limit. Nothing here is ever rendered
      into a message.
      **Exhaustion is an ending, not an error**, the same distinction WP7.2b drew
      for refusals: `Exhaustion` is returned rather than raised, and its `reason`
      is written for the person who asked — *"I reached the time limit for one
      question"*, never `wall_seconds >= 240`. Time is checked first, because it
      is the ceiling the person waiting actually feels.
      An organization may **lower** a ceiling freely and **raise** one only up to
      `MAX_OVERRIDES`, because a hard cap that configuration can switch off is
      not one. A typo is ignored rather than raised on — configuration must not
      fail a run that would otherwise work.
      `agent/state.py` is 4.2's `ResearchState`. **Raw rows never accumulate**:
      the state carries a summary and an execution reference, never the result,
      so the loop cannot grow its own prompt every iteration — and cannot end up
      holding customer data where nothing masks it. **A finding whose every
      citation was invented is refused**, not merely trimmed: 4.2 makes `support`
      the reason to believe an answer, so a claim with nothing real behind it
      must not sit beside one that has. Repeating a sentence is not progress
      either, which is what stops a model keeping the loop alive by saying the
      same thing twice.
      Fields later phases own — `capability` (WP8.2), `critic` (WP9.1) — are
      present and empty, so a checkpoint written today stays readable by the code
      that fills them. The budget is stored in `agent_runs.budget` beside the
      state rather than nested inside it, matching 10.1's two columns.
      24 tests, **100% on both modules**, no database and no model in any of them
      — if one of these ever needs a fixture, something has moved into the prompt
      that should not have
- [x] WP8.1b The bounded loop itself, wired into `runner.py`
      — `agent/loop.py`: a **`for` loop, not a `while`**, so the iteration ceiling
      *is* the range and it terminates whatever the model says, whatever the tools
      return and whatever a future editor forgets. Every other budget is checked
      before anything is spent, so a run never overshoots a cap it was given.
      **The ends did not change**, which is what WP7.2b promised when it built
      them: `runner.py` still opens with context and closes with a composed,
      citation-verified answer that ends the run exactly once. What moved is one
      call in the middle — and `repair` stopped being a concept, because a
      correction is now simply the next iteration.
      **A ceiling is an ending with caveats, not a failure.** Exhaustion gives the
      run `budget_exhausted`, an answer that says what stopped it, and no
      `failure_reason`. The progress rule gives plain `completed`, because nothing
      was overspent — the run just had nothing further worth doing.
      **D-024, at the owner's direction, fixes the document rather than working
      round it.** 4.4 listed three model calls per iteration and, four bullets
      later, 20 calls for 8 iterations: `8 × 3` plus intake and compose is 26, so
      its own defaults did not fit its own loop. Observe is now **deterministic**
      — a mechanical transformation of a typed result, which cannot invent a
      number that was never there — bringing an iteration to two calls and a full
      run to 18 against 20. 4.4 now states that arithmetic so the two ceilings are
      checked against each other rather than being independently plausible.
      **Three defects the work found, none of which a green suite would have
      shown.** A refused query recorded nothing, so the next planner could not see
      the refusal *and* the duplicate rule could not stop it being re-proposed. A
      non-repairable failure — a database that is down — would have burned the
      whole budget; WP7.2b's `repairable` rule is kept. And `finding_added` was
      emitted twice per finding, once by the loop and once by the persistence;
      findings are now written through a callback **when they are reached**, so an
      interrupted run keeps what it concluded and the trace says it once.
      **B-049 filed and pinned by a test**: the duplicate rule compares proposals,
      not canonical statements, because the canonical form only exists after the
      query has been spent. One question written two ways still runs twice.
      44 tests across the loop, the state, the budgets and Observe; `runner.py`,
      `state.py` and `budget.py` at **100%**, `loop.py` at 92%, suite at 94%.
      Verified live twice: the gate question answered **3,718** in one iteration,
      and *"which store had the most orders in July 2026, and how did its revenue
      compare?"* named **Northgate, 955 orders, $31,128.68** — figures that match
      the database exactly. That second question is the one that earned its
      keep: it first came back *unanswered*, because the composer was being given
      one-line summaries and no rows, so it could not answer anything with more
      than one row in it. The composer now gets a **bounded, already-masked
      snapshot** of the last few results — handed to one final call, never
      accumulated into the state, which is what 4.4 actually forbids
- [x] WP8.2 Capability check (join-graph) + honest refusal path
      — the check **a model cannot talk its way past** (arch 4.3). A question
      needing two tables with no join path is unanswerable, and the honest thing
      is to name the missing link. What makes it matter is that the alternative
      does not look like a failure: **a join between unrelated tables does not
      error, it returns a cartesian product**, and a confident, correctly-cited
      answer computed from one is indistinguishable from a real one.
      `agent/capability.py` builds the graph from `catalog_relationships` and
      walks it breadth-first. **Edges are undirected** — a foreign key points one
      way, a join works either way — so `payments → orders → customers` is a
      two-hop path and a question over both is answerable. **Inferred edges must
      clear `MIN_CONFIDENCE`**: a speculative edge would let the check say
      "answerable" on the strength of a guess, turning an honest refusal into a
      wrong answer, which is the one trade this module exists to refuse.
      **The required tables come from the model's own proposed SQL**, not from
      guessing the question's intent (owner's approval). Inferring intent means
      sometimes refusing an answerable question, and a false refusal is worse
      than no check — it teaches people the product is broken. So the model
      proposes, the tables are read out of its SQL, and the deterministic check
      disposes **before the statement is sent**.
      Told twice, enforced once: the unreachable pairs go to the planner as fact
      at **L0** — never truncated, because a schema limit the model did not see
      is not a limit — and every proposed statement is checked regardless, since
      being told is a courtesy and not a control.
      **`dal/validator.tables_named` is a `dal/` change and needs human review.**
      It went there rather than into `agent/` because sqlglot is confined to that
      one file on purpose; it holds no `PolicyGrant`, produces no `Validated`,
      grounds nothing and authorises nothing — the only thing a caller can do
      with it is refuse. Testing it caught a real trap: **a CTE name parses as a
      table**, so `WITH t AS (…)` would have invented a join gap against the
      query's own scaffolding.
      12 tests; `capability.py` at 92%, suite at **94%**. Verified live on the
      demo database: *"Which menu items sell best?"* → **refused in one model
      call with zero queries**, naming the gap exactly — *"There is no link
      between menu_items and orders"* — and the control question still answers
      **3718**. The model refused before even proposing SQL, because it had been
      told; the check was there in case it had not been
- [x] WP8.3a SSE streaming + durable replay
      — WP8.3 split in two (plan §1.1), by layer as WP7.3 was: this is the
      endpoint, WP8.3b is the trace UI and the Phase 8 gate.
      10.3 is unambiguous about what this is — *"`agent_events` is the single
      source of truth; SSE is just its live tail"* — so nothing is streamed that
      is not already a durable row, and the stream is built from the same
      `read_events` the poll uses. Streaming changes **when** events arrive, not
      what they are, which is what makes a reconnect trivial rather than a
      synchronisation problem.
      **Replay is the default, not a recovery path.** A stream always begins by
      sending everything after the sequence the client names, so connect and
      reconnect are one operation: no in-memory buffer to miss, no window where
      an event is lost between writer and subscriber. **`Last-Event-ID` is
      honoured**, because that is how `EventSource` reconnects by itself — a
      dropped connection recovers without the page doing anything. A malformed
      one replays rather than refusing: it comes from a reconnecting browser, and
      the worst case of ignoring it is a replay the client already has.
      **One URL, negotiated by `Accept`.** 10.2 lists one events route, and the
      chat UI polls it today; two URLs would be two contracts that could drift.
      **The stream ends when the run does**, with a heartbeat in between so a
      proxy does not mistake a quiet trace for a dead socket, and a hard ceiling
      so a stuck run cannot hold a socket forever.
      One ordering trap, pinned by a test: the run's status is read **before**
      the final read of the table. The other order silently drops any event
      written between the two — in practice `run_finished` itself, which is the
      one event a client is waiting for.
      10 tests; `sse.py` at 95%, `runs/routes.py` at 99%, suite at **94%**.
      **B-050** filed: the tail polls rather than using `LISTEN`/`NOTIFY`
- [x] WP8.3b Trace UI + the Phase 8 gate                             ← gate PR
      — the product's honesty claim, rendered. `agent_events` is append-only by
      grant precisely so a trace can be shown as a **record** rather than a
      story: what appears was written once, by the code that did the thing.
      **Every event shows, including the ones that are not progress** — a refused
      query, a duplicate blocked, a budget warning, a capability gap. A trace
      listing only successes would be advertising, and those are exactly the
      events a UI written to look good would drop.
      **Read with `fetch`, not `EventSource`, and that is a security decision.**
      `EventSource` cannot set headers, so authenticating it means a token in the
      query string — which this codebase already refused once, for the
      data-source password, in the same words: browser history, referrer headers,
      every access log in between. So reconnection is ours, resuming from
      `Last-Event-ID` off the durable rows, and there is **one** auth path rather
      than two.
      Open while the run is going, collapsed once it has finished, an explicit
      toggle winning from then on — derived rather than synced in an effect. The
      conversation's poll now asks only *"has it finished?"*; the steps arrive on
      the stream.
      6 browser tests, stable across repeated runs, including the gate's own
      *mid-run refresh replays the whole trace*. That one first passed
      **vacuously** — the stub returned `last_run_id: null`, so the page never
      adopted the run and the answer showed from the messages alone. Fixed the
      stub to match the real API, and only then did it exercise replay.
      76 unit tests, `tsc` and `eslint` clean.
      **The gate found one more blocker before it could run: B-051** — a card's
      range came from the profiler's sample, so the demo catalog claimed orders
      ended sixteen months early and the M8 scenario refused an answerable
      question. Fixed in its own PR (#51) with D-025, and **B-052** filed from
      the same session.
- [x] GATE: pizza scenario ≤8 iters; menu-items → honest refusal; sign-off
      — **signed off 2026-08-16.** Walked in the browser: the revenue-decline
      question answered **$938.28** — June $123,650.61 against July $122,712.33,
      the database's own number — in **two** research steps against a cap of 8,
      decomposing as architecture 11.2 describes: total, then by store and
      channel, then volume against order value. *"Which menu items sell best?"*
      refused **green**, naming the missing link between `menu_items` and
      `orders`, with **zero queries run**. A mid-run refresh replayed the whole
      trace, which is the property `agent_events` has been append-only for since
      revision 0012.
      **One criterion is covered by test rather than demonstrated, and that is
      the owner's decision** (**B-053**, accepted 2026-08-16). The
      duplicate-query block of 4.4 is asserted twice in CI —
      `test_the_same_statement_is_never_sent_twice` and
      `test_a_repeated_query_counts_as_no_progress_and_is_never_sent`, both
      proving **zero extra `query_executions` rows** — but it cannot be provoked
      on demand, because doing so needs a model that repeats itself and a
      competent one does not. A scripted replay was considered and **refused**:
      *a rigged demo is worse than a recorded gap.* So the gap is recorded here,
      and the evidence is the suite.
      What the phase leaves behind: a question is **investigated** rather than
      answered in one shot — a `for` loop whose ceiling is its own range, budgets
      decremented in the controller and never in the prompt, a duplicate refused
      before it is sent and two barren iterations forcing an ending. A schema
      that cannot answer is refused by a **deterministic** join-graph check the
      model cannot talk past, naming the missing link. And every step of it is a
      durable row, streamed live and replayable after a refresh, because 10.3
      makes that trace the product's honesty claim rather than a progress bar.
      Three defects the gate itself found, none visible to a green suite:
      **B-051** (a card's range came from a sample, so the agent refused an
      answerable question on the strength of it), **B-052** and **B-041/042/043**
      before it. Running the gate before asking anyone to walk it earned its keep
      three times over.
- [x] WP8.4 Capability check: the chasm trap (**B-057** P1) + **B-056**
      — **added after the gate, and the gate stands.** Pointing the same check
      at a real star schema on 2026-08-16 exposed the opposite failure to the one
      Phase 8 was judged on: a **one-row** hub dimension makes every fact
      reachable from every other, so the check calls a 1.5-billion-row cartesian
      product *answerable*. The criterion the gate tested — a schema that cannot
      answer is refused, naming the missing link — is unaffected and still met.
      The pizza fixture has no hub table, so no amount of testing against it
      could have shown this. Owner scheduled it **before Phase 9** because the
      honest-refusal claim is the product's core promise. Build spec in "Next
      step"; the short version is that direction beats degree, and the fix must
      produce a third verdict rather than a second refusal.
      **Shipped in #55.** A foreign key is many-to-one by construction, so
      every edge carries a direction the undirected adjacency was discarding;
      `safe_path` refuses the up-then-down turn that makes a shared parent
      multiply its two children together. On the F&B catalog **143 of the 210
      pairs the check called joinable were false** — two in three — while all
      385 genuine refusals and every ordinary star join are unchanged. The
      verdict is three-valued and only `unreachable` refuses, so the Phase 8
      gate's criterion is untouched. Proved live rather than only in the
      suite: the spend-against-revenue question now returns a **CTE that
      aggregates each fact to its shared key and joins the aggregates**, and
      both figures check out to the cent. **B-056** went with it. What is
      deliberately not done is *blocking* — `graph.check` sees which tables a
      statement names and not how it joins them, so blocking there would
      refuse a correct aggregate-then-join CTE along with a bad join; that
      needs join predicates read in `dal/validator.py` and its own reviewed
      PR (**D-026**).

## Phase 9 — Critic + composer + evals (M9)
- [x] **WP8.4 (B-057, P1) landed before this phase** (#55) — see Phase 8
- [x] **B-005 (P1) closed before this phase started** (#57) — and it was a
      product defect rather than an eval chore. Nothing told the model what
      the current date was, so it chose an anchor per question and chose
      differently: `CURRENT_DATE` for one, `MAX(order_date)` for the next,
      both right on the day they were measured. **D-027** gives the run an
      `as_of`, defaulted to the wall clock and pinned by the eval harness.
      The seed's `END_DATE` stays frozen and `truths.json` is untouched
- [x] WP9.1 Deterministic critic + LLM checklist + bounded re-entry (#58)
      — two stages, and stage 1 is the one that matters. Every rule 4.5 names is
      arithmetic over what is already durable: citations resolve to executions
      this run produced, the **date range in the SQL covers the period the
      question asked for** (which only became checkable when D-027 gave a run an
      `as_of`), an answer is not built on zero rows without saying so, and a
      figure appearing in no result is a **warning** — 4.5's own instruction,
      because prose rounds and computes and blocking on that would refuse correct
      arithmetic. Stage 2 is one `small`-tier call against a fixed rubric.
      **A deterministic block skips stage 2 entirely**, which is what makes stage
      1 free in the sense that matters: the M9 acceptance line is a wrong-date
      draft caught with **no model call at all**, and the test asserts the call
      count, not just the verdict. The re-entry is bounded at one by
      `critic_passes` on the *state*, so an interrupted run cannot come back and
      claim a fresh one; it moves through `validating` and back to `running`,
      which is the transition WP7.1 added and nothing had used. **D-028** raises
      the call ceiling 20 → 24, the move D-024 said would be needed the day a
      stage was added, and the arithmetic is now asserted as a sum so the next
      stage fails there rather than in a demo. One defect found by the fixtures
      while building it: the capability rule first blocked on *any* catalog gap
      rather than on a statement actually refused — a false block, the thing
      WP8.4 spent itself avoiding, caught before it shipped
- [ ] WP9.2 Composer (citations/limitations) + eval harness v1      ← gate PR
- [ ] GATE: seeded-wrong-draft caught; 20 golden evals pass; sign-off

## Phase 10 — Knowledge + semantic layer (M10)
- [ ] WP10.1 Docs ingest/chunk/embed/retrieve under RLS + APIs
- [ ] WP10.2 Semantic definitions + verified queries + critic enforcement ← gate
- [ ] GATE: uploaded policy changes generated SQL; isolation test; sign-off

## Phase 11 — Charts + polish (M11)
- [ ] WP11.1 Chart tool (validated Vega-Lite) + client renderer
      — carries **B-048** (owner, at the Phase 7 sign-off): the chart belongs
      **inside the answer card**, and its spec must be openable the way the SQL
      is. A chart nobody can trace back to the query behind it is decoration that
      looks like evidence — the same claim Phase 7 made for answers, extended to
      pictures. Filed before the tool is designed rather than retrofitted after
- [ ] WP11.2 History/catalog/members polish + Playwright smoke      ← gate PR
      — carries **B-017**: recovery when an org has no Admin who can sign in
      (owner's call 2026-08-12, moved forward from Phase 12)
      — and **B-061** with **B-020**: internal identifiers and the wrong
      currency symbol reaching the reader in prose. Grouped by the owner on
      2026-08-16 because they are one family — the system's own representation
      surfacing where a person reads — and one rule fixes both
      — and **B-046** and **B-047**, both owner requests at the Phase 7 sign-off:
      fold the status and confidence badges *into* the answer bubble rather than
      a separate box below it, and highlight the numbers, dates and names in an
      answer so it can be read at a glance. All three of these plus B-048 are one
      idea — **an answer should read as one object** — so they are best done
      together. B-047 has a real design question inside it: emphasis is a claim
      about what matters, so the composer should return structure rather than the
      UI pattern-matching prose a model wrote
      — the Playwright smoke this WP plans is now a **widening** rather than a
      start: WP7.3b already added `apps/web/e2e/` with Chromium in CI (B-044)
- [ ] GATE: trend question → rendered chart; smoke green; sign-off

## Phase 12 — Azure deploy + hardening (M12)  ⚠ human review on every PR
- [ ] WP12.1 Bicep modules + env params + what-if in CI
- [ ] WP12.2 OIDC deploy workflow → dev env + Key Vault backend + smoke
- [ ] WP12.3 Observability wiring + quotas hard-stop + alerts
- [ ] WP12.4 Prod env + ASVS-lite checklist + restore drill + v1.0 tag ← gate
- [ ] GATE: arch Part 14 acceptance; nightly evals on; user sign-off

---

## Next step

**The Phase 8 gate is signed off** (2026-08-16), and its evidence is recorded
against the GATE line under Phase 8 above — including the one criterion that is
covered by test rather than demonstrated (**B-053**, accepted).

**WP8.4 is done** (#55, **B-057** and **B-056**). The capability check now
tells a safe join path from a chasm by the direction the foreign keys already
declare, and the verdict is three-valued: `joinable` / `comparable` (aggregate
each side to the shared key, then join the aggregates) / `unreachable`, with only
the last one refusing. Why direction rather than any threshold, and why the
middle verdict must not refuse, is **D-026**. What it deliberately left: the
check reads which tables a statement names and not how it joins them, so it
*steers* the planner up front and records the chasm in the trace rather than
blocking — blocking needs join predicates read in `dal/validator.py`, which is a
security-boundary change owed its own reviewed PR.

~~**Before any Phase 9 code: B-005.**~~ **Closed in #57.** Phase 9's own line said it must
be closed before the phase starts, and it is an **owner decision rather than a
code task** — the seed dataset pins `END_DATE = 2026-07-31` for reproducibility,
so "last full month" stops meaning July as real time moves on, and golden eval #2
is phrased relatively. Either pin every eval question to absolute dates, or add a
documented `SEED_END_DATE` override that CI fixes while local demos track today.
Raise it early; it needs an answer, not an implementation. **Tick both its
checkboxes in the same PR** — Phase 9's line and its BACKLOG row — because the
STATUS guard keys on the last occurrence and B-039 already cost a red CI run on
every PR raised after it merged.

**WP9.1 is done** (#58). Next is Phase 9 / **WP9.2 — composer and eval
harness v1** (`p9.2-composer-evals`), which is the **Phase 9 gate PR**.
Plan §6 Phase 9, architecture 4.5 and M9.

Build:

- **The composer's limitations.** WP9.1 hands the critic's warnings to the run
  and the second draft is *told* what was wrong, but `FinalizeIn` still has no
  `limitations` field — the reasons reach the answer only as prose the model
  chose to include. Give it the field, render it in the answer card, and make a
  `WARN` finding travel there by construction rather than by persuasion.
- **`ops/evals/`** — the twenty golden questions from plan §6, expected answers
  read from `ops/seed/truths.json` and **never hardcoded**, run against the
  FakeLLM so `make evals` is deterministic and free.
- **`as_of` is pinned to 2026-08-16** in the harness (**D-027**). Keep the
  relative phrasing in #2, #6, #11–13, #17 and #20 — those exist to test exactly
  that handling — and absolute dates only in #1 and #18, which were always about
  a fixed window. **#19 only works because of the anchor**: "a future date range"
  means after `as_of`, and against a wall clock no date stays future.
- `make evals` as a required CI check, plus `nightly-evals.yml` on a schedule
  with real keys and a hard token cap (plan §4.1).

What WP9.1 hands it:

- **A verdict that already knows how to be a limitation.** `CriticVerdict.warnings`
  is separate from `.blocking` precisely so the composer can render one without
  acting on it.
- **A checked date range.** Eval #1 and #18 are absolute and #2 is relative, and
  the critic now blocks a draft whose SQL missed the period — so an eval that
  passes for the wrong reason fails here first.
- **Headroom that was counted, not assumed.** D-028's arithmetic leaves three
  calls spare against 24, and `test_the_call_ceiling_fits_the_run_that_spends_the_most`
  asserts the sum.

Two things to watch:

- **B-052** — `FinalizeIn.answer` is 4,000 characters against a default output
  ceiling of 1,024 tokens, and adding `limitations` makes that schema bigger. The
  critic's own call sets its ceiling explicitly for this reason; the composer's
  does not, and WP9.2 is the phase that makes the composer work harder.
- **B-060** — the eval suite is where "the same question twice gives the same
  answer" stops being a hope. #20 is a duplicate of #11 phrased differently and
  exists to check exactly that.

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
