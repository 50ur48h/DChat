# STATUS — data-agent build

Current position: **Phases 0-11 are done and signed off. Phase 12 is under
                  way: WP12.1 is merged (#92).** The owner answered the whole
                  **USER INPUT** batch on 2026-08-22, so nothing in this phase
                  waits on them for scope: subscription and region settled,
                  `rg-dataagent-dev` approved, USD 50 a month, `OPENAI_API_KEY`
                  only, no custom domain, Postgres B-series at 7-day retention.
                  **The one decision that changes the plan is D-041: this phase
                  stands up `dev` only.** Prod is deferred and `v1.0.0` is tagged
                  from dev. Nothing is dropped from WP12.4's gate — the restore
                  drill, the quota hard-stop, managed identity and the ASVS-lite
                  checklist all still apply, against dev. Architecture 9.1 and
                  the plan's gate were amended in #92.
                  **Dev is deployed and serving** (superseding this paragraph's
                  original claim that nothing was): `rg-dataagent-dev` exists,
                  the web app and API answer on their public hostnames, and a
                  person can sign in, invite, and register a customer database.
                  What it cannot yet do is answer a question — see B-126.
Next step:        **WP12.2 — OIDC deploy workflow, Key Vault backend, dev env**
                  (`p12.2-deploy-dev`). Plan §6 Phase 12 WP12.2, architecture
                  Part 9 and §4.2.
                  **It cannot start until the owner has done two things**, both
                  written out as exact instructions in #92's description:
                  (1) run the `az` commands that create the GitHub-OIDC app
                  registration and its two federated credentials, and (2) create
                  the `dev` GitHub environment holding `AZURE_CLIENT_ID`,
                  `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` and
                  `POSTGRES_ADMIN_PASSWORD`. Until that identity exists there is
                  nothing to authenticate as — which is why `what-if` belongs to
                  WP12.2 rather than WP12.1, and why the very first task of the
                  next session is to confirm those two are done rather than to
                  write code.
                  **One decision is still open and is the owner's**, raised in
                  #92 and recorded here so it survives the session: `roles.bicep`
                  creates three role assignments, and creating a role assignment
                  needs `User Access Administrator` (or `Owner`) — a strictly
                  larger permission than `Contributor`. Either grant the OIDC
                  identity that role at the resource group, or create the three
                  assignments by hand and drop `roles.bicep` from what the
                  pipeline deploys. **Ask before writing the deploy workflow**,
                  because the answer changes what the workflow may do.
                  **The budget alert address is deliberately not in this repo.**
                  It is read from the environment at deploy time; ask the owner
                  for `BUDGET_ALERT_EMAIL` when WP12.2 wires the deploy, and
                  keep it out of every tracked file when they give it.
                  **Every Phase 12 PR needs human review**, including the ones
                  that only touch `infra/`.
Merge policy: ASK
Blocked on user: **no.** (This entry described the two owner tasks that gated the
                 start of WP12.2; both were done on 2026-08-23 and the deploy has
                 run many times since. Kept below for the record of what was
                 needed, because the same two are needed again for any second
                 environment.) Two owner tasks, written out as
                 exact commands in #92's description: the `az` commands creating
                 the GitHub-OIDC app registration and its two federated
                 credentials, and the `dev` GitHub environment holding
                 `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`
                 and `POSTGRES_ADMIN_PASSWORD`. Plus one decision — whether the
                 OIDC identity gets `User Access Administrator` or the three
                 role assignments are made by hand (see `Next step:`), and one
                 value to be asked for at deploy time and never committed,
                 `BUDGET_ALERT_EMAIL`.
                 Not blocking: the **OpenAI key is a repository secret** (owner,
                 2026-08-17), so `nightly-evals.yml` can run — keep its token cap
                 tight, because the local live run spent **223k tokens** for
                 twenty questions. An Anthropic key would still close **B-029
                 (P1)** and with it the Phase 6 gate; it blocks nothing in
                 Phase 12 either, and WP12.4 turns nightly evals on against dev,
                 so B-029 wants an answer before that gate rather than after.
Last updated: 2026-08-24 by Claude Code (**dev serves authenticated requests and answers
              no questions.** B-123 is merged (#105) and the owner has signed in, created a
              user, sent an invitation and registered a customer database through the UI.
              Asking it anything fails: `apps.bicep` ships `OPENAI_API_KEY` and none of the
              four `LLM_*` variables, so every run dies at its first model call — **B-126
              (P1)**, fixed on `p12.2-llm-config` along with the three things that let it
              through, and **B-127** — no test here could ever observe a log line, which is
              why the silent failure survived. Both merged (#108). **The re-dispatch then
              failed before touching Azure**: #107's new preflight step read
              `${${$name}}`, so the step that checks the deployment has its values could never
              pass — **B-128**, instance five of a check failing for a reason unrelated to
              what it checks. Fixed in #109; the workflows are linted with shellcheck now,
              which is what would actually have caught it — `bash -n` would not, because the
              failure is runtime rather than syntax)
              each failing on something no check here could see — OIDC subject, a log
              destination needing a key, BuildKit, an extension allow-list, a browser bundle
              built for localhost. The owner has ruled that **dev proves the deployment path
              and never hosts a demo**; the deployed database stays empty on purpose)

---

## A GUID nobody could check, 2026-08-25 (B-130)

The deploy after #111 got past the preflight, signed in, ran `what-if` clean —
and **failed on the infrastructure step three minutes in**, with the resource
group already touched:

```
RoleDefinitionDoesNotExist: 'b86a8fe444ce4948aff78adaef4a4c62'
```

`roles.bicep` set `keyVaultSecretsOfficer = 'b86a8fe4-44ce-4948-aff7-8adaef4a4c62'`.
The real id, from `az role definition list`, is
`b86a8fe4-44ce-4948-`**`aee5-eccb2c155cd7`**. First three groups right, last two
invented — **written from recall in #107 instead of looked up**. The same failure
as the fabricated `fnb_readonly` password earlier in this phase, except the
password was caught within minutes because somebody tried it.

**Why nothing caught it, which is the part worth keeping.** A wrong *name* reads
wrong, and `check_env.sh` catches it (B-120). A wrong *expansion* reads wrong, and
shellcheck catches it (B-128). A wrong **GUID reads exactly like a right one** —
to a reviewer, to `bicep build`, which type-checks a template and cannot know what
a constant means, and to **`what-if`**, which listed the role assignment as a
resource it would create without ever resolving the role it points at. Another
entry in the what-if blind-spot list, and a sharper one: what-if had the value in
hand and no reason to doubt it. Only Azure can tell the two apart, so only Azure
can be the check.

`ops/scripts/check_role_definitions.sh` now runs after the OIDC sign-in and before
`what-if`, so a bad id refuses the deploy instead of stopping it halfway. Verified
against the real committed files — red on `roles.bicep` at 49e4082, green on the
fix — and it also fails when it extracts no ids at all, because a guard that
silently checks nothing is the defect this repository has already shipped twice.

**Two of the three ids were correct** (`AcrPull`, `Storage Blob Data Contributor`),
checked against Azure while fixing this. "One was wrong" and "the file was
unreliable" are different findings, and only the first is true.

---

## The pipeline asks the app what it can do, 2026-08-25 (B-125, B-129)

**Key Vault taught this the expensive way and Blob was about to.** Registering a
data source writes a customer's credential to Key Vault; the app identity held a
**read-only** role, so the product's central action failed on a deployment that
was otherwise healthy, and the way we found out was a person clicking a button
(**B-125**). Every query execution writes a result artifact to **Blob**, and no
run has ever completed in Azure — so that path was in exactly the same position,
waiting for the first successful question.

**The obvious check would have been worthless, and that is the interesting part.**
`deploy_smoke.sh` runs on the GitHub runner, authenticated as the **OIDC deploy
identity**, which has broad permissions on the resource group. A vault write from
there would have succeeded happily throughout the entire period B-125 was live —
a check that passes for a reason unrelated to the thing it checks, which is the
class that has now cost this project five separate incidents.

So the check runs **as the app**: a Container Apps job on the same user-assigned
identity as the API, executing `python -m dataagent.ops.selfcheck`, which goes
through the product's own `get_secrets_provider()` and `artifact_store()` rather
than calling the Azure SDK directly. A bespoke SDK call can succeed against a
resource the product cannot use.

* **Key Vault**: write, read, delete, and confirm gone. All four, because the
  product needs all four — `delete` is the verb *Secrets User* lacked alongside
  `set`, and rotating or removing a data source both delete.
* **Blob**: write and read. Two verbs, not four, because `ArtifactStore` has no
  delete — that is **B-021**, and asserting on a permission nothing needs would
  fail a deployment for the wrong reason. The probe is cleaned up best-effort and
  the `expire-artifacts` lifecycle rule catches what is left.

**Backend-agnostic on purpose**, so the local backends are the control: the suite
proves the check *can* pass against a real `LocalSecretsProvider` and a real
`LocalArtifactStore`, and fourteen tests prove it fails on each separate way a
permission can be missing. The sharpest is `silent` — a backend whose `delete`
reports success and keeps the value passes the write, the read *and* the delete,
and only the read afterwards catches it.

**B-129, found while listing what dev cannot do**: `storage.bicep` creates a
`documents` container that nothing writes to, reads from or names — knowledge
text lives in Postgres (revision 0016). An empty container costs nothing; a name
asserting a purpose the system does not have costs whatever the next person
builds on it, and that container is outside the retention policy and has no
tenant-prefix rule behind it.

---

## Every question failed, 2026-08-24 — a credential with no configuration (B-126)

The owner registered the F&B customer database through the UI, asked it two
questions, and both came back **failed / "The run could not be completed."**

**The cause.** `apps.bicep` set `OPENAI_API_KEY` and not one of `LLM_PROVIDERS`,
`LLM_MODELS`, `LLM_ROLE_MAP` or `LLM_PRICES`. `llm_providers` falls back to
`('openai',)` and `llm_models` to `{}`, so `registry.resolve` raised *"LLM_MODELS
names no models for provider 'openai'"* at the first model call of every run.

`ops/docker-compose.yml` **already carries that exact sentence**, from when the
same thing happened in Phase 7: *"a key that exists only in the developer's shell
reaches the smoke script and never the product."* Compose was fixed then. This
template was written afterwards and repeated it.

### Three things let it through, and they are the part worth keeping

**The guard was right and its coverage was the gap.** `MODE_REQUIREMENTS` and the
`degraded` probe shipped in #107 *for this shape of failure* — the docstring says
"a configuration that promises a mode it cannot serve" — and covered `auth_mode`,
`secrets_backend` and `artifacts_backend` and not the LLM. So `/healthz` reported
`ok` while every question failed. Nothing about the mechanism was wrong; its list
was short. **This is the third time that distinction has mattered** (B-124 is the
second), and it is worth stating because the instinct on finding a defect past a
new guard is to distrust the guard — here the repair is two names, not a redesign.

**`check_env.sh` passed**, because its couplings are `KEY=VALUE → COMPANION` and
this is not a mode being selected. Check 9 is the new shape — `RUN_REQUIRED`, what
a *run* needs in every mode — and per B-124 it was run against the unfixed tree
first and reported both missing names before anything was changed.

**The failure was silent where an operator looks.** `_record_failure` wrote the
reason to `agent_events` and called no logger: 300 lines of API log, no error.
The person asking saw a generic reason; the operator saw a healthy service; the
actual sentence needed the platform DSN and a SQL client to read. The owner's
instruction was to fix that in the same pass rather than defer it to WP12.3.

### And why nobody noticed the missing logging for eleven phases — B-127

**No test in this repository could observe a log line, and none had ever tried.**
Alembic's `env.py` called `fileConfig(...)`, whose `disable_existing_loggers`
defaults to **True**, so migrating at session start switched off every
`dataagent.*` logger for the rest of the process.

**The asymmetry is the finding.** An assertion that a line *is* logged fails
loudly — that is how this was found. An assertion that *nothing* was logged
passes **vacuously**, and that is exactly the shape a **control** case takes:
B-126's own "a successful run logs no error", written to prove the positive tests
are not satisfied by a runner that logs on every path, would have passed against
a runner that logged nothing anywhere. Fourth instance of a check that cannot
fail.

**Measured rather than assumed:** searching the suite for `caplog`,
`LogCaptureFixture`, `assertLogs`, `capsys`/`capfd` and logger patching returns
**one file — the one B-126 added**. So nothing was quietly passing; the capability
had never been used, and the unfalsifiability was latent until somebody tried.
Thirteen call sites across nine modules were unobservable, including the records
of last resort — `auth/audit.py` when an audit row cannot be written,
`db/security_events.py` when a security event cannot be written — where the log
line *is* the record.

**Not a production outage**, and worth saying so: nothing under `src/dataagent`
runs Alembic in-process, so the deployed API's logs were always real. What was
disabled was the ability to test logging. Fixed here, with
`tests/test_logging_is_observable.py` as the guard — and that guard requests the
migration fixture on purpose, because without it the guard passes against the
defect and becomes the fifth instance.

### What is not fixed

Nothing is deployed from this yet. Two PRs are open: **#107** (Key Vault Secrets
Officer, `MODE_REQUIREMENTS`, degraded `/healthz`, D-042) and this one, which is
branched from it because it builds directly on `missing_for_mode`. #107 merges
first.

---

## The first authenticated request, 2026-08-24 — and the rule that was never true

**The seventh dispatch went green on all twelve steps.** The web app serves the
Entra card, `/healthz` names the deployed sha, an unauthenticated `/v1/me` is
refused with 401, and the vault lists its three secrets. The owner signed in
successfully.

**Then the first authenticated request failed**, and the reason is not a
deployment defect.

`/v1/me` returns **500**, and FastAPI adds no CORS headers to an unhandled 500,
so the browser blocks the response and the client reports *"Could not reach the
API"* — which is misleading: the API is reachable and answered. The preflight is
correct (200, right origin, `authorization` allowed); CORS was never the problem.

From the API's own logs: `RuntimeError: DATABASE_URL is not set`, raised inside
`sweep_orphaned_runs` at startup and again inside `/v1/me`.

**`apps.bicep` sets `APP_DATABASE_URL` and never `DATABASE_URL`.** That omission
is real — and fixing it is refused, because of what it exposed.

### What it exposed

`system_session()` is the **owner** connection. Its docstring says *"for
migrations, bootstrap and admin jobs only"*. Eight call sites in six request-path
modules use it, including `auth/context.py`, which is every authenticated
request. CLAUDE.md's hard rule is *"Never collapse the two"*, and the API has
collapsed them on every developer machine since Phase 1 — invisibly, because
every `.env` sets both DSNs.

**Azure is the first environment that handed the API only the unprivileged DSN.**
A configuration omission surfaced a design violation; had the Bicep been
"correct", the rule would still be false and nothing would have said so.
`rls_proof` proves `dataagent_app` cannot cross tenants and says nothing about
which role the API connects as.

### The owner's decision, and the price

Offered the one-line fix — give the deployed API `DATABASE_URL` too — the owner
refused it: *"A makes the hard rule false in the environment where it matters
most, and a temporary owner credential is exactly what my dev rule exists to
prevent."* **Dev stays broken until the separation is real.**

### Why the fix is not simple

Three of the eight sites touch only non-tenant tables (`users`,
`security_events`) and move to the app engine unchanged. **The other five read
genuine tenant tables**, either across every organization or *before* the
organization is known — which is the authorization bootstrap, since `app.org_id`
cannot be set until the caller's membership has been discovered. Under the app
role those five correctly return nothing.

Filed as **B-123 (P1)** with the three candidate designs and what each costs.

---

## What `dev` is for, and the thing nobody should quietly decide later

**Owner's decision, 2026-08-24, and it is a standing rule rather than a note on
the current state:**

> Don't seed customer data or fixtures into Azure. Get sign-in and org creation
> working there and leave it — dev exists to prove the deployment path, not to
> host a demo. The local stack is where demos happen.

**So the deployed database stays empty on purpose.** No organization beyond
whatever a real sign-in creates, no data source, no catalog, no runs. That is not
an unfinished state waiting for someone to fill it; it is the state.

**The thing this exists to prevent.** The deployed API can answer nothing until a
data source is registered, and registering one means giving it **a credential to
a real database**. The path of least resistance, at some future moment when
somebody wants to show the product to somebody else, is to point dev at whatever
database is handy — a customer's, a copy of a customer's, or a "temporary" one
that outlives the demo. Once that credential is in Key Vault it is in a
subscription with a public web hostname, and the question of who may read it is
no longer a question about a laptop.

**None of the reasons this looks safe are load-bearing.** The credential would be
encrypted, held by a managed identity, and reachable only through a JWT-protected
API — all true, all irrelevant to whether it should be there at all. The rule is
about what is *stored*, not about how well it is guarded, which is the same
distinction the customer-data section of CLAUDE.md draws for the repository.

**What dev is allowed to prove**, and it is enough for WP12.4's gate: that the
pipeline deploys, that Entra sign-in works against a deployed redirect URI, that
an organization can be created, that migrations run inside the vnet, that
`dataagent_app` cannot bypass RLS on a real server, that quotas hard-stop, and
that a backup restores. None of those need a customer's data.

**The seed databases are deliberately local-only.** `seed-pizza-pg` and
`seed-fnb-pg` are compose services; nothing in `infra/` creates them and nothing
should. `make demo.setup` registers them by their compose hostnames and is
therefore local by construction — B-115's fix is also, usefully, a fence.

**If someone later needs a demo against a deployed environment**, that is a
decision with a name and an owner, not a convenience: it needs a database that
exists for the purpose, a written note of whose data it holds, and the owner's
explicit permission first — the same standard CLAUDE.md sets for any customer
sample reaching a tracked file.

---

## The second dispatch, 2026-08-24 — ten resources created, and the apps refused

**Sign-in succeeded.** The owner added federated credentials matching the
ID-qualified subject GitHub actually sends, for `dev` and `prod`, and left the two
old-format ones in place as a record. Four credentials, and the door opened.

**Then phase 1 created ten resources and failed on the eleventh.** Present and
billing in `rg-dataagent-dev`: `psql-dataagent-dev` (Standard_B1ms, 32 GB, PG 16,
**Ready** — the only always-on cost), `crdataagentdevv4ilto`,
`kv-dataagent-dev-v4ilto` (**empty**, seeding never ran), `stdataagentdevv4ilto`,
`log-dataagent-dev`, `appi-dataagent-dev`, `vnet-dataagent-dev`, the private DNS
zone and its link, and `id-dataagent-dev`. **Not created**: the Container Apps
environment, both apps, the migration job. **There is no dev URL yet.** Roughly
**$18-21 a month**, almost all Postgres and ACR.

```
Microsoft.App/managedEnvironments (2024-03-01) preflight:
LogAnalyticsConfiguration is invalid. Must provide a valid LogAnalyticsConfiguration
```

`apps.bicep` asked for `destination: 'log-analytics'` with a `customerId` and no
`sharedKey`, under a comment saying the environment writes with its own identity.
It does not — that destination requires both — so the environment could never
have been created as written.

**The owner chose `azure-monitor` plus a diagnostic setting over a one-line
`listKeys()`**: the second would have reinstated the single secret WP12.1
designed out *while looking like it had not*, and a credential-free template is
the point of this phase rather than a nicety. The environment is now also gated
on `deployApps || deployJobs` — it was unconditional, so a phase-4 resource
failed a phase-1 pass, which the owner called "a lie about where the risk is".

**Filed as B-122, and it is the more general finding.** Every `what-if` returned
`Succeeded` and every one also reported `NestedDeploymentShortCircuited` for
`apps`, `roles` and `postgres` — the three modules whose parameters come from
earlier modules' outputs. **Three of ten modules were never validated**, and they
are the app, its permissions and its database. No pre-flight check in this
repository could have caught this, which is B-120's shape one level out: parts
individually correct, never checked against each other, and only a real
deployment closes the gap. `infra/README.md` now says to read the `diagnostics`
array rather than `status`.

---

## The first deploy dispatch, 2026-08-24 — refused at the door, and nothing created

**`deploy.yml` ran and stopped at step 3 of 12.** Sign-in to Azure failed, every
later step is `skipped`, and no resource was created: no spend, no partial
deployment, nothing to unwind.

```
AADSTS700213: No matching federated identity record found for presented
assertion subject 'repo:50ur48h@130345252/DChat@1329894088:environment:dev'
```

**Nobody misconfigured anything.** The two federated credentials on
`dataagent-github-oidc` carry `repo:50ur48h/DChat:environment:dev|prod`, which is
exactly what #92's instructions specify and what GitHub documented. GitHub now
sends the **ID-qualified** form, so that renaming an owner or a repository cannot
silently point somebody else's workflow at this app registration. The repository
agrees it is on defaults:
`{"use_default":true,"use_immutable_subject":false,"sub_claim_prefix":"repo:50ur48h@130345252/DChat@1329894088"}`
— `use_immutable_subject` false, and the prefix applied is the immutable one
anyway. **The platform default moved after #92 was written**, which is the kind
of staleness no CI check in this repository could have caught: the instructions
were correct and the world changed.

**The owner's decision, 2026-08-24:** add credentials matching what GitHub
actually sends, for `dev` **and** `prod` at once so this is not rediscovered
later, and leave the two originals in place — they cost nothing and they document
what the old format was. Recorded in `infra/README.md` beside the commands, with
the instruction to *ask the repository what it will send* rather than copy a
subject out of a PR description.

**Blocked on the owner** for exactly two `az ad app federated-credential create`
calls; the re-dispatch is one `gh workflow run` afterwards.

---

## WP12.2 started, 2026-08-23 — the first thing that ever touched Azure

**Both owner prerequisites are done, verified rather than assumed.** The app
registration `dataagent-github-oidc` exists with two federated credentials —
`github-dev` and `github-prod`, both on `repo:50ur48h/DChat:environment:…` — and
the GitHub `dev` environment holds `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
`AZURE_SUBSCRIPTION_ID` and `POSTGRES_ADMIN_PASSWORD`.

**The open role-assignment decision answered itself in the subscription.** The
OIDC identity holds **Contributor**, **User Access Administrator** *and* **Key
Vault Secrets Officer**, all scoped to `rg-dataagent-dev`. So `roles.bicep` stays
in what the pipeline deploys; the alternative — three assignments made by hand
and the module dropped — is not needed. `rg-dataagent-dev` exists in
`southeastasia` and is **empty**; `Microsoft.App` and `Microsoft.DBforPostgreSQL`
are both registered.

**`what-if` succeeded.** `status: Succeeded`, `error: null`, **14 resources, all
`Create`, nothing modified and nothing deleted**: the budget, ACR, App Insights,
Key Vault, the user-assigned identity, the private DNS zone and its vnet link,
the vnet, the Log Analytics workspace, the storage account with its two
containers and the artifact lifecycle policy. Three `NestedDeploymentShortCircuited`
warnings — `postgres`, `roles`, `apps` — which is what Azure returns for modules
whose parameters come from an earlier module's `reference()` output, and is
expected rather than a fault.

Run with obvious dummies for the two secrets the params file deliberately does
not carry, because `what-if` creates nothing and a secure string is not evaluated.

**One item still owed by the owner, and it is not in this repo on purpose:**
`BUDGET_ALERT_EMAIL`. Wanted as a **`dev` environment secret** rather than a
value passed in conversation, so it reaches the workflow without ever appearing
in a tracked file or a transcript.

---

## The new machine, 2026-08-23 — the documented path walked from nothing

**No work package. A relocation**, and the second deliberate walk of the
quickstart from a machine that had never run this. The owner moved hosts,
carried `.env`, `.SampleData/`, `ops/.secrets/` and the artifact directories
across, and left the Docker volumes behind — so the platform database was empty:
no organization, no data source, no catalog, no run history. That is the exact
starting state **B-102** says nothing exercises automatically, and it produced
three findings, none of which any suite can see.

### What the machine was missing, and what that cost

`make` and the Docker daemon, both expected. `gh` was the one that matters,
because it is not in the README's prerequisites and every merge here needs it —
**B-116**. Node is **24.19.0** against `apps/web`'s `>=22 <23`, so every pnpm
command warns; both web suites pass anyway, which is why it is P3 and not
ignored. Docker Hub throttled the first two pull attempts to a dead stop before a
third succeeded — a network condition rather than a repo defect, recorded here
only so the next person reads a stalled `make up` as slow rather than broken.

### B-115 — the provisioner built an organization the product could not query, and is fixed here

The finding worth the walk. With an empty database, the repo offered exactly one
target that rebuilt the lost state: `make evals.setup`. It registers the pizza
database at `SEED_PIZZA_HOST=localhost:6543` — the host-side address, correct for
the harness that runs on the host and wrong for the API, which answers questions
**inside the container**. Proven rather than argued: from `dataagent-api-1`,
`localhost:6543` is `ConnectionRefusedError [Errno 111]` and `seed-pizza-pg:5432`
is reachable. The org, the verified read-only credential and the six-table
catalog were all real; every question asked of them in the browser died at the
connector.

**It is B-102's shape one layer in.** The target is not broken for someone who
knows it belongs to the host-side eval harness. It is broken for precisely the
person a provisioning target exists to serve, and nothing distinguished the two.

**Fixed rather than filed, on the owner's call** — *"a provisioner that produces
a broken org is worse than no provisioner"*. `make demo.setup` runs
`ops/seed/provision_demo.py` **inside the api container**, which is the only
honest place for it: registering a source also *connects* to it, so a host
process cannot check `seed-pizza-pg` and therefore cannot register it in good
faith. Both seed databases, compose names, discovered, profiled, embedded,
idempotent, and a database that was never seeded is **skipped and named** rather
than fatal.

`evals.setup` keeps the host address, deliberately: CI's evals job has no
container at all and `make evals` runs in-process on the host, so changing it
would have broken the harness to fix the browser. What changed is that it now
*says* which network it registered for and points at `demo.setup`, and its
`make help` line says HOST — where it used to read "create the org, register the
seed source, build its catalog", which is true of both provisioners and useful
about neither.

**The part that would have caught this** is the reachability report
`demo.setup` ends with: for every source in every organization, can the API open
a socket to the address that source is registered with. Its first run printed
`NO  evals / Demo: localhost:6543/pizza` — the defect, reported by the thing
built to report it. Only the organization the run provisioned can fail the
command; a host-addressed eval org is unreachable *correctly*, and failing on one
would make the target red on every machine that has run the harness, which is how
a check gets ignored.

### What is on the machine now

Three organizations. **`evals`** (`5eb716b6-…`) is the harness's, pointed at
`localhost:6543`. **`Demo`** (`92ba0ac2-…`) is the browser's: `Pizza demo`
(`seed-pizza-pg:5432`, 6 tables, 33 columns, 4 sensitive columns masked) and
`F&B sample` (`seed-fnb-pg:5432`, 35 tables, 280 columns, 43 relationships), all
41 cards embedded so retrieval is hybrid rather than lexical. **`Demo B-114
check`** (`d7a4deb3-…`) is what `make demo.setup` built from clean to prove the
fix; it was named before this entry was renumbered off B-114, which #94 took,
and can be deleted whenever the owner likes. The owner's own account is an
Admin of `Demo`; their `FNB` org, created by hand while this ran, has no sources.

### B-119 — the browser walk found a fabricated refusal, and it is P1

**The fourth question was the unproven one, and it earned its place.** *"Which
outlet wastes the most, and what does it cost?"*, asked of the F&B sample, came
back `answerable=false` with **no query executed** and this sentence: *"The
available reference also prohibits combining `fact_waste` with `dim_outlet`, so
the outlet name cannot be returned."*

**There is no such prohibition.** `fact_waste.outlet_key → dim_outlet.outlet_key`
is a declared foreign key at **confidence 1.00**; the run's own
`capability_checked` event lists 20 unreachable pairs and this is not one of
them, nor is it a chasm. It was `joinable` — a direct child→parent join. Run by
hand it answers **Outlet C, 3.398**. The cost half of the question was refused
honestly, because `est_cost` is NULL on both of `fact_waste`'s two rows; that
half is not the defect.

**The check was right and the answer was wrong**, which is the opposite of where
the fault was first expected to be. Nothing deterministic imposed the rule the
model asserted.

**D-026's mitigation was correct and insufficient.** `runner.py` already phrases
the chasm note as an instruction rather than a prohibition, and says why — a
model told only *"do not"* would refuse a question it could have answered. That
guards over-refusal of a **listed** pair. This is over-refusal of an **unlisted**
pair whose table names appear in the list: 20 chasms and 20 gaps, `dim_outlet` in
seven of them and `fact_waste` in thirteen, including `fact_member_visit ↔
fact_waste via dim_outlet` — both names adjacent inside a sentence about not
joining directly.

**It cannot appear on the pizza fixture.** Six tables make a short note; F&B's
thirty-five make forty pairs under two prohibition-flavoured headings. Every eval
and every gate demo so far has run against the narrow fixture, which is why a
second dataset was worth having and why the twentieth green eval said nothing
about this.

### The evidence, including the part that is not green

Fixtures rebuilt and matching their own truths: pizza at 71,798 orders with the
planted **−12.1%** overall decline and **−52.5%** on store 3 delivery; F&B at
112,327 sales and 51,356 stock moves, 27 tables and 8 views.

- `test.api` — **1547 passed, 20 skipped, 94% coverage** in 47m46s. Every skip is
  the SQL Server suite, which needs the on-demand container.
- `test.web` — **155 passed**. `lint`, `typecheck`, `check.status`,
  `check.backlog`, `check.env`, `check.infra`, `check.truths` — all pass.
- `evals` — **20/20** on the FakeLLM path.
- `test.web.e2e` — **8 passed, 1 failed, twice, on a different test each time**
  and the same hydration timeout underneath. Filed as **B-117**, which matters
  because the config already chose `next build && next start` to prevent this
  symptom and the symptom returned.

Three live questions through the real model, run inside the container so the path
is the product's own. *"How many orders were placed in July 2026?"* → **3718**,
byte-equal to `truths.json` `.orders.in_july_2026`. *"What were our best-selling
items?"* → `answered=False`, one model call, **no query executed**, and the
sentence names the missing table rather than guessing. *"How many sales are
recorded, and over what date range?"* against F&B → 112,327 over 2025, with the
retrieval step reaching the customer's own views.

---

## The gate walk, 2026-08-20 — two defects, and the gate not ticked

**The walk did what three suites and a green build did not.** Everything in the
work package was passing when the owner sat down with it.

### B-105 — the chart was wrong rather than plain, and it is fixed here

Four monthly bars, April to July 2026, drawn on a **continuous** temporal axis:
Vega ticked it by week — `Apr 05`, `Apr 12`, `Apr 19` — so four thin spikes sat
on a daily calendar and the space between them read as zero revenue. Confirmed
from the stored spec: `mark: bar`, `x: {"type": "temporal", "field": "month"}`,
four values on month-firsts, **no `timeUnit`**.

The owner's framing is the rule: *"this is the Q1/Q2 rule with real dates —
wrong is worse than absent, and `charts.decide` refuses the one and drew the
other."*

**The line chart from step 2 of the same walk had the identical encoding.** It
passed inspection only because a line implies no width, and so claims nothing
about the space between two points. One defect, one visible form and one not —
which is why the fix is keyed on the data rather than on the mark.

The grain is now read **off the values**: the coarsest unit every present value
sits exactly on becomes `timeUnit`. Off the values and never off the name, which
is the line this module holds everywhere else — `_kind` judges `order_date` by
what is in it, `axis_title` de-snake-cases rather than translating, `_is_number`
refuses to parse a numeric-looking string. Where there is no grain because the
dates carry a time, a `bar` gets an **ordinal** axis, which is not a compromise
but what a bar chart's axis is; a `line` over the same values stays continuous,
because spacing a working day's readings evenly would misstate when they
happened.

### B-106 — the answer card is a panel for the newest run — **the gate blocker, fixed**

The owner read the chart vanishing on the next message as the Phase 7
suppression rule outliving its reason. That is true of the card's *sentence* and
understates the rest: `replied` suppresses only the duplicated sentence, and the
disappearance is one layer up — `ConversationThread` holds **one** `run` and
renders **one** card, so a previous run's card is not suppressed, it is never
rendered. Chart, method line, limitations, findings, evidence controls **and the
trace** all become unreachable from the screen while remaining durable rows.

**The answer to the owner's question — card or bubble — is the card, and it
should become the assistant turn.** Moving the chart into the bubble fixes a
quarter of the problem, since architecture 4.2 makes an answer four things and
three of them still vanish; it saves no work, because the thread's messages
carry `content` and `run_id` and nothing else, so a chart in a bubble means
fetching the run anyway; and it puts the chart *outside* the card, which is what
B-048 refused. The sharper form: the bubble and the card are two renderings of
one thing, which is the only reason `replied` exists and was the root of the
Phase 7 gate defect.

**The owner made it a gate blocker** on 2026-08-20, on the demo rather than on
the code: *"a chart that disappears on the second question fails in front of an
audience, and 'chart renders' is not met by one that survives only until the next
message."* Built here. `GET …/conversations/{id}/runs` returns every run in the
thread oldest first; the screen holds them by id; each assistant message renders
as its run's card. **`replied` is gone rather than re-tuned** — it existed only
because an answer had two renderings, and now it has one. The plain bubble
survives as the fallback for a run that could not be fetched: the words are what
the reader came for, and losing them because a second request failed would be a
worse trade than losing the picture.

Two things worth keeping about how it was checked. **Three of the five new web
tests fail against the old screen**, and the two that pass are guarding the new
design rather than reproducing the defect — worth saying, because "five new
tests" and "five tests that would have caught it" are not the same claim. And the
counts are what is asserted, in the unit tests and in the smoke: *presence* passed
against the broken screen, because the newest answer always had its card.

### B-107 — a rule fixed at one of its two call sites is fixed nowhere in particular

Two findings on one execution, and **B-096's guard worked**: it keys on the
citation set, it lives in `_write_ending`, and the composed answer — a third
wording of the same numbers — was correctly not added. Both findings came from
the **loop**, where `state.add_finding` still compares characters. That is the
guard B-096 was filed against, one layer up, in the copy nobody changed.

**Copying the rule across would have dropped the better sentence, and the owner
asked to be told that rather than shipped it.** The two arrived from **one**
reflection — the events read `finding_added → finding_added → reflection` — in
the order enumeration-then-shape. First-wins keeps the numbers and discards the
trend, which is the worse of the two by **B-097**'s own rule that the prose
should give the shape and let a chart carry the detail; last-wins would be right
here and wrong the moment a model emitted them the other way round.

So the guard detects rather than chooses. `merge_by_evidence` **joins** findings
from one reflection that rest on the same executions into one claim — one
citation, one badge, the weakest of their confidences — and `state.add_finding`
takes the citation-set rejection for the other half, a *later* reflection
restating on evidence already concluded from, where the earlier finding stands
and the iteration counts as barren. Merged **before** anything is persisted,
because a finding row and its `finding_added` event are written together and a
later merge would rewrite a row whose event already said something else. Only for
findings that cite something: the empty set is shared by every uncited finding,
and keying on evidence there would collapse them all into the first.

### What the `web` job caught, and the gap that let it through

**`loadThread` could take the whole conversation down with it.** B-106's runs
request went into the same `Promise.all` and the same `catch` as the conversation
and its messages, so a failure there emptied the screen — questions, answers and
all. CI found it against the hermetic stub, which did not serve the new route
yet; the effect would be the same against a network blip or an older API. The
runs *enrich* the answers, the messages *are* the conversation, so the request now
fails to null and keeps whatever runs were already held. A unit test covers it,
and fails against the previous version.

**The gap is `preflight`'s, and it is the more useful half.** That target says
*"everything CI will run"* and prints *"safe to push"*, and it omitted
`test.web.e2e` — the one suite this broke. Everything else was green locally.
So the browser suite is in `preflight` now, which is the same rule the lint
recipes are written for and state in as many words: *what a developer runs must be
what CI runs, with nothing between them that could differ.* The compose smoke
stays out — it builds images and seeds a database, which is minutes rather than a
minute, and CI gives it its own job.

### What the `web-e2e` job caught on its first real run

**Red, and correctly so.** `PermissionError: '/app/ops/.secrets/secrets.json.8.tmp'`
— on a fresh checkout those bind-mounted directories do not exist, because they
are gitignored, so Docker created them as **root** and the api image runs as uid
1001. Invisible on the machine the smoke was written on twice over: Docker
Desktop ignores bind-mount ownership, and the directories had been there for
weeks. **B-102's shape again** — a precondition every existing machine already
satisfies, so nobody walks the path where it is missing.

`ops/docker-compose.smoke.yml` replaces those two mounts with named volumes for
the smoke stack only. The container creates them, so its own user owns them and
no host uid has to match; they go with `down --volumes`; and the walk stops
writing a credential into the developer's `ops/.secrets/secrets.json`, which it
never had a reason to touch.

**The overlay alone did not fix it, and the way it failed was the useful part.**
Docker seeds a named volume from the image's content and ownership *only when the
path exists in the image*; when it does not, the volume is empty and owned by
root. Same error, different mount type — and now reproducible on Windows, where
the bind-mount version never was. The api image therefore creates
`/app/ops/.secrets` and `/app/ops/artifacts` before its `chown`, which is where
that declaration belongs: the API writes credentials to one and stored results to
the other, and an image that does not admit to it leaves every mount over it a
coin flip. It does nothing for a bind mount, which never inherits image
ownership — **so the local stack has the same hole for any Linux developer whose
uid is not 1001**, which cannot be tested from this host.

> **`make up` may not work on Linux, and nobody here can find out.** `ops/.secrets`
> and `ops/artifacts` are gitignored, so a fresh clone has neither; Docker creates
> a missing bind-mount source as root; the api container runs as uid **1001**. On
> a host where those directories do not already exist and the developer's uid
> differs — a typical Linux desktop user is 1000 — registering a data source dies
> with `PermissionError` and every successful query dies writing its artifact.
> **Two conditions have hidden this**: development has been on Windows, where
> Docker Desktop's bind mounts ignore ownership entirely, and both directories
> have existed on that machine for weeks. Filed as **B-108** with the candidate
> fixes and what each costs. It wants somebody on Linux, not more thought here.

**Green afterwards: `web-e2e` in 2m23s, the walk itself 15.6s** — inside the
plan's ~5 minute budget, on a runner building both images from nothing.

### The one thing this host says and CI does not

Cold runs here are intermittent: **two of eight failed with `next dev` not
serving a framework chunk**, leaving the page unhydrated and parked on B-104's
sign-in card. Warm runs were 3/3, and the first clean CI run had none of it. So
the flake looks like this Windows host rather than the smoke, and the dev target
stays — changing what the smoke exercises on the strength of a symptom that does
not appear where it runs would be trading fidelity for nothing.

Two things were done rather than assumed. The walk's last step now **reloads
until the page is running** before it asks about the answer, so an unhydrated
page reports as one instead of as *"the answer never arrived"* — which is the one
thing it does not mean. And if CI ever does show this, the answer is already
written down: the hermetic suite chose `next build && next start` over `next dev`
for exactly this symptom, and its config says why.

One cold run also died on **B-028** (`ConnectionResetError [WinError 64]` inside
`alembic upgrade head`) before reaching a browser — the known Windows flake that
hit the second README walk too, and unrelated to anything in this work package.

---

## WP11.2b — the record (2026-08-20, #87)

**Nothing here is a to-do list.** What is outstanding is the **Next step** field
above. This exists because several of the findings below are not recoverable from
the diff, and one of them is about how the work was done rather than what it
changed.

### The compose smoke, and what it is allowed to mean

`apps/web/e2e-compose/smoke.spec.ts` drives the whole product in Chromium against
a stack `ops/scripts/web_smoke.sh` brings up: sign in through the dev issuer,
create the organization, register the seeded database, prove the credentials
cannot write, discover and profile the schema, ask, read the answer card, open
the query behind it, open the trace, reload. Everything in that chain is real
except the model.

**Two assertions carry the whole thing, and they pull in opposite directions.**
One is `71798` in the evidence table — the seeded fixture's own order count,
which nothing but a real query against a real database could produce, and the
only line in the file no stub could satisfy. The other is the scripted model's
exact sentence, which **only** the stub can satisfy: it is there so that a stack
brought up with a real key fails loudly instead of quietly spending the owner's
money and reporting a model's variability as a broken product.

**Its own compose project, on its own ports** (`dataagent-smoke`, web 3200, api
8200). Not politeness: a developer's stack holds the demo fixtures both README
walks depended on, and `down` on the shared project would take them with it. The
script exports `LLM_PROVIDERS=scripted` and blanks `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY` and `EMBEDDINGS_PROVIDER` — an exported variable beats
`--env-file` in compose's interpolation order, so the owner's `.env`, which says
`openai` and `entra` and carries a live key, cannot reach it. Blanking the
embedder matters as much as the model: **discovery embeds catalog cards**, so a
smoke that left it alone would bill for the schema it just read.

**Measured, cold, on this machine: 3m06 end to end** — build, migrate, grant,
seed 71,798 orders, walk, tear down. The walk itself is 11.7s; most of the rest
is `next dev` compiling each route on its first request. A warm re-run is 4.6s.

### Three failures while writing it, and only the third was mine to keep

**`DEV_ISSUER_URL` is not a browser address.** Pointed at the published port, the
walk signed in, rendered the profile screen, and then every authenticated call
came back 401 `jwks_unavailable` — because that variable is both the `iss` the
dev issuer stamps *and* where the API fetches its own JWKS, and it does that
**from inside its container**, where the only port that exists is 8000. A failure
that looks like a bad token and is a wrong port.

**A page can be served long before it is running.** `next dev` compiles a route's
client bundle on first request, so the server's HTML sits on screen with no React
attached: `fill` landed in a DOM nothing was listening to, the click reached a
button with no handler, hydration then reset the field, and the report read
*"signing in does not work"* over a screenshot of an untouched form. The walk now
waits for the app's own proof of life — the health widget reaching a verdict,
which is also the first thing that must be true anyway.

**And the third is a product defect: B-104.** Navigating by `page.goto` — a full
load each time — the walk reached a conversations page rendering the **sign-in
card** and waited thirty seconds for a heading that was never coming. `who`
starts null and is restored by a round trip, and all nine screens read null as
*signed out* rather than as *not known yet*. This is exactly the defect WP11.2a
fixed once, for the conversations list that said *"Nothing yet"* while it was
still loading — except this one does not say *you have nothing*, it says *you are
nobody*, and then heals if you use the form. Filed, not fixed, on B-101's
reasoning. The smoke now clicks through the product the way a person does, which
is both more faithful and steadier.

### The `web-e2e` job, and one test that was checking its own wording

The job is path-filtered on `apps/web`, `apps/api`, `ops` and the workflow — it
is the one job that would notice the two halves disagreeing. Steps are gated, not
the job (**D-007**), so the check always reports. The script prints the api and
web logs itself when the walk fails, **before** the teardown: a workflow step
could not, because by then there would be nothing left to ask.

Writing it turned up that `test_it_answers_every_role_the_loop_asks_of_it`
asserts on the scripted provider's own strings — `'"done": true' in reflect.text`
— which is a check on this module's wording, not on the contract. It would pass
unchanged while `Plan` gained a required field and every smoke run died in
`structured.py`. There is now a test that **parses each reply with the model the
loop actually passes as `schema=`**, imported from `agent/` on purpose: the
contract is with those four classes, and a copy of the shape restated in the test
would be free to agree with nothing.

### Two more quickstart defects, from walking it once more

Both in prose, both invisible to a reader who already knows the product. The
**Status** section still said *"Phase 0 — bootstrap… there is no runnable
application yet"* directly above a quickstart that works — a non-developer
reading top to bottom would stop there. And step 4 said **"Data sources → Add"**;
there is no Add, and there is no visible route to data sources at all until you
know that the **Members** button is how you enter an organization. That is the
third and fourth defect of the same family this work package has found, after the
"Discover" button and the omitted **Test connection**.

### B-103 — the blocker, cleared

**The gate criterion has been met live for the first time.** A clean stack, a
fresh `.env`, a real model, and a money column:

```
status : completed
method : 2 queries over 2 steps, against orders, payments, staff, stores and customers.
CHART  : DRAWN, mark = line   axes: Revenue month / Monthly revenue
  points: 18 -> {'revenue_month': '2025-02-01', 'monthly_revenue': 119558.51}
```

`monthly_revenue` is a number, not `"119558.51"`. Before this, charts had only
ever been demonstrated on a `count(*)` — WP11.1's live proof in #82 was *number
of orders by month*, and the demo had chosen the one shape that worked.

The cause was at the artifact seam: a `Decimal` crossed JSON as text, so
`_is_number` said no. The fix records `column_types` where the writer still has
the Python objects, and the chart rebuilds the Decimal from that declaration
rather than deciding that numeric-looking text is a number — which would make a
measure of a postcode.

### The second failure, which the suite could not see

**Worth keeping, because it is the more useful half.** The first re-walk got
*further* and still failed:

```
TypeError: Object of type Decimal is not JSON serializable
[SQL: UPDATE agent_runs SET chart=$1::JSONB ...]
```

Rebuilding Decimals in the frame put them into the spec's inline values, which
are stored as JSONB. **The fix had moved the defect one seam later and would have
shipped.** It is the same shape as the original defect, one layer along — and the
round-trip tests written *for* the original stop at `decide` and never store what
it returns. A corpus that had just learned to cross one seam stopped one seam
short of the truth.

### The quickstart, and what walking it proved

An OpenAI key is now stated as required, with rough cost and where to put it: a
keyless path would answer from a script, and the product is the agent.

Walking it found **five** defects, four of them in prose written minutes earlier
by someone who had just read the code:

1. **B-102** — `make db.setup` crashed from a clean state. Sourcing `.env` strips
   quotes, so `LLM_ROLE_MAP={"compose":"small"}` arrived as `{compose:small}`, and
   environment beats dotenv in pydantic's order. **Step 2 of the documented path.**
2. `docker compose restart api` does not re-read the env file, so the secrets key
   never arrives and registering a database 500s. **This predates the rewrite** —
   the old quickstart said "restart the api" too.
3. A **"Discover" button that does not exist**; the controls are *Refresh
   catalog* and *Profile columns*.
4. **Test connection** omitted, and it is required: until it passes, refreshing
   the catalog refuses.
5. B-103 itself.

**B-102's lesson is the durable one.** That crash stayed hidden because
`make migrate` was a *second route around the broken one* — and it is what
everybody actually ran, because it is what you reach for mid-development. The
documented path had a working shortcut beside it, so the broken one was never
taken and never reported, and the people the documented path exists for are by
definition not the people who know the shortcut. **Walking the documented path is
the only thing that has ever caught this class**: not one of these five was found
by a test, a review or a re-reading.

### Local state, and the traps that are still traps

**Run `make migrate` before anything.** Three revisions landed in WP11.2a and are
on `main`: **0025** (`agent_runs.method`), **0026** (`conversations.archived_at`)
and **0027** (`org_recovery_grants`). A missing revision surfaces as a CHECK
violation mid-run rather than as a migration error.

**The scripted provider is a stub in the shipped image.** `LLM_PROVIDERS=scripted`
selects it; two guards refuse it in production, one at boot and one at first use.
Do not weaken either without reading why they are separate — the boot check reads
configuration and cannot see a runtime registration; the other reads the instance
and cannot see configuration that is never built.

**The owner's demo fixtures survived both walks and are still here**: `waste_cost`
active at v6 with its full history, five chart-carrying runs, 366 conversations.
Both walks ran under a **parallel compose project** (`-p dataagent-clean`) with
`make down` first, so the volumes were never touched. That method has one cost
worth knowing: `db_setup.sh`'s grant step targets the default project, so it
fails under a parallel one and has to be applied by hand. That is the method, not
a defect.

**B-028 fired again** during the second walk — `make db.setup` died on
`ConnectionResetError [WinError 64]` and succeeded on a retry. Its traceback and
the cheap innocence check are in its row.

## WP11.2a — polish, B-017, B-100, B-098 (2026-08-20)

**The work package was split first.** WP11.2 carried seven workstreams into one
gate PR, which is a diff nobody reviews properly. The owner split it: **11.2a**
is the product work, **11.2b** is the smoke and the gate, and sign-off is on the
second. **B-020 and B-061 were dropped** — B-020's own entry had already decided
it needs its own reviewed PR, since it is a `dal/validator.py` change carrying a
collision rule and dal/ requires human review; B-061's currency half has no
honest route until B-059 or a per-source setting exists.

**B-017 — a way back in.** An Admin arms a recovery grant, keeps the token
outside the product, and whoever holds it can claim Admin of that one
organization. The plan preferred this over a break-glass platform-operator role
because it adds **no new privilege**: the organization creates its own way back
and the platform gains nothing to defend. It is deliberately *not* an ordinary
invitation, and that is the whole feature — `accept_invitation` adds a membership
only where there is none, so a Reader redeeming an Admin invitation stays a
Reader, and the locked-out person is usually already a member. The test that
carries it reproduces the bricked state (every Admin demoted, the product
refusing the remaining Reader an invitation) rather than asserting a 200.
`ops/scripts/set_role.sh` now says in its own header that it is no longer the
answer.

**D-039 — archive, not delete.** A conversation is the root of its runs, their
events, their findings and their query executions. Two conditions came with the
owner's decision and both are in the code: the control **says Archive**, with a
test asserting the word and asserting no control says "delete" — *"a button that
says delete and hides instead is a lie to the user"* — and **true erasure is
named as a Phase 12 retention story** rather than left implied. The test that
carries the decision asserts against the *record*, because `archived_at is not
None` would pass just as happily on an implementation that had cascaded
everything away.

**B-100 — the method line, surfaced.** Migration 0025, written from what
`assemble` already built, rendered above the limitations and styled quieter:
"how did you get this" is not a doubt about the answer, and a method line that
read like a caveat would make every answer look qualified.

**B-098 — axis titles.** A de-snake-casing, and deliberately not a dictionary:
expanding `qty` or deciding `dt` means date would be the platform inventing
meaning it does not have. The catalog's column description was considered and
**not** used — it is prose written for another purpose and is often a paragraph,
which is not an axis label.

**One empty-state defect found while working.** The conversations list started at
`[]`, so every visitor was told *"Nothing yet"* for as long as the fetch took —
an empty state standing in for a loading one, which reads as "you have no
conversations" rather than "wait". Fixed, with a test that holds the fetch open.

### What is not in 11.2a, and is not in the gate either — B-101

The catalog and members **rounding** and the **mobile layout pass** named in the
plan are *not* done. Members gained the recovery panel and both screens gained
their loading states, but nobody has sat down with these screens at 375px — the
app has three `@media` rules in total.

**This is now B-101, and it is deliberately not in WP11.2b** (owner,
2026-08-20), on the reasoning that dropped B-020: the gate PR already carries a
new CI job, a scripted provider in the shipped image and the sign-off walk, and a
layout pass touching every screen reviews badly beside that. The sharper half of
the reason is that it is *"the one item where done is my judgement in a browser
rather than a test"* — a responsive pass has almost no assertable surface, so
unlike everything else in 11.2a it cannot ride along on a green build.

> **The Phase 11 gate does not cover small screens.** Sign-off on WP11.2b says
> the demo works, the chart renders and the stack wires up end to end. It says
> nothing about whether any of it is usable on a phone. That is B-101, and it is
> open.

## The session of 2026-08-20 — B-095

**One item, and the entry understated it.** B-095 was filed as a missing
limitation: a run whose every query failed carried `limitations: []` while the
answer described the failure as an empty result. That half is fixed — an answer
now says how many of the run's queries failed and quotes the connector's own
sanitized message. Tracing it found a second half the walk could not see.

**The true sentence already existed, and the runner discarded it.** `research`
stops on an unrepairable failure and writes the refusal there, naming what
failed. The runner's no-compose test asks whether anything *ran*
(`not state.executions`) — and a failed execution is still an execution, so the
run fell past it into `_compose`, which hands a model a list of refusals, no
results, and an instruction to answer. A model given no evidence does not
decline; it describes the absence as a finding. So the platform knew, wrote it
down, and paid a model call to replace it with a guess.

**The owner's decision is D-038: refuse it, do not compose.** The alternatives
were to force `answered=false` after composing anyway, or to add the limitation
and leave `answered` to the model. The second fixes the silence and keeps the
misdirection; the first buys question-shaped prose in the one case where the
model has nothing to write from. Refusing is also a model call cheaper — the
only one of the three that costs less than the defect.

**A test was passing over a failed run.** The existing test of this path
asserted `answered` and `llm_calls` but never `status`: composing raised inside
the FakeLLM, so the run ended **`failed`** and the suite was green anyway. With a
real model there is no exception — it composes, which is the live report. The
new test asserts `status` first. Worth generalising: *a test that asserts on the
shape of an outcome and not on the outcome itself will pass through the failure
it was written for.*

**Verified live, on the environment that produced the report.** The smoke
script run from the **host** against the F&B demo — registered with the compose
hostname `seed-fnb-pg`, which does not resolve there — is B-095's original
trigger. It now ends `status completed`, `answered False`, **1 llm call** (the
plan; no composing call), on *"I could not answer that from this data. The query
could not be run: gaierror: [Errno 11001] getaddrinfo failed"*, with the
limitation stored beside it. The healthy path was checked through the rebuilt
api image against the pizza demo: a real answer, and the failure note correctly
absent. The live run also caught what the tests did not — *"nothing here rests
on what **they** would have returned"* over a single query. The assertions were
on the opening clause; the agreement is on the count that failed.

**One new entry: B-100.** Fixing `method_note` turned up that nothing reads what
it produces. Architecture 4.2 makes the *method* one of the four parts of an
answer — one line on how it was reached, for a reader who will not open the SQL —
and `assemble` builds it, sets it on `ComposedAnswer.method`, and `_write_ending`
drops it: no column, no field on the run view, nothing in the card. Filed rather
than fixed, because the choice is the owner's: surface it, or accept that
`supported_by` and B-034's evidence panel already answer *"how did you get this"*
and **delete** it. Computed, tested and discarded is the one state it should not
keep.

**B-028's failure text, captured at last.** The full suite went red three times
in this session, in three *disjoint* subsets, all inside whichever file was being
torn down at the time. That is B-028 — open since 2026-08-14, whose entry records
that the traceback had never been caught. It has been now:
`OSError: [WinError 64] The specified network name is no longer available`,
through `ConnectionResetError`, in `_temporary_database` at the line the entry
guessed — the `DROP DATABASE … WITH (FORCE)`. So it is the **teardown connection**
resetting, not the tests. Added to its row, along with the cheap way to prove a
red local run innocent: `git stash && pytest <file>` reproduces it on clean code
in a couple of minutes, which is what settled it here. Frequency tracks host
load; these sightings followed a container rebuild and concurrent suite runs.

**Two smaller things, in the same pass.** `method_note` said *"Answered without
running a query"* over two failed executions — a failure described as a choice,
one field above the limitations. And `ExecutionRef.error` is set **only** where
rewriting could not have helped: the loop records a policy refusal as a failed
execution too, and the next planner routinely corrects it, so keying the note on
`ok` alone would caveat a self-correction on a large share of healthy runs.

## The session of 2026-08-19 — the record, not a to-do list

**Nothing here is outstanding.** Where to pick up is the **Next step** field
above. This section exists because the reasoning behind several choices is not
recoverable from the code, and because what the session *cost* is worth knowing
before repeating the shape of it.

### What shipped

| PR | What |
|----|------|
| **#79** | B-088's web half — editing, retiring and history on the Definitions screen |
| **#80** | B-094 raised to P1, record only |
| **#81** | **B-094** — a retired definition can be found and brought back (revision 0023) |
| **#82** | **WP11.1 — charts** (revision 0024), and **B-096** folded in |
| **#83** | B-097 and B-098 filed and assessed, not built |

**WP11.1** gives an answer a chart, drawn in the browser from a spec the server
built, inside the answer card with its spec openable the way the SQL is (B-048).
A chart that *cannot* be drawn says why, in the chart's own place, with the
number that makes the refusal actionable.

**B-094** closed a dead end three correct rules had created between them: accept
takes only proposals, edit takes only active ones, and an import skips a name
already held — so a mis-clicked **Retire** was recoverable only in `psql`. The
owner left `waste_cost` retired on purpose so the first un-retire would be
through the product; it came back at **v6**, recorded as `reinstated` rather than
as another edit.

**B-096** was the same claim recorded twice — see the decisions below.

### What it cost, and where the time went

**The CI finding.** The `web` job hung for **1h 58m** on a PR whose lint,
typecheck, tests and build had all passed. `playwright install --with-deps` runs
apt as root, and apt was stalling on GitHub's own Ubuntu mirror — the log reads
`Ign: http://azure.archive.ubuntu.com` on repeat, then seven silent minutes.
Dropping `--with-deps` took the whole job to **1m29s**: the runner image already
carries the libraries Chromium needs, so the browser now comes from Playwright's
CDN and no mirror is consulted. The step and the job also carry timeouts now,
because *"do not depend on a mirror"* and *"do not burn six hours finding out"*
are two different lessons.

**Rebasing a stack.** Four PRs touching STATUS, BACKLOG and CHANGELOG conflict
with each other by construction, and each merge invalidates the next branch's
copy. After a **squash** merge, a stacked branch needs
`git rebase --onto main <old-base>`: a plain rebase replays commits whose content
is already on `main` under a different hash and conflicts with itself.

**Two verification habits that failed, and how.** The B-044 recipe greps a served
chunk for a string from your own source — it proved the chart UI reached the
browser and said nothing about whether `vega-embed` resolved, because
`node_modules` comes from the image and `pnpm add` ran on the host. And a browser
assertion written for a `canvas` found nothing, because vega renders these as
SVG; had it been written loosely enough to pass, it would have shipped an empty
box. Both are now in CLAUDE.md and in the tests respectively.

### The decisions a fresh session cannot reconstruct from the code

**Charts — all three placement choices are in D-037**, with the reasoning: the
request rides on `FinalizeIn` rather than `Plan` (once per run, +339 tokens,
rather than once per step) or the runner alone (`charts.decide` can refuse an
impossible chart but cannot know *which* chart answers the question — that is
B-060's silent choice); the outcome is stored on the **run** rather than the
finding, because a refusal can exist on a run that reached no finding; and a
refusal renders in the **card's chart slot**, never in `limitations`, whose
accessible name is *"What this answer does not establish"* and whose every other
member bears on whether the answer is true.

**No Recharts** (owner, 2026-08-19). The server-built validated spec is what
makes the browser test *"drawing the chart reaches nothing outside the page"*
true. A client-side chart library builds the picture from data and dissolves it.

**Tailwind + shadcn is a separate scoped decision that has not been made** —
**B-099**. The owner's instruction was polish within the current system, and not
to start it inside WP11.1.

**B-096 keys on the citation set, not on text.** The Phase 7 rule is *one claim
once*; the guard compared characters, so the composer rephrasing a finding into
an answer defeated it. Two claims resting on exactly the same executions are one
claim, whatever words they use — which is the rule `mark_cited` already followed
one line below. Equality rather than overlap, deliberately: an answer that
synthesises two findings has a union nobody else cites and is a new claim.

### Local state, and the traps that are still traps

**Run `make migrate` before anything.** Three revisions landed this session:
**0022** (`semantic_definition_versions`), **0023** (`reinstated`) and **0024**
(`agent_runs.chart`). A missing revision surfaces as a CHECK violation mid-run
rather than as a migration error.

**The local stack holds real state worth knowing about.** `waste_cost` on the
F&B demo is active at **v6** with a full history — it is the fixture that proved
B-094 and reads as a life: created, edited, retired, reinstated. The pizza demo
has a conversation carrying a rendered chart. The F&B **warehouse** (the
`fnb-gate` org) was re-profiled after B-092, so its cards carry value shares; the
F&B **demo** in the owner's own org was not, and still shows the old
`examples: A, B, C` line until somebody profiles it.

**`agent_smoke.py` runs on the host and cannot reach a container-registered
source.** The F&B demo's host is `seed-fnb-pg`, a compose network name, so a
host-side script gets `getaddrinfo failed` — which is how B-095 was found, since
the answer reported those failures as *"no data was returned"*.

**Three standing traps, unchanged.** `docker compose … restart web` after editing
the web app, and verify what is *served* rather than what is on disk. A **new web
dependency** additionally needs `--build`, and the grep recipe will not tell you.
The environment the container gets is not the environment you have — that is
B-090, now guarded by `scripts/check_env.sh`, which fails the build when a
documented key reaches no service and nothing says why.

## Phase 11's first four items, as they merged — the record, not a to-do list

**Nothing here is outstanding.** Where to pick up is the **Next step** field at the top of
this file; this section is how the code got where it is.

**Phase 11's first three items are merged**, in the order the owner set:

| PR | What |
|----|------|
| **#75** | B-088, API half — edit, retire and version a definition (D-036, revision 0022) |
| **#76** | B-090 — the environment guard, and the six variables it found on its first run |
| **#77** | B-060 — reproduced and diagnosed; filed B-092 and B-093 |
| **#78** | B-092 and B-093 — the two fixes the owner chose. **This one** |

**What is left in Phase 11**, in order:

1. **B-088's web half** — editing and retiring on the Definitions screen. #75
   shipped the routes; nothing in the product calls them yet, so an Admin still
   needs curl to correct a definition. B-088 stays `[ ]` until this lands.
2. **WP11.1** — the chart tool, carrying B-048.
3. **WP11.2** — polish and Playwright, the gate, carrying B-017, B-061, B-020.

**Two things this session learned the hard way.**

*The status guard keys a tracked item on its `B-###` and reads the last line
carrying it*, so splitting B-088 into `B-088a [x]` and `B-088b [ ]` made B-088
read as regressing from signed-off to open on any branch built from that one. A
split is a way of sizing PRs, not a way of numbering work: **one backlog id is
one checkbox**, and it stays open until every half of it has merged.

*Four PRs touching STATUS, BACKLOG and CHANGELOG will conflict with each other* —
every one of them edits the same three files, and each merge invalidates the
next branch's copy. Rebasing a stacked branch after its base is **squash**-merged
needs `git rebase --onto main <old-base>`: a plain rebase replays commits whose
content is already on `main` under a different hash and conflicts with itself.

**One operational step #78 asks for.** Rewriting a card is not a migration
(revision 0013 settled why), so an existing catalog carries the old card text
until its data source is profiled again — an existing demo shows the old
`examples: A, B, C` line until then. The F&B warehouse has already been
re-profiled locally; nothing else has.

## How Phase 11 opened — the record, not a to-do list

**Nothing here is outstanding**, and its PR table is two sessions old. Kept for its three
warnings, which are still the three warnings.

**Phase 10 is signed off and merged. Phase 11 has since started** on the owner's
instruction of 2026-08-18, in the order they set: **B-088, then B-090, then
B-060, then WP11.1.** The paragraphs below were written when nothing was in
flight and are kept because their three warnings are still the three warnings.

**Do this first:** the ritual in plan §7.1 — `git fetch --all && git checkout
main && git pull`, then `gh pr list`. Then read the **Next step** block at the
top of this file, which names the two P1s Phase 11 opens with and why each is a
guard rather than a feature.

**Three things a new session will get wrong without being told.**

1. **`make migrate` before anything runs.** Revision **0022**
   (`semantic_definition_versions`, B-088) is the head as of this line, and a
   missing revision surfaces as a CHECK violation mid-run rather than as a
   migration error.
2. **`docker compose … restart web` after editing the web app**, and verify what
   is *served* rather than what is on disk — the recipe is in CLAUDE.md. This
   caught me twice in one session, once while proving a fix the owner was
   waiting on. The container had the new bytes and Turbopack had not
   recompiled them.
3. **The environment the container gets is not the environment you have.** That
   is B-090, unfixed. `EMBEDDINGS_*` was missing from compose for the whole
   phase, so nothing embedded in the product while everything embedded on the
   host — and no test could see it.

**What the phase cost, and where the value came from.** Six defects were found
by *running* the product rather than reading it or testing it, and none was
reachable from a green suite: **B-083** (a definition bound the critic and never
reached the model), **B-085** (an imported definition answered only to its key,
so the import was inert), **B-086** (the container had no embedding model),
**B-087** (the import screen could not carry the customer's names, and nothing
said when a question matched none), plus B-073 and B-018 earlier in the phase.
**B-083 is the one to remember**: it would have made the gate demo pass for the
exact opposite of its intended reason, and seventeen tests written for that rule
could not see it because they all tested enforcement and none tested
communication.

## How Phase 10 was finished — the record, not a to-do list

**Nothing here is outstanding.** Six PRs closed the phase: #67 (B-073),
#68 (B-018), #69 (WP10.2a), #70 (WP10.2b), #71 (WP10.2c) and **#72 (WP10.2d,
the gate)**, merged and signed off on 2026-08-18. `main` is at the #72 merge and
every phase branch is deleted. Kept because the reasoning is worth more than the
outcome: each numbered item below records what the gate owed and what settled
it, and §3 is the argument for the habit that found six defects a green suite
could not.

### 0. What the gate owed, and what closed each item

What is already on the branch, tested and green:

* **B-079 / D-034** — an unresolved critic block is the answer's first
  limitation and caps its confidence.
* **B-059's import service** — `semantic/proposals.py`: a customer's metric table
  becomes proposals, read through the DAL, `accept` is where an Admin adds the
  filters that make a definition bind.

**What the gate still owes, in the order that makes sense:**

1. ~~**Routes** for definitions and proposals~~ — **done.**
   `semantic/routes.py`: list, list proposals, create, import, accept, reject,
   all six **Admin**, all six in the role matrix and its snapshot
   (`admin: allow`, `contributor: deny(403)`, `reader: deny(403)`, a pure
   addition — no existing route's access moved). `binds` is on the wire so a
   screen never has to infer "this constrains the SQL" from an empty array.
   Import returns **201 with an empty list** when everything was already known:
   a request that succeeded and proposed nothing is not a wrong mapping.
   Thirteen route tests, and the one that matters asserts what the
   **definitions** list says after an import — still empty — rather than what
   the import returned, because activating on import is the shortcut a product
   surface would take. Locking the *read* side to Admin is deliberate and is
   the weaker half of the argument: filed as **B-082** rather than defaulted.
2. ~~**The admin review UI**~~ — **done.** `screens/definitions.tsx`, at
   `/orgs/{org}/data-sources/{id}/definitions`, linked from the data-source card
   for Admins only. A proposal shows what it says, its expression, its synonyms
   and **where it came from** — the customer's own table, out of the provenance
   the import recorded. B-008 held twice over: a Reader sees one sentence
   explaining why not, an unknown role fails closed, and the screen **fetches
   nothing** for either, because a 403 for an action never offered is an audited
   denial nobody attempted. The load-bearing detail is D-033 made visible: the
   accept button reads **"Accept and enforce"** or **"Accept as prose"**
   depending on whether a filter is staged, and the sentence above it says what
   that will mean — *"a query that ignores it is blocked"* against *"binds
   nothing… an answer resting on it will say so"*. Two defects the tests found
   and no reading would have: the same error rendered in two cards at once, and
   two different controls both labelled "Table". 15 web tests; suite now 107.
3. ~~**`semantic/verified.py`**~~ — **done.** Admin-approved question→SQL
   pairs, matched to a new question **lexically** — free, deterministic, no
   embedding and no provider dependency — and rendered at **L3** beside the
   definitions, since an Admin approved them and the validator judged them. Two
   properties carry it. **It informs and never binds**: no critic rule reads an
   example, deliberately, because a question that merely resembles one is not
   its question and demanding the same SQL would be a false block on a correct
   answer — so `VerifiedFrame` says *"examples, not answers"* where
   `DefinitionFrame` says the query is checked. And **an example is validated by
   the validator that guards execution**, so an Admin cannot bless a statement
   the platform would refuse: an approved query naming a table that does not
   exist is a worked demonstration of hallucination sitting in the prompt.
   Nothing is executed to approve one. The matcher is reluctant on purpose —
   two shared content words minimum, three examples maximum — because a wrong
   example is worse than none. Revision **0021**, RLS + `TENANT_TABLES` +
   rls_proof extended, three Admin routes in the matrix. The screen is
   **B-084**; the grounding is what 5.4 and this gate asked for.
4. ~~**B-070**~~ — **done, and proved live.** Same question, same model, one
   definition apart. **Before**: `FROM customers AS c LEFT JOIN orders o` —
   denominator 8000, `[FAIL] 0.984471 was in no result`, 5,960 tokens.
   **After**: `FROM orders GROUP BY customer_id` — denominator 7985, `[PASS]`,
   6,102 tokens. The definition **changed the generated SQL**, which is the
   phase's whole claim, shown on the fixture this project designed rather than
   on a customer's warehouse. It carries **no required filters and does not
   pretend to**: the ambiguity is in which rows are counted, and no predicate
   expresses *"customers that appear in orders"* — so it informs and does not
   bind, which is the honest shape (D-033). The fragile part is reachability and
   it has tests of its own: nobody types `repeat_rate`, so the match is on the
   synonym *"ordered more than once"*, and rewording golden #10 would undo this
   silently with every scripted run still green. **This only worked because
   B-083 was fixed first** — before that the definition reached the critic and
   never the model.
5. ~~**The live walk against the F&B source**~~ — **done, and the answer
   changed.** Against the real 112k-row warehouse, through the real routes.

   **Before** — *"Which item brings in the most sales revenue, and how many units
   of it did we sell?"* →
   **"Ayam Penyet Set brings in the most sales revenue, with 0.00 units sold."**
   `limitations: []`. Correct SQL, business nonsense, and **no caveat at all** —
   B-059's finding reproduced exactly, four months of revenue attached to zero
   units because that item's sale lines are `parent_zero_qty` rows carrying money
   with no quantity. The customer's own view `v_menu_performance` sums `qty`
   across every row role, so their own reporting carries the same hole.

   **Import** — `meta_metric` → **18 proposals**, each with its provenance
   (`public.meta_metric`), none of them binding anything.

   **Accept** — `prep_quantity` blessed as Admin with one required filter,
   `fact_sale.row_role not_in (parent_zero_qty)` — the rule the customer's own
   `meta_gate` table asks about in English as **G_ROWROLE** and marks
   `enforced = 0`. Response: `"binds": true`.

   **After** — the same question in the organization's own metric vocabulary →
   **"Ayam Penyet Combo brings in the most sales revenue, at 157,258.26. Its prep
   quantities by weekday are 21.48, 20.37, 18.42, 19.50, 18.29, 19.87 and 22.35
   units."** The executed SQL carries
   `WHERE "fs"."row_role" <> 'parent_zero_qty'`, inside a CTE.

   **A definition an Admin accepted changed the SQL the model generated, on a
   customer's warehouse.** That is the phase's whole claim and it is no longer an
   assertion. The walk also found **B-085** — see below; without that fix the
   import is inert and this step would have proved nothing.

   **The owner's own walk produced a second, stronger outcome** on 2026-08-18,
   and it is worth recording above the one I scripted for. Asked the same
   question, the run applied the filter to the units half only and answered
   *"I can't determine the answer… the available preparation data is for Ayam
   Penyet items, but it does not establish that one is the top-selling item."*
   `applied_definitions: ["prep_quantity"]`, and `row_role <> 'parent_zero_qty'`
   in **4 of 4** executed queries. The reason is in the data: **`Ayam Penyet Set`
   has 1,581 sale rows and every one is `parent_zero_qty` with `qty = 0`** — no
   component or standalone rows exist for it. Without the definition, `SUM(qty)`
   over those rows is `0.00` and the agent reports it as fact; with it, those are
   exactly the rows excluded, nothing remains to count, and the agent says so.
   **A confident wrong number became a truthful refusal** — Phase 9's thesis and
   D-033's claim arriving in the same answer, which is a better demonstration
   than the changed number I had recorded. The two outcomes differ only in where
   the model scopes the filter across a question mixing revenue with units, and
   that ambiguity is filed as **B-089**.
6. **A run the critic could not talk out of a bad draft** — **accepted as
   covered by test, not demonstrated** (owner, 2026-08-18, the same disposition
   B-053 has). Four live attempts against the F&B warehouse produced three
   outcomes and none was B-078's criterion:

   * the plain question — the model **complied**, writing
     `row_role <> 'parent_zero_qty'` into a CTE unprompted;
   * told explicitly to include every row, it **refused and named the rule** —
     *"the authoritative prep_quantity definition requires excluding fact_sale
     rows where row_role is parent_zero_qty"*;
   * asked about an item whose every row that filter excludes, it **refused
     honestly** rather than reporting the `0.00` it used to report as fact.

   No blocking critic verdict was recorded on any run of the owner's walk.

   **B-083 changed what this criterion can show, and that is the part worth
   keeping.** Before it, a matched definition reached the critic and never the
   model, so the model dropped required filters *every time* — the criterion
   would have been met trivially and would have proved the opposite of its
   intent: not that a constraint binds a model that saw it, but that a model is
   punished for not reading minds. Now that the definition reaches the prompt,
   **compliance is the correct behaviour**, and a drop-and-catch may not be
   stageable against a competent model without rigging the run — which the owner
   ruled out, on B-053's reasoning: provoking a failure in order to film it is
   set dressing, not evidence.

   What stands instead: the rule is asserted **both ways** in
   `tests/agent/test_required_filters.py` — it fires on a query that drops a
   required filter, and stays silent on `status = 'completed'`, which honours
   *"exclude cancelled orders"* without containing the word. It runs on every
   answer, deterministically, and **D-034** guarantees that a block which does
   stop a run becomes the answer's first limitation. Revisit if a weaker model
   is ever configured for the planner role.

7. **The manual test script**, and the gate box left unticked. **Done** —
   `docs/plan/gate-10-manual-test.md`, eight steps, walked end to end by the
   owner on 2026-08-18. Three of those walks died on the same silence and
   **B-087** was built before sign-off at the owner's direction: a run now
   records `definitions_available` beside `applied_definitions`, and the answer
   card reads *"governed by prep_quantity"* or *"no definition matched this
   question (18 defined here)"* — and stays silent when nothing is defined,
   because a caveat on every answer is how people learn to stop reading
   caveats.

### 1. Session ritual

`git fetch --all && git checkout main && git pull`, then `gh pr list` — which
should show **#72 as a draft**. Check out `p10.2d-import` rather than branching.

### 2. The four decisions this session added, and why they matter

Read these before touching the semantic layer; the code will not make sense
without them.

* **D-031** — an embedding is a spend like any other, and a refused one
  *degrades* the search rather than breaking it. The lexical arm still answers
  and the result **says** the other one did not, because a search that quietly
  halved itself reports *"nothing is written down about that"*, which reads as a
  fact about the customer's documents and is not one.
* **D-032** — the planner may ask for a term to be defined, and a lookup **costs
  an iteration rather than a model call**. That detail is what leaves D-024's and
  D-028's call arithmetic untouched.
* **D-033** — **prose informs the model; a structured definition binds it.** A
  retrieved passage is evidence the agent may use; a definition with
  machine-readable filters is a constraint the critic enforces. An answer resting
  only on prose carries a limitation saying so, and that limitation **goes away**
  when an Admin blesses the passage into a definition. This is the spine of the
  whole phase.
* **D-034** — **any critic finding strong enough to stop a run must reach the
  reader.** If a run ships despite a block, the block is the loudest limitation
  on the answer. *An answer that overstates its own rigour is worse than one that
  admits doubt.*

### 3. Running it found what the suite could not — five times

Every work package this session shipped a defect that no scripted test would have
caught, and every one was found by running the thing against a real model. This
is not a coincidence and it is the habit to keep.

* **WP10.2a**: a model that needs a definition says so by **refusing** —
  `answerable` false, the term in `define` — and the loop checked `answerable`
  first, turning the one state the feature exists for into a dead run. Every
  scripted test passed because every script set `answerable` true.
* **WP10.2a**: a duplicate lookup was refused correctly and **in silence**, so the
  model asked again and hedged an answer it had already computed.
* **WP10.2c**: the critic blocked twice, the answer shipped anyway claiming to
  have done what the critic said it had not, and the block was invisible
  (**B-079**, now fixed under D-034).
* **B-073**: nothing in the application had ever *built* an embedder, so every
  upload stored text alone — the vector arm was dead in production, not only in
  the tool.
* **B-073**: the query embedding bypassed the meter entirely, one layer below
  where the defect had been noticed.

A corollary worth remembering: **a demo must use a term the business invented.**
Asked about *"net revenue"*, a live model never looked anything up — the `orders`
card lists a `status` column whose examples include 'cancelled', so it inferred
the exclusion. The lookup path only fires when a definition genuinely cannot be
guessed, which is the case it exists for.

### 4. Local machine state a fresh clone will not have

`.env` gained **`EMBEDDINGS_PROVIDER`, `EMBEDDINGS_MODEL`,
`EMBEDDINGS_DIMENSIONS`** and a price for `text-embedding-3-small` in
`LLM_PRICES` on 2026-08-18. WP10.1a had verified that model against the account
and never written it in, so **nothing on this machine could embed** until then.
The price is not optional: an unpriced model under `LLM_RUN_COST_LIMIT_USD` is
**refused** (D-019), so omitting it does not make searches cheaper — it makes
every capped run lose its vector arm, silently before D-031 and audibly after.

Run `make migrate` before anything live: this session added revisions **0018**
(card embeddings), **0019** (`knowledge_consulted`) and **0020**
(`semantic_definitions`). A missing 0019 is what made the first WP10.2a live run
fail with a CHECK violation — the event vocabulary is enforced by the database.

### 5. CI: a job could hang for six hours, and one did

On #71 the `mssql` job's ODBC install sat on `azure.archive.ubuntu.com` for **32
minutes** with nothing above it: GitHub's default job timeout is **360 minutes**.
Fixed for that job (`timeout-minutes` on the job and the step, apt retries with a
15-second timeout in `install-odbc.sh`, which the Docker image runs too). **The
class is not closed** — no other job in the workflow has a timeout, and the next
hang will be somewhere else. That is **B-080**.

The job only runs when the `connectors` path filter fires, which a `dal/` change
does. Expect it on any PR touching the validator, and expect it to be slow even
when healthy.

### 6. A backlog row was lost, nothing in the repo noticed — now something does

Filing **B-080** prepended its row to **B-076**'s and dropped the newline between
them. Every character of B-076 survived — but it no longer began a line, so
`grep '^| B-076'` found nothing, and a later edit to B-076's own text landed
inside what read as B-080's cell. It was caught by an id audit at the end of the
session, by hand, and not by anything in the repository.

BACKLOG.md is **append-only, never renumbered, never deleted** (plan §1.5) and it
is the only record of why things were *not* done. **B-019** built precisely this
guard for STATUS.md after #24 gutted it; the same argument transfers, and the
guard is now written: **`scripts/check_backlog.sh`** (**B-081**, closed in #72),
running in `hygiene` and in `make preflight`. Nothing has to be grepped by hand
after editing the file any more.

The replay is the part worth keeping. Pointed at `dc35e7a` — the commit that
caused this — it reports the lost row **four independent ways**: B-076 does not
begin a line, there is a gap where B-076 should be, B-080's row carries fifteen
columns, and B-076 was on the baseline and is absent. CI reported success on
that commit.

Running it on the file as it stood found two defects nobody was looking for.
**B-013 and B-081 were rendering wrong on GitHub**: an unescaped `\|` inside a
code span split them into ten and eight columns, and GFM drops whatever
overflows the header — so B-013's Suggested phase, Prio and Status cells have
been rendered away on a **public** repo since Phase 3, in a row about TLS. Both
are escaped now and §2.3 says to write `\|`. The lesson generalises past this
file: a markdown table is only as intact as its narrowest row, and nothing in a
review shows you the cells GitHub silently dropped.

The deeper lesson is one this file already records and I repeated anyway: **a
patch script that reports success without asserting its edit landed will lie to
you.** Mine asserted the anchor was *present*; it never checked what the file
looked like afterwards.

### 7. Two traps still worth carrying

* **`pytest tests/a tests/b` fails to collect** — every `conftest.py` is the
  module `conftest`, so two suites' helpers collide (**B-074**). It bit again
  this session. CI runs the whole suite and is unaffected; narrow to **one**
  directory, or run everything.
* **After a squash merge, a plain `git rebase` replays commits already in
  `main`.** #68 needed
  `git rebase --onto origin/main <old-base> <branch>`, as #65 did before it. The
  conflict GitHub reported on #68 was the deleted base branch, not the content.

### 8. What is open, and what it means

**P1**, all four, checked against the file rather than remembered —
**B-088** (an accepted definition cannot be edited) and **B-090** (nothing
compares a developer's environment with the container's), both raised by the
owner and scheduled to **open Phase 11**; **B-029** (a second real provider, the
only thing that closes the Phase 6 gate, and it needs an Anthropic key);
**B-060** (asked the same question twice, the agent chose different tables and
answered two orders of magnitude apart — found in the F&B trial, **still open**
and the oldest P1 here, unscheduled).
**Closed this phase**: B-059, B-079, B-083, B-085 — and **B-078 accepted**
rather than done, covered by test and unstageable until the planner's model
changes (standing note 5).

**P2** — **B-077** (`search_tables` and `describe_table` are advertised to the
model and the loop cannot dispatch them — named in a test that fails if a third
joins them), **B-070** (`repeat_rate`'s denominator — gate work now), **B-069**,
**B-067**, **B-065**, **B-058**, **B-054**, **B-052**, **B-038**, **B-035**,
**B-003**, **B-048**, **B-028**.

**P3 worth remembering** — **B-076** (a call too cheap to round to a millionth of
a dollar records **zero**, so a thousand tiny embeddings are invisible to the
ceiling; the owner's fix is to **meter in tokens, not rounded dollars**),
**B-080** (the CI timeout sweep), **B-071** (no vector index yet, deliberately —
the entry asks for a measurement rather than an opinion), **B-072**, **B-074**,
**B-061** with **B-020**, **B-062**.

### 9. Suite numbers

**API: `1408 passed, 20 skipped`. Web: 113 tests.** Lint, format, pyright and
both guards clean on `p10.2d-import`. Run one suite at a time on this machine — the per-test databases
are created and dropped by name, and two pytest processes racing over them is
enough to break one.

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
5. **B-078 is unstageable, not wrong — revisit it if the planner's model
   changes.** The Phase 10 gate accepted *"a required filter is dropped by the
   model and caught by the critic"* as **covered by test rather than
   demonstrated** (owner, 2026-08-18, B-053's disposition). Four live attempts
   produced compliance twice and honest refusals twice, and **B-083 is why**:
   before that fix a matched definition reached the critic and never the model,
   so the model dropped required filters *every time* — the criterion would have
   passed trivially while proving the opposite of its intent, that a model is
   punished for not reading minds. Now that the definition reaches the prompt,
   **compliance is the correct behaviour**, and forcing a failure to film it is
   set dressing rather than evidence.
   **What would make it stageable again**: a weaker or cheaper model configured
   for the **planner** role (`LLM_ROLE_MAP`), where a genuine drop is likelier —
   so the moment that map changes, this criterion is worth re-running before
   anything else is concluded from it. The rule's value does not depend on being
   filmed: it is deterministic, it runs on every answer, it ships with its
   false-block twin, and **D-034** guarantees that a block which does stop a run
   becomes the answer's first limitation.

6. **A false block is the critic's characteristic failure — test for it every
   time.** Owner's direction, 2026-08-16, after the third one in a session. A
   rule that fires on a legitimate question is worse than a rule that misses,
   because a fluent refusal of an answerable question teaches people the product
   is broken while a miss merely leaves things as they were. All three came from
   a check that was *nearly* right: the capability rule fired on any catalog gap
   rather than on a statement actually refused; the numbers rule warned that
   "2026" appeared in no result, having read the year out of the question; and
   the range rule read "March 2026" out of *"between 1 March 2026 and 15 March
   2026"* and rejected correct SQL. **So every new critic rule ships with two
   tests: one proving it fires on the thing it is for, and one proving it does
   *not* fire on a legitimate question near it.** The second is the one that
   catches this class, and golden eval #18 is the first test that found a false
   block before a human did — which is the whole argument for the eval suite.
7. **There are two customer databases now, and only one of them is a fixture.**
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
- [x] WP9.2a Composer (citations/limitations) + eval harness (#59)
      — the answer grew its other three parts. **Limitations are assembled, not
      requested**: a model asked for its own caveats writes hedging or nothing,
      so `agent/composer.py` builds them from what the run knows — the ceiling
      that stopped the search, the critic's warnings, queries that came back
      empty. Revision 0015 puts them on `agent_runs` rather than inside `state`,
      because a limitation is part of the answer and the card should not read the
      agent's own scratchpad to decide what to show a person. `findings.cited`
      marks what the answer rests on, matched by **shared execution** rather than
      by text, so rephrasing does not lose the link. `ops/evals/` holds the
      twenty golden questions with every expected number a **path into
      `truths.json`**, and **20/20 pass** against the real seed
- [x] WP9.2b Evals in CI (seed + register + required check) + nightly (#60)
      — the thing standing between twenty golden questions and CI was that they
      needed a registered data source, which until now only ever came from a
      person clicking through the UI. `ops/evals/provision.py` makes that state
      from nothing — organization, member, the pizza database registered with
      its **read-only** login, discovery, profiling — and is idempotent, so a
      rerun reuses rather than duplicates. The `evals` job seeds its own fixture
      with the same generator `make seed` runs, so the numbers it checks are the
      numbers in `truths.json`. **FakeLLM only in CI** (owner's direction): it
      costs nothing, cannot flake on a provider, and gives the same answer every
      time; the model's own quality is `nightly-evals.yml`'s business, where it
      may fail without blocking a merge. `EVALS_TOKEN_BUDGET` is enforced from
      `usage_ledger` and checked **before** each question, because a budget that
      stops once it is already over is a report rather than a ceiling
- [x] GATE: seeded-wrong-draft caught; 20 golden evals pass; sign-off
      — **signed off 2026-08-16.** Walked in the browser and at the terminal.
      `make evals` → **20/20**. The seeded wrong draft caught deterministically:
      question 1 changed to ask July and query June returned **3742** — June's
      real count, correctly run and correctly cited, nothing about it looking
      wrong — and the critic blocked it naming both the period asked for and the
      period used, **with no model call**. In the browser: an answer citing its
      query, an honest refusal on the missing `orders`↔`menu_items` link, and
      limitations rendered beside the answer rather than instead of it.
      **The live run is recorded and it is 12/20, not 20/20.** That number is
      kept as it is. Five failures are one harness defect (**B-066**): the value
      check names a column the *scripted* SQL used, and a real model aliases its
      output as it likes — a check that is sound in CI and wrong in the only mode
      that spends money. Two more are the harness expecting the wrong thing:
      **#19** refused because the data ends 2026-07-31, which is precisely
      D-027's last clause working, and **#17** refused *"how are we doing?"* as
      too vague, which is defensible. One is a real gap and already has an id —
      **#14** was never shown the `orders` card, because search is lexical and
      nothing embeds (**B-018**). So what the live run establishes is that the
      pipeline holds and the *harness* is not yet ready to judge a real model.
      **223,685 tokens** for the twenty; the two multi-step questions were half
      of it. `nightly-evals.yml` has still never executed — the repository has
      **zero secrets and zero variables**, so the workflow's own guard refuses
      it, correctly. Putting a provider key into GitHub is the owner's decision
      and is not mine to make.
      What the phase leaves behind: a draft is **judged before it becomes an
      answer**, by arithmetic first and a model second, and the arithmetic half
      is free because a deterministic block skips the call. An answer now says
      what it does **not** establish, and those limitations are assembled by the
      platform rather than asked of the model, so they cannot be hedged away.
      And twenty questions run against a real database on every pull request,
      against numbers that live in exactly one place.

## Phase 10 — Knowledge + semantic layer (M10)
- [x] **B-064 (P1)** A conversation is a conversation: the thread reaches the
      prompt
      — taken before any Phase 10 code, at the owner's direction, because it was
      the largest thing the Phase 9 gate demo found and it was scheduled nowhere.
      Nothing was broken: `_question_of` read one string off `agent_runs.question`,
      `ContextBundle` had no field for a prior turn, and L5 rendered that one
      question — so *"check again"* was answered with "no business question has
      been given". **No message but the current one had ever reached any prompt.**
      **DECISIONS D-029** settles the four questions the backlog entry left open,
      and the architecture is edited in the same PR because 4.8's six layers had
      no slot for a thread. **L5**, inside the question turn and above the
      question, framed as records rather than instructions exactly as a retrieved
      chunk is (owner's direction) — anywhere higher and 4.8's precedence, soft
      everywhere else by design, would have one place where it was simply absent.
      **Three turns**, clipped, and a *truncation candidate*: dropped oldest-first
      **before any table card is dropped**, because a follow-up read without its
      thread is a question misunderstood while a question with no cards cannot be
      answered at all. The **answer goes back in**, since *"why?"* is meaningless
      without it — and the frame saying "a number in an earlier answer is not a
      result you obtained" is only the cheap half, the expensive half being
      `_verified_citations`, which already drops any citation this run did not
      produce. So a follow-up **re-queries**, which the live run shows it doing.
      **One function renders the thread for all four prompts that carry the
      question** — the layered one, the loop's reflection, the critic's rubric and
      the composer — because a thread worded four ways is four chances for one of
      them to read as an instruction. The critic is in that list for a specific
      reason: asked whether a draft answers *"check again"* with no idea what was
      being checked, a model says it does not, and a **false block on a correct
      answer** is this component's characteristic failure (standing note 5).
      **B-041 arrived again by a new road and was fixed in the same PR.**
      *"check again"* names no table, so the card search returned **nothing** and
      the planner was about to be handed an empty catalog — the exact defect that
      cost the M7 gate. The fix takes B-041's own shape: the strict search on the
      question keeps every promise it makes, and only when it matches nothing at
      all is the thread searched instead. `context_selected` records which
      happened (`tables_found_via`), because "which words chose these tables" is
      precisely the silent choice **B-060** was filed for.
      No migration — every row this needs has existed since revision 0012 — and
      **a first question's prompt is byte-for-byte what it was**, which is what
      made it safe to ship under twenty golden evals, none of which is a
      follow-up. 26 new tests. Raised **B-067**, **B-068**, **B-069**.
      **Verified live** against the pizza database on 2026-08-17, three turns in
      one thread: *"How many orders were placed in July 2026?"* → **3718**
      (`history_turns 0`, tables found via the question); *"check again"* → *"The
      recheck confirms that 3,718 orders were placed in July 2026."*
      (`history_turns 1`, tables via the **thread**, and **a new execution of its
      own** rather than the previous run's); *"and in June?"* → **3,742**
      (`history_turns 2`). `SELECT count(*)` against the seed database returns
      **3718** and **3742**
- [x] **B-066 (P2)** The eval harness can judge a model, not only a script
      — taken before Phase 10 code because a nightly job now has a key and would
      otherwise report a number that says more about the harness than about the
      product. The defect: `expect.value_of` named a result column, which is
      exactly what the *scripted* SQL emits and not what a real model emits, so
      `SELECT ... AS cancellation_rate` was read as a missing `cancelled_rate`.
      **Sound in FakeLLM mode and wrong in the only mode that spends money.**
      `match_value` now tries three rules — the named column
      (case-insensitively), the value of a **1x1** result, then any cell of a
      **single row** of at most four columns — and **the order is the safety
      property**: a fallback runs only when the named column is *absent*, so a
      result carrying that column with the wrong number in it still fails and
      nothing rescues it. That is the way a fix like this quietly turns a
      required check into decoration, and
      `test_a_named_column_with_the_wrong_value_is_never_rescued` holds it.
      `may_refuse` is new, for **#17** and **#19** — two questions with two right
      answers each, where the live run was punishing the product for behaving
      well. It is not a free pass: a refusal must still **say why**.
      **The harness got its own unit tests**, 21 of them, without a database, a
      model or a dollar. Their absence is the whole reason B-066 existed — the
      harness had only ever been exercised by running it, in the one mode where
      its defect could not appear.
      **Re-running live corrected the taxonomy this entry was written from.**
      **#4** was never an aliasing failure: the model answered *"Northgate"*, not
      `3`, which is the **better** answer since an internal key reaching a reader
      is itself a defect (B-061, B-020) — so `truth_any` now lets one fact have
      two right spellings, and `top_store_by_name` joins `decline.store_name`,
      which the generator had carried all along. And **#10** is not a harness
      problem at all but **B-070**.
      `make evals` stays **20/20** in FakeLLM mode with no weaker-rule lines
      printed, which confirms the fallbacks are dead code in CI. Live on the
      affected cases: **#4, #8, #12, #16 pass**, #17 and #19 accepted as honest
      refusals, 6/7 — the seventh is B-070. 49,157 tokens. Raised **B-070**
- [x] WP10.1a Knowledge: schema, chunking, embeddings, ingest, retrieval (#64)
      — **WP10.1 split in two** (plan §1.1) by what could go wrong: this half is
      the **store and the text**, WP10.1b is the **agent and the API**. Not a
      schema with no consumer — WP7.1's objection — because the library is the
      schema's consumer and its tests take a document from bytes to a retrieved
      passage.
      **The USER INPUT was checked, not assumed.** The owner's existing OpenAI
      key embeds (D-017 — one key, one bill, one place to rotate), and
      `text-embedding-3-small` was verified against the **live account** before
      being written into configuration rather than read off a page (standing note
      4, B-027's habit). Its **width was measured** with one 5-token call:
      **1536**, which is what revision 0016 fixes. A model of a different width
      would have had every insert refused by a constraint nobody was thinking
      about; `EMBEDDINGS_DIMENSIONS` makes that a startup error naming both
      numbers.
      **Revision 0016** adds `knowledge_documents` and `knowledge_chunks` — the
      first new tenant tables in three phases, so the rule that has now bitten
      six times applies in full: a policy each, two `TENANT_TABLES` lines, and
      the rls_proof suite extended to seed **and forge** rows in both. Verified
      live: RLS enabled *and* forced, `vector(1536)`, a **generated** `tsv`, and
      a **partial** index over unembedded chunks so the backfill's work list is a
      query rather than a scan. **Revision 0017** widens two CHECK constraints so
      `embed` is a legal role *and* a legal tier — its own tier, not `small`,
      because D-018's ladder does not apply to a single embedding model and
      filing its tokens beside intake calls would make any spend-by-tier query
      wrong. `DEFAULT_ROLE_TIERS` gains the entry rather than being exempted from
      the "every role resolves to a tier" guard, which is a real invariant.
      **Embedding is metered like every other spend**, because WP6.1's rule is
      that no path spends tokens without a `usage_ledger` row and a corpus costs
      more than the chat calls that later answer questions about it. A failing
      batch is metered *before* it raises, as `llm/service.py` does.
      **`embedding` is nullable and that is a state, not an oversight**, and
      ingest is built around it: chunks are written **before** vectors, so text
      is lexically searchable the moment it lands and an embedding failure leaves
      a half-searchable document that **says so** rather than one that lost its
      text to a rate limit. Re-indexing is **delete-and-rewrite**, never append —
      `seq` is unique per document, and stale chunks would keep answering
      questions from text the source no longer contains.
      **`retrieve.py` is hybrid in 5.5's poor-man's sense**, merged by
      **Reciprocal Rank Fusion on rank rather than score**: a cosine distance and
      a `ts_rank_cd` are numbers on unrelated scales, and normalising them
      invents a comparison nobody can defend. `PER_DOCUMENT_CAP` stops one
      verbose document filling the result. **B-041's lesson is applied here
      too** — the strict `websearch_to_tsquery` runs first and only a total miss
      falls back to OR'd words.
      **DECISIONS D-030: pypdf, not pymupdf**, which architecture 5.5 named.
      pymupdf is AGPL-or-commercial, this repository is public and its own
      licence is undecided (**B-001**), so the dependency would have quietly
      prejudged a decision B-001 exists to have taken deliberately. pypdf is
      **BSD-3-Clause, read from the installed package's own metadata** rather
      than recalled. Architecture 5.5 edited to match, including that pypdf does
      no OCR — so a *scanned* PDF is a **failure naming OCR**, never a successful
      upload of nothing.
      **51 knowledge tests.** Tampered four ways, and the fourth is the one worth
      recording: removing **both** `org_id` predicates from retrieval left every
      isolation test **passing**, because row-level security held the line on its
      own — 5.10's two independent layers, demonstrated rather than asserted.
      Removing the *policy* instead failed 13 of them plus the rls_proof suite,
      so the tests do catch a real leak. Also tampered: trusting the provider's
      arrival order (caught), ignoring code fences and dropping chunk overlap
      (both caught). One real bug found by a test being written: `utf-8` decodes
      BOM-prefixed bytes happily, so `utf-8-sig` never ran and every such file
      carried an invisible character into its first chunk and its embedding.
      Raised **B-071** (no vector index yet, deliberately — the decision owes a
      measurement) and **B-072** (two object stores that should converge in
      WP12.2, and the duplicated half is the safety half).
- [x] WP10.1b Retrieval tool + routes + documents page — `p10.1b-knowledge-tool`
      — the `search_knowledge` tool with architecture 7.4's framing in the
      **result envelope** (`framing` is the output model's *first* field, because
      a frame rendered after the passage is a caveat about text already read),
      the plan's named injection test (a document saying *"ignore your
      instructions"* comes back **unaltered** and wrapped — suppressing it would
      be worse, since then nobody could see what a customer's document says),
      and registration **before** `run_sql` because 5.5 puts "what does this
      term mean" ahead of "what is its value".
      **Six documents routes**, with uploading at **Contributor-or-Admin**
      (10.2's `[contributor+]`: a document is guidance every future run follows,
      not data a Reader supplies). The role matrix covers all six and **asserts**
      them rather than only snapshotting, with a document **per role** so DELETE
      cannot turn the next role's probe into a `deny(404)`, and the Reader's
      denials asserted as **403** specifically — a 404 would mean the route was
      reached and the object was missing, a different claim.
      **The documents page**, following B-008: a Reader sees the list and no
      controls, and an unknown role **fails closed**. A part-embedded document
      says *"4 passages, 1 searchable by meaning so far"* rather than rounding up
      to "indexed" — that is the state a large upload spends longest in. A
      failure renders as the thing to act on, carrying the API's own words, so a
      scanned PDF says it needs OCR.
      **92 web tests** (up from 81) and 11 knowledge-tool tests. Tampered three
      ways, all caught: making the role gate fail open failed 3 tests, setting
      `Content-Type` on a multipart body failed the upload test, and the API-side
      tampers are recorded under WP10.1a.
      **B-018 was listed here and is not closable here**, found by building it:
      reranking card search needs a *query* embedding inside `build_context` —
      the agent's own path — which is the spending-capability question filed as
      **B-073**. The two go together. Raised **B-073** and **B-074**
- [x] **B-073 (P2)** An embedding is a spend, so it goes through the same door,
      the same meter and the same ceiling as every other one
      — taken before WP10.2 at the owner's direction, because a gate that
      demonstrates retrieval must demonstrate the half that reads *meaning*.
      **DECISIONS D-031** settles the question the entry left open — what happens
      when the ceiling refuses an embedding mid-run — and the answer is that the
      **lexical arm still answers and the result says the other one did not**.
      8.5 calls budget exhaustion not a failure; the lexical arm has already been
      paid for; and the alternative is worse than either, because a search that
      quietly halved itself reports *"nothing is written down about that"*, which
      reads as a fact about the customer's documents and invites a model to stop
      looking. `Retrieval.degraded` is that distinction, and the tool prints it
      **before** the "nothing found" sentence.
      **`get_embedder` is now the one door an embedder comes out of**, which is
      what makes B-040's guard possible at all: the session fixture wraps it
      exactly as it wraps `registry.get_provider`, refuses anything whose
      `is_stub` is false, in the same words, recording into the same list so one
      per-test check covers both. `embed_texts` takes a **`run_id`**, writes it on
      the ledger row and calls `budget.assert_within_limit` **before each batch**.
      **Two things this found on the way, and both were larger than the entry.**
      Nothing in the application had ever *built* an embedder — `OpenAIEmbedder`
      was constructed nowhere, so the upload route ingested every document with
      `embedder=None` and the vector arm was dead in production, not only in the
      tool. And the query embedding **bypassed the meter entirely**, because
      `retrieve.py` called `embedder.embed` rather than `embed_texts`: the same
      defect one layer below where it had been noticed.
      **Raised B-075 (P1)**, the largest thing here and not fixable in this PR:
      the research loop dispatches `run_sql` and nothing else, so
      `search_knowledge` is registered, described in every prompt, and
      **unreachable**. WP10.2 owns it, because putting definitions in front of the
      planner is the same mechanism.
      15 new tests. Tampered four ways, all caught: dropping the ceiling check,
      dropping `run_id` from the ledger row, disabling the guard's `is_stub` test
      (`DID NOT RAISE`, twice), and removing the role matrix's embedder seam —
      which failed **both** ways B-040 designed for, raising *"a test asked for a
      live embedder"* and then failing the test with *"this test reached for
      ['embeddings']"*.
      **Verified live** on 2026-08-17, for about a ten-thousandth of a cent.
      `text-embedding-3-small` re-checked against the account with
      `GET /v1/models` and its width **measured** at 1536 before anything was
      written to `.env` (standing note 4). A two-chunk policy document uploaded
      to the demo org came back `indexed, chunks=2, embedded=2`; the question
      *"what do we count as takings"* — which shares **no word** with the
      document — returned both passages, each marked `[vector]`. The ledger shows
      `embed/embed text-embedding-3-small`, 7 tokens **charged to the run** and 48
      tokens charged to no run (the ingest). Re-asked with the ceiling already
      spent, the same search came back `arms=('lexical',)` and said *"the search
      by meaning was not run because this run has reached its spending
      ceiling"* — and returned **zero** passages, which is the whole argument in
      one line: without the vector arm that question finds nothing, and the
      difference between saying so and saying *"nothing is written down"* is what
      D-031 is about. Raised **B-076**: at $0.02 per million tokens a 7-token
      embedding costs $0.00000014 and `cost_usd` rounds it to **zero**, so a
      thousand searches are invisible to the ceiling that sums that column
- [x] **B-018 (P2)** A table card can be found by meaning, and golden eval #14
      goes green live
      — the oldest item in Phase 10, open since WP4.3, and B-073 is what
      unblocked it: a **query** embedding inside `build_context` is the agent's
      own path, so it needed to be a metered, capped spend before it could exist
      at all. Revision **0018** adds `catalog_tables.embedding`, which **D-014**
      refused to create until something could fill it — that condition, stated in
      2026-08-13, is exactly what was met here.
      `cards.embed_cards` is the **idempotent backfill**, run from discovery,
      from profiling and from the eval provisioner, so a deployment that
      configured a key after its first crawl catches up by refreshing.
      `search_cards` is hybrid, merged by **RRF on rank** exactly as
      `knowledge/retrieve.py` merges its two — a `ts_rank_cd` and a cosine
      distance are numbers on unrelated scales, and with one arm RRF is a
      monotone transformation of that arm's own order, so a deployment with no
      embedder gets precisely the search it had.
      **A refresh that changes nothing re-embeds nothing** — D-012's rule applied
      to the half that costs money. Both failure paths degrade rather than break
      (D-031): a provider failure during the backfill leaves the cards lexically
      searchable and `queued`, and a failed *query* embedding falls back to
      wording, because this is the context stage of **every** run and raising
      here would turn a busy provider into an unanswerable question.
      **Verified live** on 2026-08-17 against the eval catalog. *"Which day of
      the week is busiest?"* returns **0 cards lexically and 5 hybrid**, every
      one `found_by=vector`, `public.orders` among them — and `context_selected`
      records that, so a run whose tables all came from the lexical arm on a
      deployment that has an embedder is now a visible regression rather than a
      silent one. **Golden eval #14 passed live, 5,795 tokens**, against the
      known live failure this entry was filed for. One honest limit: the vector
      arm decides *candidacy*, not perfect ranking — `stores` outranks `orders`
      on that question and both reach the prompt, which is what the planner
      needs. 10 new tests; tampering the vector arm off fails three of them
- [x] WP10.2a The agent can consult a document mid-run — `p10.2a-knowledge-in-the-loop`
      — **B-075**, and the owner's direction on 2026-08-18 is what made it a work
      package rather than a backlog line: *"an agent that's told it can search
      documents but can't dispatch the tool means Phase 10 ships a feature the
      product can't reach."* WP10.1b registered `search_knowledge`, described it
      in every prompt, and left it **unreachable** — the loop called `run_sql` by
      name and nothing else, so the corpus an organization uploads never reached
      a run at all.
      **DECISIONS D-032** settles the shape, and it is the opposite of what the
      backlog entry recommended. B-075 proposed retrieving into the context
      deterministically; the owner's criterion is that the agent **consults** a
      document, which is a decision the agent makes rather than a retrieval
      performed on its behalf. So `Plan` gains `define`: the planner may name a
      term it needs explained before it writes SQL, and the loop dispatches the
      tool, puts the passages in front of the next plan, and records both.
      **A lookup costs an iteration, not a model call.** That is the load-bearing
      detail: the lookup iteration runs no statement, so there is nothing to
      reflect on and it costs one plan call rather than two — **cheaper** than an
      ordinary iteration, which leaves **D-024**'s and **D-028**'s worst-case
      arithmetic exactly as it was. Bounded further by `MAX_LOOKUPS` and by
      refusing a term already asked, which is the duplicate-query hash's shape
      applied to a second kind of repetition.
      **A twenty-first event type** — `knowledge_consulted`, revision **0019** —
      because 10.3 fixes the vocabulary and widening it is a decision. The
      argument is the criterion itself: `tool_called` records the *asking*, and a
      lookup leaves no execution row to carry the *answer*.
      `KnowledgeFrame` moved to `agent/context.py` beside `REFERENCE_FRAME` and
      `HISTORY_FRAME`, since a retrieved passage now renders in the layered
      prompt as well as in a tool envelope — three frames for untrusted text, in
      one place, which is what the test comparing them was always assuming.
      **The tamper is the best evidence here.** Dropping the retrieved passage on
      the floor — retrieving it, emitting the trace, and never putting it in the
      bundle — leaves the dispatch test and the trace test **both passing** and
      fails only `test_the_definition_reaches_the_plan_that_writes_the_sql`. That
      is precisely the failure a gate demo would not catch by watching the
      timeline. 10 new tests.
      **Two defects came out of running it live, and neither was reachable from
      the suite.** First: a model that needs a definition says so by *refusing* —
      `answerable` false, the reason naming what is undefined, the term in
      `define` — and the loop checked `answerable` **first**, so the one state
      this feature exists for became a dead run. Every scripted test passed
      because every script set `answerable` true. Second: a duplicate lookup was
      refused correctly and **in silence**, so the model asked again at iteration
      3, got nothing, and hedged an answer it had already computed. Both fixed,
      both now have tests, and the second is why `_progress_so_far` names what
      has been looked up without repeating it.
      **Verified live** on 2026-08-18, against a term this business invented so
      that no model could guess it. *"How many anchor orders were there in July
      2026?"* → iteration 1 asked (*"Count July 2026 orders once the business
      meaning of 'anchor orders' is established"*) → `tool_called
      search_knowledge` → `knowledge_consulted term='anchor orders' passages=1
      found_by=['both'] source='Order reporting policy > Anchor orders'` →
      iteration 2 wrote `status = 'completed' AND total_amount > 40 AND
      EXTRACT(ISODOW …) BETWEEN 1 AND 5`, which is the document's sentence turned
      into SQL and which nothing in the catalog could have suggested. A first
      attempt using *"net revenue"* is worth recording as the control: the model
      **never asked**, because the `orders` card lists a `status` column whose
      examples include 'cancelled'. The lookup fires when a definition genuinely
      cannot be inferred, which is the case it exists for — and it means a gate
      demo has to use a term the business actually invented.
      Raised **B-077** (`search_tables` and `describe_table` are still advertised
      and still undispatchable, now named in a test that fails if a third joins
      them) and **B-078 (P1)**, which the same live run found and which WP10.2b
      must answer: having written the right SQL, the model spent two more
      iterations reasoning its way *out* of the weekday clause and answered
      **1,054** where the document says **747**. The definition reached it and it
      discarded it in the open — and nothing could object, because a passage
      retrieved as prose carries no machine-readable filters for a critic to
      check the statement against
- [x] WP10.2b An answer grounded in prose says its definition was not checked
      — **DECISIONS D-033**, the owner's principle stated as one on 2026-08-18
      after **B-078**: *"prose informs the model, a structured definition binds
      it."* A retrieved passage is evidence the agent may use; a semantic
      definition with machine-readable filters is a constraint the critic
      enforces. They are different objects with different guarantees and the
      product must not blur them.
      This is the **honest half**, and it ships first because it is true whether
      or not the structured half exists yet: a run that took a definition from a
      document now carries a limitation **naming the term** and saying nothing
      checked that the query followed it. WP9.2's assembled kind — a fact the run
      knows, not a hedge the model writes — and it names the term because a
      reader who knows *which* definition went unenforced can check that one.
      `state.prose_terms` is separate from `state.lookups` and the split is the
      design: `lookups` counts attempts, because that is what bounds the cap and
      refuses a repeat; `prose_terms` counts the ones the documents **answered**,
      because a term the corpus could not explain left the model no worse
      informed and caveating it would be a warning about nothing — which is how a
      reader learns to skip warnings. Both halves are tested.
      The limitation **disappears** when an Admin blesses that passage into a
      definition (WP10.2c), because the claim stops being unverifiable. That is
      the seam between the two halves, and it is why B-059's import path is not a
      convenience: it is how a customer buys enforcement for definitions they
      already wrote down.
      3 new tests. **The plan is re-lettered** to match what ships: WP10.2c is
      the structured half with B-078 as its **central criterion**, WP10.2d is the
      import and the gate
- [x] WP10.2c Semantic definitions bind: the critic enforces them
      — **D-033's other half, and B-078 is its central criterion** rather than a
      side rule (owner, 2026-08-18): the demo has to show a run where a
      definition's filter is **required**, the model **drops** it, and the critic
      **catches** it. A run where the model happens to comply proves nothing.
      Revision **0020** adds `semantic_definitions` — the seventh tenant table,
      so a policy, a `TENANT_TABLES` line and the rls_proof seed/forge pair, with
      the forged row using a *different name* because `(data_source_id, name)` is
      unique and a collision would be refused before the policy was consulted.
      Scoped to a **data source**, not an organization: a definition names
      columns and columns belong to a database.
      **The two halves of a definition do different jobs.** `description` and
      `expression` are prose for the prompt; `required_filters` is structure for
      the critic. Rendered at **L3**, above L4, because a definition is the
      platform's own object — validated against the catalog, enforced — while a
      retrieved passage is a customer's untrusted prose and stays at L4.
      **The rule has two strengths and the split is what keeps the strong one
      safe.** It **blocks** when the statement does not constrain the column at
      all, which is arithmetic rather than taste. It **warns** when the column is
      constrained but the definition's values are absent — `status = 'completed'`
      is that shape and is very likely correct, so blocking it would be the false
      block standing note 5 exists for. Six false-block twins, including a filter
      applied inside a CTE and a query that never touches the table.
      **The seam closes**: a term with a definition stops carrying WP10.2b's
      *"read as prose"* limitation, because now something checks it.
      13 new tests; tampering `filtered_columns` to claim every column is
      constrained fails the two criterion tests.
      **Verified live** on 2026-08-18. The seeded draft — the criterion — was
      caught: *"'net_revenue' is defined here as requiring orders.status none of
      cancelled, refunded, and the query behind this answer does not filter on
      orders.status at all."* And the **real** run was better evidence than
      expected: asked for net revenue, the model wrote `status = 'completed'`,
      the deterministic rule **warned** exactly as designed, and the **LLM half
      blocked independently** — *"the query does not clearly exclude cancelled
      and refunded orders as required by the metric definition"* — which is the
      definition reaching L3 and being used. Raised **B-079 (P1)** from the same
      run: the critic blocked on the last permitted pass, the answer shipped
      anyway saying *"explicitly excluding cancelled and refunded orders"*, and
      the block was invisible because `limitations_for` reads only warnings
- [x] **B-083 (P1)** A definition bound the critic and never reached the model
      — found while wiring verified queries into the same layer and asking what
      else renders there. `runner` matched definitions, put them on the bundle
      and handed them to the critic under a comment claiming *"the critic must
      judge the same definitions the planner was shown"*; `_layers` never
      referenced them, and `Definition.render()` — written for exactly this —
      **was called by nothing**. So the rule blocked statements for omitting
      filters the model could only have guessed. WP10.2c's seventeen tests
      proved the rule fires and does not false-fire; **not one asked whether the
      definition reached the prompt.** The enforcement was tested and the
      communication never was. Now at **L3** behind `DefinitionFrame`, framed as
      **authoritative** — the deliberate opposite of `KnowledgeFrame` — and not
      a truncation candidate, since the critic enforces it whether or not the
      budget left room to say so. Six tests, four of which fail with the layer
      removed, checked by removing it.
      **This changes what item 6's demo proves.** B-078 asks for a run where a
      required filter is *dropped* and *caught*; with this defect the model
      dropped it every time because it could not know, so the demo would have
      passed for the wrong reason — proving a model is punished for not reading
      minds rather than that a constraint binds a model that saw it.
- [x] **B-081 (P2)** Nothing guarded BACKLOG.md, and a row was silently merged
      into another — `scripts/check_backlog.sh`, in `hygiene` beside
      `check_status.sh` and in `make preflight`. Ids unique and contiguous from
      B-001, every row beginning a line with the seven columns the header
      declares, `Prio`/`Status` from the vocabulary, and no id that was on the
      base branch missing here. **Replayed against `dc35e7a`, the commit that
      lost B-076, it reports the row four independent ways where CI reported
      success.** A `--selftest` of fifteen files runs first, so a guard that has
      stopped matching fails the build rather than passing every damaged file.
      Its first run on the real file found two more: **B-013 and B-081 were
      rendering wrong on GitHub**, an unescaped `\|` in a code span splitting
      them into 10 and 8 columns — GFM drops the overflow, so B-013's Phase,
      Prio and Status cells have been invisible on a public repo since Phase 3.
      Both escaped, the rule written into §2.3. The Status vocabulary now names
      `in progress` and `accepted`, which two rows already used: a guard that
      enforces a vocabulary the project does not use is one that gets
      switched off.
- [x] WP10.2d Import (B-059) + verified queries + admin UI ← gate
- [x] GATE: **an organization's own writing changed the SQL a model generated,
      on a customer's warehouse** — `prep_quantity` imported from the customer's
      `meta_metric`, accepted by an Admin with the row-role filter their own
      `meta_gate` states in English and records as `enforced = 0`, after which
      *"Ayam Penyet Set, 0.00 units sold"* became an honest refusal and
      `row_role <> 'parent_zero_qty'` appeared in **4 of 4** executed queries;
      the same claim shown a second way on the pizza fixture, where defining
      `repeat_rate`'s denominator moved golden eval **#10** from FAIL to PASS by
      changing `FROM customers LEFT JOIN orders` into `FROM orders GROUP BY
      customer_id`; a document **consulted mid-run** (`knowledge_consulted`);
      org isolation proved for `semantic_definitions` and `verified_queries` in
      the rls_proof suite; a Reader offered no control and issuing no request.
      **B-078's live drop-and-catch is accepted as covered-by-test rather than
      demonstrated** (owner, 2026-08-18, B-053's disposition): four attempts gave
      compliance and two honest refusals, and **B-083's fix is why** — before it
      the model never saw the definition and dropped the filter every time, so
      the criterion would have been met for the worst possible reason.
      **Signed off by the owner on 2026-08-18** (#72), gate wording confirmed as
      read.

## Phase 11 — Charts + polish (M11)
- [~] **B-060 (P1) — reproduced and diagnosed on live runs, 2026-08-18. No fix
      attempted**: the owner asked to be told what is causing it and what the
      options are before anything is built. Five real runs against the
      customer's warehouse. **The same wording is now stable** — four runs, one
      table, agreeing to the cent — so the run-to-run divergence of 2026-08-16
      did not recur; Phase 9 and 10 changed the prompt and the retrieval beneath
      it. **The instability moved rather than went away**: a paraphrase switched
      to the other table and invented a third reading nobody has defined, then
      spent its whole iteration budget and refused. Four defensible readings of
      one question span a factor of **538**.
      **Three causes, each fixable alone.** (1) The card drops the counts the
      profiler already measured, so a code covering 0.01% of a table reads like
      one covering 78% — **B-092**. (2) The profile is the first rows rather
      than a random sample, so the card advertised five codes for a column with
      eight — also **B-092**. (3) Nothing says a choice existed, though the run's
      own `context_selected` names both candidate tables and the answer names
      neither — **B-093**.
      **Awaiting the owner's choice** between: make the card carry the shares it
      already knows (B-092); say which source an answer came from when another
      was available (B-093); a warn-only critic rule for a filter on an
      undocumented code column, which needs B-092 first; leaving it to an Admin
      to define the metric, which the semantic layer supports and B-088's API
      half now makes correctable; or asking the user, which is a product
      decision the V1 plan does not carry.
      **The owner chose B-092 and B-093** (2026-08-18), and declined the critic
      rule and leaving it to a definition. Both are built:
- [x] **B-092 (P1)** A card described an undocumented code column as an unranked
      list of examples from the head of the table. The counts were measured,
      stored and dropped one line before use. Now each value carries its share
      and the line says how far the profile looked and that the rows were the
      table's first — on the warehouse B-060 came from, `move_type` went from
      `examples: DO, PI, UC, CN, GR` to `DO 78%, PI 17%, UC 4%, CN 0.2%,
      GR 0.1%`. No migration: 0013 settled that a migration must not import
      application code to regenerate prose, and this is not an exact string
      transformation. A profile run rebuilds existing cards
- [x] **B-093 (P1)** An answer now names the source it read and the comparable
      ones it did not, when the question matched more than one. It **states the
      choice and does not judge it**: the run cannot know the other source would
      disagree without running it. Silent when one source was offered, when all
      were read, and when nothing was read
- [x] **B-090 (P1)** Nothing compared a developer's environment with the
      container's. `scripts/check_env.sh` now does, as a **declaration** rather
      than a diff — every key that stays on the host is named with its reason,
      so adding a variable costs one deliberate line and forgetting costs a red
      build. It runs in `hygiene` with its `--selftest` first, like the STATUS
      and BACKLOG guards, and `make check.env` runs it locally. **On its first
      run against this repo it found six more instances of B-086's class** —
      `DAL_MAX_ROWS`, `DAL_TIMEOUT_SECONDS`, `ARTIFACT_RETENTION_DAYS`,
      `EMBEDDINGS_BATCH`, `LLM_REFUSE_UNPRICED_WHEN_CAPPED` and `OIDC_ISSUER` —
      every one a setting a developer could tune on the host while the product
      ignored it, proved with `DAL_MAX_ROWS=7` reaching a container that
      answered 1000. All six are passed now, and one rule made that safe: **a
      variable set to nothing is unset**, because `${VAR:-}` is how compose
      passes a key nobody set and an integer field refused to parse it. That
      rule also fixes a latent break B-086 shipped — a `.env` missing
      `EMBEDDINGS_DIMENSIONS` was a container that could not start
- [x] **B-088 (P1)** An accepted definition cannot be edited — no edit, no
      un-accept, and re-accepting is a 404. **Raised from P2 to P1 and scheduled
      here by the owner on 2026-08-18**, having hit it mid-walk: *"a semantic
      layer whose definitions are write-once will not survive real use."* The
      likeliest moment to get a filter wrong is the first time you write one,
      which is exactly when the product locked you out; the only way back was
      deleting the row in `psql` and importing again.
      **Split in two, because the whole of it is past §1.1's size target. The
      item stays open until both halves are merged.**
      **The API half is built and is this PR.** `PATCH .../definitions/{id}`
      edits an **active** definition's `description`, `expression`, `synonyms`
      and `required_filters`; `DELETE` retires one; `GET .../versions` says what
      it has said. Validated against the catalog exactly as `accept` is,
      Admin-only by the role matrix, and **audited** — all five decisions now
      write an `audit_log` row, not only the two this item named. Versioning was
      **decided rather than deferred** (**D-036**, migration 0022): a definition
      binds, so *"what did it require when that answer was written"* is a
      question about whether an answer was right, and the day editing ships is
      the day every overwrite starts costing one. `semantic_definition_versions`
      is append-only in the database — 0022 revokes UPDATE and DELETE from
      `dataagent_app`, and `rls_proof` proves it.
      **The web half is this PR**: the "In force" card now edits a definition's
      description, formula, synonyms and filters, retires it behind a second
      click, and shows what it has said version by version. It sends **only the
      fields that changed**, because the API reads an absent one as *leave it
      alone* and resending a description nobody touched is how a description
      quietly loses a sentence. A refused edit stays on screen to correct, since
      the 400 names the column it was written to help repair.
      **The owner's walk found two defects in this screen and both are fixed
      here**: a refused save put its message in one region at the top of a long
      page, where nobody editing at the bottom would see it — so a refusal
      looked like nothing happening — and the summary line opened with
      *"Saved, …"*, which reads as a receipt for a save that had not happened.
      The test that was supposed to cover the first one asserted the sentence
      was *somewhere* on the page, which it was, and so it caught nothing; the
      replacement asserts it renders inside the card being edited, and fails
      against the old code
- [x] **B-094 (P1)** A retired definition could not be brought back through the
      product, and its name stayed taken so re-importing could not recover it
      either. Three correct rules — accept takes only proposals, edit takes only
      active ones, import skips a name already held — left a mis-clicked
      **Retire** recoverable only in `psql`, which is the shape of the hole
      B-088 was filed for one verb earlier. Found on B-088's manual walk,
      **raised from P2 to P1 by the owner on 2026-08-19** and built ahead of
      WP11.1 at their instruction, because it is small and its fixture was
      sitting retired.
      `POST …/definitions/{id}/reinstate` — a POST for the reason accept and
      reject are — validated before it takes effect, Admin-only, audited, and
      recorded as **`reinstated`** rather than as another edit (revision 0023),
      because a history that called it an edit would read as though somebody had
      changed the wording. `?status=retired` and an **Out of force** card close
      the discoverability half: being listed puts nothing back into force, and
      the card says so.
      **The second dead end is closed in the same act.** A retired definition
      cannot be edited, so one whose catalog has moved on would have been
      permanently unreinstatable; `reinstate` takes optional filters and repairs
      it as it comes back
- [ ] B-091 (P2) a run records the definitions that governed it by **name**, so
      a citation cannot be resolved to the version in force at the time. Harmless
      while definitions were write-once; B-088's API half is what ends that.
      Filed with the versioning that makes it fixable
- [x] WP11.1 Chart tool (validated Vega-Lite) + client renderer — the chart is
      inside the answer card and its spec opens the way the SQL does (**B-048**).
      **A chart that cannot be drawn says why, where the chart would have been.**
      Not in `limitations`: that region is about whether the answer is *true*,
      and a missing picture says nothing about that — the owner's call, on
      B-079's argument that a heading a line contradicts teaches readers to skim
      the region carrying an unresolved critic block. `charts.decide` refuses
      with the number that makes a refusal actionable (51 categories against a
      cap of 50; a measure holding words; `Q1`/`Q2` on a time axis, which is a
      *wrong* chart rather than a missing one). A truncated result is never
      drawn at all — B-051's rule, and a chart has nowhere to put the caveat.
      The spec is assembled server-side from a closed vocabulary and the frame's
      own columns, so the sneaky-url case is closed by construction rather than
      by a denylist. Stored on the run rather than the finding, which is a
      **plan-wording correction** recorded in the plan itself: a refusal can
      exist on a run with no finding, and arch 4.2 already puts chart specs on
      the `ComposedAnswer`.
      **Proved in a real browser**, which is the only place it can be: jsdom has
      no canvas, so the unit tests mock the renderer and can only show the card
      *offers* a chart. The browser smoke asserts vega's own marks are on the
      page, that they are in the same card as the citation, and — the security
      claim of the whole design — that drawing one **reaches nothing off the
      page's origin**. The first version of that test looked for a canvas, found
      nothing, and would have shipped an empty box: vega renders these as SVG
      — carries **B-048** (owner, at the Phase 7 sign-off): the chart belongs
      **inside the answer card**, and its spec must be openable the way the SQL
      is. A chart nobody can trace back to the query behind it is decoration that
      looks like evidence — the same claim Phase 7 made for answers, extended to
      pictures. Filed before the tool is designed rather than retrofitted after
- [x] **B-096 (P1)** the same claim was recorded twice, because the guard
      against it compared characters — the Phase 7 rule is *one claim once*, and
      a text match enforces it only for identical text, so the composer
      rephrasing a finding into an answer defeated it. The owner saw it on a
      live run: two `high confidence` badges and two *“show the query”* controls
      over one query. **The fix keys on the evidence**: two claims resting on
      exactly the same executions are one claim, whatever words they use — which
      is the rule `mark_cited` already followed one line below. Fixed in this
      WP, with a test that fails against the old guard
- [x] **B-095 (P1)** a run whose every query *failed* carries no limitation, and
      the answer calls the failure an empty result — *“no data was returned”*
      with `limitations: []`, while both executions had ended in a connector
      error. A reader is told their data is empty when nothing was asked of it.
      One predicate: the thin-evidence note only looks at executions that
      succeeded. Found on WP11.1's manual walk, from the owner's own question
- [ ] B-097 (P2) the prose enumerated all 18 months above a chart of the same
      18 points — B-096's redundancy from a different direction, prose against
      picture rather than finding against finding. **Assessed before building**
      at the owner's request: a composer-prompt change, keyed on **result size**
      rather than on whether a chart was drawn. Keying it on the chart is the
      trap — prose and chart request are written in the same `finalize` call, so
      a model told "you are drawing a chart, do not list the numbers" can end up
      beside a refusal, leaving the reader neither. It becomes structural only
      if the prose should *refer* to the picture
- [x] B-098 (P3) chart axes are labelled with raw column names. Small, WP11.2's
      polish pass; the axis title is one field on a spec the server already
      assembles — done in WP11.2a as a de-snake-casing and **deliberately not a
      dictionary**: expanding `qty` or deciding `dt` means date would be the
      platform inventing meaning it does not have
- [x] WP11.2a History/catalog/members polish + B-017 + B-100 + B-098 (#86)
      — conversation history with rename and **archive** (**D-039**, revision
      0026: a button that says delete and hides instead is a lie to the person
      clicking it); **B-017**'s recovery grant, which adds no new privilege
      because the organization creates its own way back; **B-100**'s method line
      surfaced rather than deleted (revision 0025). **B-101** carries out what is
      not here — the small-screen pass and the catalog/members rounding
- [x] WP11.2b Compose Playwright smoke + README quickstart          ← gate PR
      — **built, not signed off.** `apps/web/e2e-compose/` drives the whole
      product in Chromium against a real compose stack — sign in, register the
      seeded database, prove it read-only, discover and profile the schema, ask,
      read the answer card, open the query behind it, open the trace, and reload
      to prove none of it was held in the page. The `web-e2e` CI job runs it
      path-filtered. The model is a **stub in the shipped image** selected by
      environment and refused twice outside CI (**D-040**). **B-103** is fixed
      and the chart criterion has been met live.
      **Walked by the owner on 2026-08-20 and not signed off**, on three
      defects, all now fixed here: **B-105** (a monthly aggregate on a continuous
      day axis — wrong rather than plain), **B-106** (every answer but the newest
      lost its chart and its evidence — raised to a gate blocker by the owner,
      and the card is now the assistant turn) and **B-107** (B-096's rule applied
      to one of its two call sites). What remains is the owner's second walk, and
      the GATE line below stays unticked until it happens
      **Superseded:** ~~WP11.2 History/catalog/members polish + Playwright
      smoke~~ — split into 11.2a and 11.2b on 2026-08-20, because seven
      workstreams in one gate PR is a diff nobody can review properly. Kept as a
      note rather than an unticked box: the work was split, not abandoned, and a
      box that can never be ticked is a small lie in the file the plan calls the
      single source of truth. What follows is that entry's own record, which is
      worth keeping because it says why each item was grouped with the others.
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
- [x] GATE: trend question → rendered chart; smoke green; sign-off
      — **signed off by the owner on 2026-08-21**, on the second walk. The first
      (2026-08-20) did not sign off and found three defects: **B-105** (a monthly
      aggregate drawn on a continuous day axis — wrong rather than plain),
      **B-106** (every answer but the newest lost its chart and its evidence) and
      **B-107** (B-096's rule applied at one of its two call sites). All three
      fixed in #87.
      **B-103's criterion, in the owner's own hands**: money rendered on a
      quantitative axis at 120,000–160,000, which cannot happen from strings.
      **B-105**: the bars are banded on monthly ticks, not spikes on a weekly
      calendar. **B-106**: both charts stay across questions — the card is the
      assistant turn, so an answer keeps its chart, method, limitations,
      findings, evidence and trace when the next question is asked.
      **`web-e2e` green in CI** (2m19s, the walk itself 15.6s) against a stack it
      builds from nothing, with a scripted model (**D-040**) — it proves the
      stack wires up end to end and can never show that a question was
      understood, which is why the chart criterion is met by the live walk and
      not by it.
      **The quickstart was walked from nothing three times** and produced seven
      defects, five of them in prose. B-102's lesson stands: nothing walks the
      documented path automatically, and that gap is still open.
      **What this sign-off does not cover** — small screens (**B-101**: three
      `@media` rules in the whole app, and nobody has sat at 375px); Linux
      (**B-108**: `make up` may not start for a developer whose uid is not 1001,
      and this host structurally cannot find out); whether the agent is any good,
      which is the evals' business and not this gate's; and anything deployed,
      which is Phase 12.
      **And it does not cover B-044.** Twice during this walk the assistant said
      the branch was ready having checked *the branch* and not the owner's
      containers, and both times that stack was silently serving old code — the
      second with a **stale API underneath it**, which reproduces the same
      symptom on its own and would have been read as the fix not working. A gate
      walked against a stack that can be silently stale is only as good as the
      check that catches it, and **the documented check failed here**: the B-044
      recipe cannot see a screen whose chunk is fetched lazily, and reported
      ABSENT for code that was current. It has been amended in CLAUDE.md, along
      with the restart's second failure — a kept `.next` that came back
      inconsistent and 404'd every route under the dynamic segment. Neither of
      those is closed by this sign-off; what closed them here was doing it by
      hand, twice, after the fact.

## Phase 12 — Azure deploy + hardening (M12)  ⚠ human review on every PR
- [x] WP12.1 Bicep modules + env params + what-if in CI — **merged (#92,
      2026-08-22)**. Nine modules, one per service in architecture 9.1's justification
      table and nothing beside it. Postgres is **private-access** rather than
      firewalled, which costs a VNet, two delegated subnets and a private DNS
      zone and buys a database with no public endpoint to mis-scope. Secrets are
      never in a template: the vault is created empty, apps reference secrets by
      URI and read them with a managed identity, and the two parameters that
      would otherwise carry one are read from the environment at deploy time.
      **`what-if` is not here and cannot be**: it needs the OIDC identity WP12.2
      creates, so this work package is `bicep build` and the guards, exactly as
      the plan says. **Nothing has been deployed** — every claim in this line is
      a claim about what compiles, and the first contact with Azure is WP12.2's
      `what-if`. Two things were learned the hard way and are worth carrying:
      a guard written inline in workflow YAML had its regex mangled into one
      that matched any assignment at all, so it failed the build on correct code
      and the tempting fix was to loosen it — it is a file with a selftest now,
      like the three beside it; and a realistic-looking fake key in that
      selftest tripped gitleaks, which cost a force-push to keep a plausible
      credential out of a public repository's history rather than allowlisting
      one forever
- [ ] WP12.2 OIDC deploy workflow → dev env + Key Vault backend + smoke
- [ ] WP12.3 Observability wiring + quotas hard-stop + alerts
- [ ] WP12.4 ~~Prod env~~ + ASVS-lite checklist + restore drill + v1.0 tag ← gate
      — **prod is deferred (D-041)**; every other criterion applies, against dev
- [ ] GATE: arch Part 14 acceptance; nightly evals on; user sign-off

### What Phase 12 already owes, from the backlog

**Sixteen open items name P12 as their suggested phase**, and none of them is
scheduled inside a work package — so they are invisible until somebody reads all
of BACKLOG.md. Listed here because the phase that closes the project is the last
chance to take them, and because "suggested phase" has never been a commitment.
Nine are P2 and the rest P3; nothing here is P1.

**The three that belong to a Phase 12 work package by subject**, and should be
taken there rather than separately:

- **B-114** — the artifact store can lose a result and nothing says so. Filed
  2026-08-22 from a count of files against rows: 411 rows, 408 files, 2 gone,
  1 with a NULL reference. **Not retention** — nothing is past `expires_at` and
  B-021 records that no sweep exists. A citation still opens (the evidence panel
  reads `sample_rows` from the database, not the file), so what it costs is a
  chart that cannot be redrawn — and the refusal for *the store lost your
  result* is the same sentence as for *the model named the wrong query*. Take it
  with WP12.2, where the Blob backend makes the same conflation hide an outage.
- **B-021** — nothing deletes an expired result. `result_artifacts.expires_at` is
  written on every row and read by nothing, so retention is a promise with a date
  attached. WP12.3 is where a scheduler and a real Blob container first exist.
- **B-014** — nothing removes a secret when its organization goes away. Not
  reachable today (there is no org-deletion API), and off-boarding a tenant must
  not leave credentials in Key Vault. WP12.2 is where the KV backend lands.
- **B-007** — every CI action still targets the Node 20 runtime. A warning today
  and a broken pipeline when the shim is removed; one PR, all six actions.

**The rest are genuine debt that Phase 12 inherited rather than caused** — five
from the Phase 11 gate walk (**B-101** small screens, **B-102** the documented
quickstart nothing walks, **B-104** a full page load throwing the session away,
**B-108** the uid hole no one here can test, **B-112** a cap that flags nothing)
and **B-110**, the two guards against this project's characteristic defect, which
the owner scoped and deliberately did not build. **B-110 is the one to weigh
first**: CLAUDE.md records four instances of *built, tested, unreachable*, not one
of them caught by CI, and walking is still the only thing that has ever caught
it. **Two P1 items are open and neither is P12-scheduled**, so nothing will surface
them at the gate: **B-029** — no Anthropic key, which is still what holds the
Phase 6 gate, and which WP12.4 makes newly relevant because that gate turns
nightly evals on; and **B-109**, three parts of four built and verified live,
the fourth deliberately left with the owner. Both want an answer before the
Phase 12 gate rather than at it.

---

## Next step — superseded, kept for the reasoning it carries

> **This section is Phase 9's handoff and is history.** Everything it schedules
> shipped: B-064 and B-018 are closed, Phase 10 and Phase 11 are signed off.
> **The live next step is the `Next step:` field in the header of this file** —
> that is the one the plan makes authoritative (§1.1) and the one a new session
> reads first. This is kept rather than deleted because the reasoning below is
> still the best statement of why conversation memory was a safety decision and
> not a feature, and B-052's last paragraph is still open.

**Phase 9 is signed off** (#60, 2026-08-16). Its GATE line above carries the
evidence, including the live run at 12/20 and why that number is what it is.

**First, and before any Phase 10 code: B-064.** It is P1, it is the largest thing
the gate demo found, and it is scheduled nowhere. A conversation is answered one
message at a time with no memory of the thread — see the handoff's item 2 for the
four design questions that need settling first. It is a decision, then a small
change: `ContextBundle` gains a field, `_layers` renders it at L5 beside the
question, and `runner` reads the conversation's recent turns. What makes it worth
deciding rather than writing is that every wrong answer here is a *safety*
answer — history is user-supplied text, and putting it above the platform rules
would be the one place 4.8's precedence stops being soft.

Then Phase 10 / **WP10.1 — knowledge ingest and retrieval** (`p10.1-knowledge`).
Plan §6 Phase 10, architecture 5.4–5.5.

Build:

- Revision 0016: `documents`, `document_chunks` (vector + tsvector), **+RLS and a
  proof-suite extension in the same PR** — the rule has held for every tenant
  table so far and this is the first one in three phases.
- `knowledge/`: upload, heading-aware chunking with overlap, embedding through
  the Phase 6 provider config, hybrid retrieve (vector + lexical, RRF merge), all
  under the org session. The retrieval tool is registered with **L4 framing** —
  retrieved text is reference material and explicitly not instructions (7.4).
- **This closes B-018**, and B-018 is why golden eval #14 failed live: card
  search is lexical, so *"which day of the week is busiest"* never surfaced the
  `orders` card. Every card already carries `flags.embedding = "queued"`, so the
  backfill has its work list.
- **Tests:** an org-isolation retrieval test in the `rls_proof` family (two orgs,
  one query, zero cross-hits); chunker goldens; and an injection-framing test
  where a chunk saying *"ignore your instructions"* arrives wrapped.

Then **WP10.2**, the gate PR, which owes **B-059**: the semantic layer must
*import* definitions a database already carries, admin-reviewed, provenance kept.
Its gate walks against the F&B source as well as the pizza one, and the phase has
done its job when the question that answered **0 units** answers something else.

Two things to watch:

- **USER INPUT, now hard-required:** embedding provider credentials. Phase 4 made
  them optional and search degraded to lexical; Phase 10 cannot. Ask before
  starting WP10.1, not during it.
- **B-052** — a structured call's output ceiling can be smaller than the schema it
  must fill. Fixed for the planner and the critic, still true for the composer,
  and chunk-summarisation is the next shape to hit it.

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
