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
make seed.fnb SQLITE=…  # load a customer SQLite file into seed-fnb-pg
make migrate            # alembic upgrade head against local platform-pg
make db.setup           # migrate + grant dataagent_app its local login
make api.dev / web.dev  # hot-reload dev servers
make lint / typecheck / test / fmt
make evals              # eval harness with FakeLLM (Phase 9+)

## Hard rules (full list: implementation-plan.md §1)
- PR-only into protected main; squash merge; branch p{phase}.{wp}-{slug}.
- Update docs/plan/STATUS.md in the same PR as the work.
- New deferred work → docs/plan/BACKLOG.md entry (B-###) in the same PR.
- **Allocate a B-### against a freshly-fetched `origin/main`, and expect to
  renumber.** Two machines filing on the same day both took B-114 (#94 and #95);
  `check_backlog.sh` caught the duplicate at merge, so nothing was corrupted, but
  the second one to push pays — and the cost scales with how far the id has
  spread. #95's had reached six files, because entries get cited in code
  comments, `make help` text and the README, not only in the table. `git fetch`
  before you pick the number; if you lose the race, renumber every citation, not
  just the row.
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
- **A new capability ships with proof it is *reached*, not only proof it
  works.** See below — this is the defect this project produces most.
- Mid-phase, verify it yourself and report the evidence. Only ask the user to
  check what you genuinely cannot: rendered browser UI, anything specific to
  their machine, and decisions. Their time is the scarce resource.

## Built, tested, unreachable — this project's characteristic defect
Five instances so far, and **not one was caught by CI**:

- **B-083** — a definition reached the critic and never the model, so the rule
  was enforced against a planner that had not been told it. `Definition.render()`,
  written for exactly that job, was called by nothing.
- **B-085** — an imported definition answers only to its key and its label, and
  no real question is phrased in those words. Eighteen metrics imported, nothing
  bound.
- **B-100** — the answer's method line, computed on every run and read by
  nothing.
- **B-109** — a colour channel assembled, carried, accepted by the tool and
  asserted by a test, with no field on the schema the model actually fills.
- **B-133** — `answered` computed on every run since WP7.2b, written into a trace
  event and onto no column, so the screen had no way to ask and labelled every
  honest refusal *"answered"* — the one claim a refusal exists to deny.

**B-133 sharpens what "tested" is worth here, and it is the most useful thing on
this list.** `answered` *had* a test. It asserted `outcome.answered is False` — on
the **outcome object**, which is the one thing the product cannot look at. A test
on an intermediate value proves the value is right and says **nothing** about
whether anything reads it. The assertion was true, stayed true, and was true the
whole time the screen was contradicting it.

So when you write the test, ask what it is holding: a value in flight, or the
thing a person receives. B-133's fix moved the assertion one object outward — to
`view.answered`, what the API actually returns — and that single step is the
difference between a test that guards the behaviour and a test that guards a
local variable.

**Coverage cannot see this class, by construction.** A unit test hands a function
its arguments directly, which is the one thing the product cannot do — so the
more carefully a capability is tested in isolation, the more convincingly it
looks exercised. `charts._spec`'s colour branch was covered the whole time it
was unreachable. No coverage threshold changes that.

**So: a new capability ships with proof it is reached on the live path.** A test
that drives it the way the product does — through the schema the model fills,
the route the screen calls, the prompt the run sends — not only one that calls it
directly. B-109's regression test is the shape: it asserts a refusal code that is
only reachable if the field survived `ChartAsk` → `_chart_for` → the tool →
`decide` → the stored run, so if the field ever stops travelling, the test goes
red rather than staying green over dead code.

Every one of the four was found by reading or walking. Until **B-110** ships a
schema-correspondence check, walking is still the only thing that has caught it.

**And when a field is *removed* from a schema, sweep the whole repo — not the
test tree.** D-044 deleted `FinalizeIn.answered`; `FinalizeIn` is
`extra="forbid"`, so a stale key is a hard validation failure rather than an
ignored field. **Three producers still sent it, and each was found separately:**
the unit fakes (the suite, same commit), `ops/evals/runner.py` (the `evals` job,
failing **20/20**), and `llm/scripted.py` (the **browser e2e**, two hours later —
and that one ships in the product image, so its failure read as a broken product
rather than a stale fixture). The sweep script reported `leftover: none` and was
telling the truth about `apps/api/tests`, which is the only directory it was
given. Producers of a model-filled schema live in `src/`, `ops/` and the tests
alike; grep all three, and prefer a check that validates each fake's output
against the model the product parses it into.

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
  **That recipe cannot see a screen whose chunk is fetched lazily**, which is most
  of them: the chunk is not in the server's HTML, so there is nothing for the grep
  to walk and it reports ABSENT for code that is perfectly current. Driving the
  page in a browser and grepping what it *requests* does not rescue it either — a
  signed-out visitor renders the sign-in card, so the screen's chunk is never
  asked for at all. Ask the container what it compiled, then fetch that chunk by
  name:
  ```sh
  docker exec dataagent-web-1 sh -c "grep -rl '<a-token>' /app/.next/dev/static/chunks | head"
  curl -s http://localhost:3000/_next/static/chunks/<the-file>.js | grep -c '<a-token>'
  ```
  Use a token that exists **only** in the new code, and check that an old-only
  token is *gone* as well: "the new string is present" and "the old code is not
  being served" are two claims, and a partial recompile satisfies the first
  alone — seen on 2026-08-21, where after a restart the api-client chunk carried
  the new method while the screen's chunk had not been rebuilt yet.
  **And restart `api` too.** It bind-mounts its own source and runs with
  `--reload`, which has the same blind spot: a container up for hours served an
  OpenAPI schema with no trace of a route that had been on disk the whole time.
- **`docker compose restart web` has a second failure, and it looks like a
  routing bug.** The restart keeps `/app/.next`, and Turbopack's dev cache can
  come back inconsistent with the source it then compiles: on 2026-08-21 every
  route nested under the dynamic `[orgId]` segment served a **404** — the whole
  product, since that is where the product is — while `/orgs/{id}` itself and
  `/invitations/accept` were fine and every file was present in the container.
  The log is what settles it: the request immediately before the old process's
  `ELIFECYCLE` had returned 200, so the restart broke what it was run to fix.
  **Clear the cache when you restart for a code change:**
  ```sh
  docker exec dataagent-web-1 rm -rf /app/.next
  docker compose -f ops/docker-compose.yml --env-file .env restart web
  ```
  It costs one recompile. A 404 on a route whose file is right there is otherwise
  a long hour, because every instinct says to look at the routing.
- **A new web dependency needs the image rebuilt, and the B-044 recipe will not
  tell you.** `node_modules` comes from the image, not the bind mount, so
  `pnpm add x` on the host leaves the container without it: the page loads, your
  code is served, and only the feature that imports `x` fails. WP11.1 hit this —
  `vega-embed` was installed on the host, the served chunk contained every
  string the recipe greps for, and the browser still showed the chart's fallback
  because the dynamic import 404'd inside the container. Grep proves *your code*
  reached the browser; it says nothing about whether that code's imports
  resolve. After adding a dependency:
  ```sh
  docker compose -f ops/docker-compose.yml --env-file .env up -d --build web
  docker exec dataagent-web-1 sh -c "ls node_modules/<the-package> >/dev/null && echo present"
  ```
- **A local `make test.web` that reports missing files or "no tests" is usually
  this machine, not the suite.** vitest forks a worker per test file, and under
  memory pressure they fail to start: the run then collects a *subset* silently
  and exits non-zero with `[vitest-pool]: Failed to start forks worker` and
  `Timeout waiting for worker to respond` buried above the summary. Seen as
  *"39 passed | 11 errors"*, *"no tests"*, and *"7 of 18 files"* in one session,
  each looking like a different bug. The cause was **4.1 GB free of 31.4 GB** —
  Docker Desktop's WSL VM reserves ~15 GiB while the containers inside it use
  about 1 GB, and it does not give it back while the stack is up. The same suite
  is 18 files and 240 tests with `pnpm exec vitest run --no-file-parallelism`.
  Read the errors above the summary before believing the count, and cap
  parallelism rather than chasing the tests.
  **And `--no-file-parallelism` is not always enough** — at 4.1 GB free it still
  reported *"no tests"* with one worker error on 2026-08-27. `--pool=threads`
  ran the same file to 48 passing tests in 6s. The forks pool spawns a process
  per file and processes are what this host cannot afford; threads share one.
  Reach for `--pool=threads` before concluding anything about the suite —
  **but it is a better bet, not a fix.** The same command reported *"no tests"*
  once on 2026-08-28 and then 52 passing on an immediate retry, with nothing
  changed. **A bare retry is the cheapest diagnostic here**: a real failure
  fails twice, and this one does not.
- **`pytest tests/a tests/b` can fail to collect, and it is not the tests.**
  Every `conftest.py` is the module `conftest`, so two suites in one invocation
  race for the name and whichever loses gets the other's fixtures — seen on
  2026-08-28 as `ImportError: cannot import name 'Tenant' from 'conftest'` on
  five files in `tests/runs` when they were run beside `tests/dal`. **B-074 is
  the entry that explains it** and it reads as a note about *writing* fixtures;
  it is also a rule about *invoking* pytest. Run the suites as separate
  invocations, which is what CI does, and do not go looking for a product change
  that broke five unrelated files at once.
- Git Bash rewrites any argument starting with `/` into a `C:\...` path, so a
  `docker exec … sh -c '/opt/…'` arrives as nonsense. Start such command strings
  with a word (`exec /opt/…`), as `ops/scripts/seed_mssql.sh` does.

## Customer data: the default, and the one sample that is cleared
The GitHub remote is **public**. The default is that anything a customer supplies
— the database, its data dictionary, and anything derived from either — lives
under `.SampleData/`, which is gitignored as a whole directory rather than by
file extension.

**One exception, granted by the owner on 2026-08-22.** The F&B sample this
project has been built against is **cleared for the planning documents**: its
shape, its table and metric names, and the figures already derived from it in
`STATUS.md`, `BACKLOG.md` and `DECISIONS.md` may stay and may be added to. That
is deliberate rather than tolerated — entries like B-060, B-085 and B-092 are
only worth reading because they carry the real numbers (`move_type` at
`DO 78%, PI 17%, UC 4%`, a filter matching 7 rows in 51,356, eighteen imported
metrics none of whose questions name their key), and a version with the figures
removed would record that something happened without recording what.

**The rule this section had before was written as an absolute and the repo did
not keep it**, which is worse than a narrower rule honestly stated: a rule
everyone is quietly breaking stops being read at all.

**What is never cleared, for this client or any other.** Credentials and
connection strings; the raw data itself, which stays in `.SampleData/`; and
anything that would identify a person rather than a schema.

**Any other client's data needs the owner's explicit permission first**, asked
for and given *before* it reaches a tracked file — not after, and not inferred
from this exception. The clearance above is for one sample and does not
generalise.

**Scan DDL for literals, not just tables for rows.** A ported view definition is
schema by its file extension and *content* by what is inside it: `ops/seed/`
briefly held a hand-written Postgres port of a customer's views, and two of those
views were literal `UNION ALL` blocks reproducing that customer's own
data-quality findings verbatim. A scan of distinct row values did not catch it,
because those sentences are not rows — they are string constants in the view SQL.
So a check of "is any customer data in this commit" has to grep the committed
tree for **quoted literals in DDL, view definitions and fixtures**, not only for
values pulled out of tables. `load_sqlite.py` therefore defaults `--views` to
`<database>.views.sql` beside the `.sqlite`: the customer's SQL stays with the
customer's data, and no future import can put it back by accident.

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
