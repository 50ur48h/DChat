# DECISIONS — deviations & choices made during the build

Format (plan §1.6): context → options → decision → consequences, 5–15 lines.
Any deviation from `docs/architecture.md` needs an entry here **and** an edit to the
architecture doc, both in the same PR as the code.

## D-024 — Observe is deterministic, because 4.4's own caps did not fit its own loop
Date: 2026-08-16 · Phase: 8 · PR: WP8.1b · Owner's direction: fix the document
Context: Architecture 4.4 lists three model calls per iteration — Plan, Observe
(cheap model), Reflect — and, four bullets later, a default ceiling of **20 LLM
calls** for **8 iterations**. Those numbers cannot both hold: `8 × 3` is 24, plus
Intake and Compose is 26. A run using its iterations as intended would be cut
short by its *call* budget rather than its iteration budget, and the iteration
cap — the one the loop is actually written around — would never be the binding
constraint. Found while building the loop, by adding the numbers up.
Options: (a) raise the call ceiling to ~28, keeping three model stages and making
every run cost half as much again; (b) cut iterations to ~6 so three calls fit,
losing depth to preserve a stage that adds little; (c) make **Observe
deterministic** and keep both published numbers.
Decision: (c), and **the architecture is edited rather than the contradiction
worked around in code** (owner's direction). Observe's job is to turn a *typed*
tool result into a compact summary for the state — a mechanical transformation of
a value the tool layer has already validated. A model doing it costs a call,
varies between runs, and can put a number in the summary that was never in the
result, which is the one error this system must not make quietly. What 4.4
actually asks of Observe — *raw rows never accumulate in the prompt* — is kept
and kept more strictly: a result is shown in full to exactly one prompt, the
Reflect call immediately after it, and never again.
Consequences: an iteration costs two model calls, so a full run spends
`8 × 2 = 16` plus Intake and Compose — **18 against 20**, and 4.4 now states that
arithmetic so the two ceilings are checked against each other rather than
independently plausible. The headroom is deliberate: adding a stage, or giving
Observe a model later, breaks the fit and forces one of the caps to move
consciously. Interpretation still happens at a model — Reflect sees the result —
so nothing is lost but the call. `agent/loop.py::summarize` is the whole of
Observe, and it is unit-testable without a provider, which is how the loop's
tests stay hermetic.

## D-023 — The budget is stored beside the research state, never inside it
Date: 2026-08-16 · Phase: 8 · PR: WP8.1b · Owner's decision
Context: Architecture 4.2 sketches `ResearchState` with `budget: BudgetState` as
one of its fields, while 10.1 gives `agent_runs` two separate JSONB columns,
`state` and `budget`. WP8.1a had to pick one and stored them apart; the owner
confirmed that on 2026-08-16 and gave the reason that settles it.
Options: (a) nest `BudgetState` inside `ResearchState` and write one column,
matching 4.2's sketch literally and leaving 10.1's second column unused;
(b) keep them in the two columns the schema already has.
Decision: (b), and the architecture is edited so 4.2 says so. Two reasons, and
the second is the one that makes this more than tidiness. **They answer different
questions**: "what was this run allowed to spend, and what did it spend" is
operational, read by an admin screen and by cost reporting, and should not
require parsing an agent's scratchpad to answer. And **they have different trust
levels** — the research state is a scratchpad the agent fills, while the budget
is the ceiling the agent is *held to*. A limit that travels inside the thing it
limits is one bad deserialization away from being editable, which is the same
reasoning that keeps budgets in the controller and out of the prompt (4.4).
Consequences: `ResearchState.as_json()` carries no budget, and `BudgetState`
knows nothing about research. The loop checkpoints both, to their own columns, at
every transition. A resumed run reads its ceilings from `agent_runs.budget`
rather than from whatever the checkpoint claimed they were — so a state rewritten
by hand, or by a future bug, cannot raise its own limits. `matching the schema
beats matching a sketch` is the general rule this sets: where 10.1 and an earlier
Part disagree about storage, 10.1 wins and the earlier Part is corrected.

## D-022 — A conversation names the database it is about
Date: 2026-08-15 · Phase: 7 · PR: WP7.3a
Context: WP7.2c made the scheduler **refuse rather than guess** when an
organization has more than one registered data source, because a silently wrong
database produces a confident, correctly-cited answer about somebody else's
data — the worst output this product can generate, and the only one with nothing
about it that looks wrong. That refusal was always meant to be temporary: the
demo organization has two sources, so *every* question there refuses, and the
Phase 7 gate is a question answered in the browser. Something has to name the
source, and nothing could.
Options: (a) a tie-break in the scheduler — the first source, the most recently
used — which is guessing with extra steps and reintroduces exactly the failure
7.2c refused; (b) the **message** carries a `data_source_id`, so each question
names its own; (c) the **conversation** carries it, chosen when the thread
starts; (d) infer it from the question by searching every catalog, which makes a
wrong answer depend on the model's phrasing.
Decision: (c). A thread is about one database. A follow-up question in Phase 8
must reach the same source as the question it follows, or two answers in one
conversation are drawn from different databases with nothing saying so — and (b)
makes that a per-message accident rather than a property. Revision **0014** adds
`conversations.data_source_id`, nullable, `ON DELETE SET NULL`. Null is not an
error: it is every conversation written before this revision, and it still
resolves an organization's single source and still refuses when there is a
choice. **The refusal is kept, not replaced** — what closed the ambiguity is a
caller *saying* which database it means, not permission to guess.
Consequences: `docs/architecture.md` 10.1 gains the column on `conversations` and
10.2 gains the field on `POST …/conversations`. The foreign key is **not** the
tenant check — a constraint check does not consult row-level security, so
another organization's source id would satisfy the database perfectly well —
so `runs/service.py` looks the source up through the org session and answers 404,
and a test registers a second organization's source and proves it. A conversation
whose source is later removed behaves like one that named none, which is the
same trade D-016 makes one table over: the record of what was asked outlives the
registration of what it was asked about. What this does **not** do is let a
question override its thread; if that is ever wanted it is a new decision, not a
parameter.

## D-021 — Runs execute in-process, with checkpoints, and there is no queue
Date: 2026-08-15 · Phase: 7 · PR: WP7.2c · Owner's decision
Context: WP7.2b left one question open and said so rather than guessing: what
picks up a `queued` run. A research run takes thirty seconds to four minutes, so
holding the request open is not available; the three candidates were inline in
the request, an in-process background task, and a real queue with a worker.
**This entry records a choice, not a deviation.** Architecture 0.2.4 already
answers it — "the API runs agent runs as in-process async tasks, persists state
to Postgres at every step boundary" — and Part 8.2's table lists Service Bus as
✗ with "in-process tasks + checkpoints" as the V1 answer. It is written down
because the question was genuinely open at the point of building, an owner
confirmed it on 2026-08-15, and a future reader asking "why is there no worker?"
should find the reasoning rather than infer it from the absence of one.
Options: (a) inline — simplest, and holds an HTTP connection for minutes;
(b) in-process background task plus checkpoints — zero infrastructure, and a
redeploy kills in-flight runs; (c) Service Bus and a worker — durable, and buys
a second deployable, a queue to operate and cross-version message contracts
before there is any measured need.
Decision: (b), and no architecture edit, because the document already says so.
Two constraints follow and are honoured in code. **The runner never assumes it is
inside an HTTP request** — `execute_run` takes ids and returns a `RunOutcome` —
which is what makes 0.2.4's V1.5 promotion path free rather than a rewrite. And
**orphans are reconciled** rather than left: the lifespan sweeps anything left
`queued`, `running` or `validating` to `interrupted`, a status the schema already
carried for exactly this.
Consequences: a redeploy interrupts in-flight runs, visibly and with a reason
that says a restart happened rather than implying the question was at fault. A
per-org semaphore (2, arch 8.4) bounds concurrency, because questions would
otherwise be a way to spawn unbounded tasks in the API process. The trigger for
revisiting is the one 0.2.4 names — sustained multi-org concurrent runs
contending for one container, **measured, not assumed**; if it turns out wrong
before that, it gets recorded as a deviation rather than absorbed by widening
scope.

## D-020 — A run is ordered by when it was asked, not by when it started
Date: 2026-08-15 · Phase: 7 · PR: WP7.1
Context: Architecture 10.1 gives `agent_runs` in full SQL — the one table it
spells out completely — with `started_at`, `finished_at` and the index
`(org_id, conversation_id, started_at DESC)`. WP7.1 creates a run in `queued`
and hands it to WP7.2's planner to execute, so between the question and the
planner there is a real interval in which `started_at` is NULL. Every queued run
therefore sorts as NULL in the architecture's own index, and "this conversation's
runs, newest first" — the query the chat UI is built on — has no answer for
exactly the runs a user is waiting on. Two smaller cases are the same shape:
`messages` needs somewhere to keep 10.2's `idempotency_key`, and `findings` has
no timestamp at all, so findings cannot be listed in the order they were reached.
Options: (a) stamp `started_at` at creation, making it mean "asked" and leaving
nothing that means "began work" — which is the number a latency graph needs;
(b) sort by `id`, which is a random uuid and not a clock; (c) add `created_at`
and index on it, keeping `started_at` for what it says.
Decision: (c), plus the two additive columns. `agent_runs` and `findings` gain
`created_at`; `messages` gains a nullable `idempotency_key` with a partial unique
index per conversation; the index becomes
`(org_id, conversation_id, created_at DESC)`. `started_at` keeps its meaning and
stays NULL until a run actually starts, which is what makes "queued for 40
seconds" a measurable thing rather than an invisible one.
Consequences: `docs/architecture.md` 10.1 is edited so its SQL and its catalog
say this. The additions are additive only — no column changed meaning and none
was removed — and the same latitude 10.1 already grants elsewhere ("key columns
only") is being used, but stated here because `agent_runs` is the one table given
in full and a silent edit to it would make the document wrong.

## D-019 — A per-run spend ceiling, ahead of the per-org quotas 8.3 asks for
Date: 2026-08-14 · Phase: 6 · PR: WP6.2 · Owner's request
Context: Architecture 8.3 specifies per-organization daily and monthly token and
query quotas, checked at run start and at each LLM call, soft warn at 80% and
hard stop at 100%. That needs a controller, a warning surface and a notion of
"run start" — all Phase 8 — and is filed as **B-025**. Meanwhile WP6.2 puts real
spending behind a real key, and the owner asked for eval and gate runs to be
capped before that happened.
Options: (a) build 8.3 in full now, in a phase with no controller to hang it on;
(b) cap nothing until Phase 8 and rely on small models and short prompts;
(c) a hard per-run ceiling in the front door, leaving the quota system to B-025.
Decision: (c). `LLM_RUN_COST_LIMIT_USD` is checked before every call against the
run's own `usage_ledger` rows — the same rows the meter writes, so the ceiling
cannot drift from what was billed. Unset by default, because a person asking one
question should not hit a cap; set for evals and demos, where the risk is a loop
nobody is watching.
Two properties are deliberate. **A ceiling that cannot see its spend is not a
ceiling**: an unpriced model records `cost_usd = NULL`, so under a cap such a
call is refused rather than waved through (`LLM_REFUSE_UNPRICED_WHEN_CAPPED`,
default on). And **exhaustion is not a failure** (architecture 8.5): it raises
`RunCostExceededError`, a distinct type, so the Phase 8 controller can catch it
and compose an answer from the findings so far rather than apologising.
Consequences: the ceiling is per *run*, so a call with no `run_id` is not capped
— stated in the module docstring and asserted in a test rather than left to be
discovered. B-025 still stands for the org-level quotas, the 80% warning and the
run-start check. Architecture 8.3 updated to note the per-run ceiling and that it
is narrower than, not a replacement for, the quota system.

## D-018 — Roles are the architecture's six, and a role names a tier, not a model
Date: 2026-08-14 · Phase: 6 · PR: WP6.1
Context: Two documents disagree about what an LLM role is called. Architecture
4.9's `MODEL_ROLES` lists six — `intake, observe, plan, sql, critic, compose` —
and maps each to a *tier* (`small|mid|strong`). Plan §6 WP6.1 paraphrases them
as five: `planner, sql_author, critic, composer, cheap`. The names are about to
be written into a CHECK constraint, an env var and every agent call site from
Phase 7 onward, so the disagreement had to be settled before the first one.
Options: (a) the plan's five; (b) the architecture's six; (c) both, with aliases.
Decision: (b). The architecture is the binding document (plan §1.2), and its six
names are not a synonym for the plan's — they are literally the states of the
research loop in 4.4, which Phase 8 implements as a state machine. `cheap` is
not a role at all in that reading; it is the `small` tier, which is why the map
has two levels. (c) was rejected outright: an alias means two names for one
concept in a table people will grep.
Consequences: `Role` and `Tier` live in `llm/base.py`; `usage_ledger` stores
both, because the map between them is configuration and "what did moving observe
to the small tier save" is the question architecture 8.3's central claim rests
on. The schema keeps its own copy of the two lists — a migration must mean the
same thing in a year — and `test_the_ledger_and_the_llm_package_agree_about_
roles_and_tiers` fails if they drift. Plan §6 Phase 6 updated to the six names.

## D-017 — OpenAI's own API is the primary provider; Azure OpenAI waits for Phase 12
Date: 2026-08-14 · Phase: 6 · PR: WP6.1 · Owner's call
Context: The architecture (4.9, 9.1, 13.4) and plan §6 Phase 6 both name **Azure
OpenAI** as one of the two V1 providers, on the reasoning that everything else in
Part 9 is Azure. The owner has credits on `platform.openai.com` — not Azure
OpenAI — that expire sooner than anything else in the budget, plus an Anthropic
key. Provisioning an Azure OpenAI resource to spend credits that would lapse
elsewhere is paying twice.
Options: (a) provision Azure OpenAI and honour the doc; (b) OpenAI's direct API
as primary and Anthropic as the second provider, revisiting Azure OpenAI when
the rest of Azure is stood up in Phase 12; (c) OpenAI only, and drop the second
provider until later.
Decision: (b), owner's call on 2026-08-14. (c) was rejected because the second
provider is not decoration: V1's claim is that the abstraction is real, and the
only evidence for that is the same contract suite passing on two APIs that
disagree about system prompts, structured output and usage reporting.
Consequences: this is a *provider* change, not an architectural one. The
`LLMProvider` protocol, the registry and the ledger are untouched — which is the
point of having them, and the first real test of whether they earn their place.
WP6.2 builds `llm/openai.py` and `llm/anthropic.py`; `llm/azure_openai.py` is not
written. Model ids and prices are configuration (`LLM_MODELS`, `LLM_PRICES`)
rather than code, so a later move to Azure OpenAI is a deployment change plus one
new provider module. Architecture 2.1, 4.9, 9.1 and 13.4 updated to say OpenAI +
Anthropic, with Azure OpenAI recorded as reconsidered at Phase 12; the Azure
service table keeps the row and marks it deferred rather than deleting it, so the
option stays visible. Data residency is the thing given up: calls leave Azure's
network for OpenAI's, which is acceptable for a demo tenant with generated pizza
data and is the first question to re-ask before a real customer's data flows.

## D-016 — An audit row outlives the thing it is about
Date: 2026-08-13 · Phase: 5 · PR: #31 · Confirmed by the owner
Context: Revision 0010 gives `query_executions.data_source_id` `ON DELETE SET
NULL`. Architecture Part 10.1 lists the column and says nothing about what
happens when the data source is removed, so the default reading — the cascade
every other child of `data_sources` uses — would delete the execution history
along with the registration. The two readings differ in exactly the case that
matters: someone removes a source, and the record of what was read from it goes
with it.
Options: (a) `CASCADE`, consistent with `column_policies` and the catalog chain;
(b) `SET NULL`, keeping the row and losing only the pointer; (c) no foreign key
at all, as `audit_log` does with `object_id`.
Decision: (b), confirmed by the owner on 2026-08-13 — "an audit trail that
vanishes with the subject isn't one". The catalog tables cascade because they
*describe* a source and are meaningless without it; an execution row **records
an act** and is meaningful forever. What is lost on delete is the join, not the
evidence: `sql_text`, `tables`, `columns`, `sensitive_accessed`, the actor and
the timestamp all remain, and `data_source_id IS NULL` reads as "the source this
was read from has since been removed".
Consequences: `data_source_id` is nullable, which every reader must handle —
a screen grouping by source needs an "unregistered" bucket. Retention is
therefore governed by the deliberate policy on `result_artifacts.expires_at`
rather than by the accident of someone deleting a data source, which is the
right place for it (architecture 7.6). `result_artifacts` still cascades from
its execution: an artifact without its execution is a payload with nothing to
say about itself. Architecture Part 10.1 updated to state the rule rather than
leave it to be inferred.

## D-015 — A function the validator cannot name is a function it will not run
Date: 2026-08-13 · Phase: 5 · PR: #28
Context: Architecture 7.5 and plan §6 WP5.1 specify a **deny list** of engine
escape hatches (`pg_read_file`, `pg_sleep`, `xp_*`, `OPENROWSET`). Building it
against sqlglot showed the deny list is the weaker half of a control that is
already there: sqlglot resolves every function it knows to a typed node
(`Count`, `Lower`, `TimestampTrunc`, `Cast`, even `generate_series`), and leaves
everything it does not know as `Anonymous` — which is where *every* named escape
hatch lands, and where the next one nobody has thought of will land too.
Options: (a) implement the deny list as written and accept that an unlisted
function passes; (b) refuse every `Anonymous` function, with the deny list kept
only to give a clearer message; (c) an explicit allowlist of every permitted
function name, per dialect.
Decision: (b). The deny list stays and is the reason `pg_sleep` earns
`denied_function` rather than `unknown_function`, but it is not what stops
anything: an unrecognised function is refused for being unrecognised.
`_ALLOWED_UNTYPED_FUNCTIONS` is the escape valve and is **empty** — an entry in
it is a standing decision about one engine function, made in a PR that says why.
Consequences: stricter than the architecture, in the direction 7.5 already
states ("overly strict beats permissive: exotic-but-valid SQL that the validator
rejects returns a clear error the agent can rephrase around"). The cost is real:
a legitimate engine-specific function is refused until somebody adds it, and
Phase 7 is where that will first be felt. Architecture 7.5 updated to describe
the two-layer control. Two ceilings ship with it for the same reason — a
statement longer than 20,000 characters or nested deeper than 50 is refused
before parsing, because parse, qualify and generate all recurse, and 300 nested
parentheses raised a bare `RecursionError` out of the validator during testing:
a way past every rule, since none of them ever ran.

## D-014 — No `embedding` column until something can fill it
Date: 2026-08-13 · Phase: 4 · PR: #23
Context: Architecture Part 10.1 gives `catalog_tables` an `embedding vector(1536)`
and plan §6 WP4.3 says embeddings are written "when key configured, else queued
flag". No embeddings key is configured, and plan §6 Phase 4's USER INPUT note
already says card search runs lexical-only without one.
Options: (a) create the column now and leave it null; (b) create it and a vector
index, and write a backfill nothing can run; (c) leave it out until the key that
fills it exists.
Decision: (c), the same rule WP4.1 and WP4.2 followed — the columns a pass fills
arrive in the revision that fills them. A vector column nothing writes is never
exercised, so nobody learns it is wrong; the index question (ivfflat or hnsw,
and with what lists) cannot be answered without data to tune against; and a
rerank over cards nobody embedded is a code path that cannot be right.
Consequences: revision 0009 adds `card_text`, a **generated** `card_tsv` and
`flags`, and no vector column. Every card is written with
`flags.embedding = "queued"`, so the backfill has its work list before it
exists, and `search_cards` already has the seam it needs (rank, then reorder).
**B-018** carries the work. Architecture Part 10.1 annotated so the absence is
deliberate rather than an omission a later session "fixes".

## D-013 — A profile belongs to a snapshot; a policy belongs to a column
Date: 2026-08-12 · Phase: 4 · PR: #22
Context: Architecture Part 10.1 puts `policy allow|mask|deny` on
`catalog_columns`, and plan §6 WP4.2 says the classifier "writes
`column_policies`". Both cannot be right, and the difference matters: since
D-012, `catalog_columns` rows belong to a snapshot and are rebuilt every time a
schema changes. A policy stored there would be silently reset by the next
refresh — a data leak caused by a routine operation, with nothing failing to
draw attention to it.
Options: (a) policy on `catalog_columns`, copied forward on each refresh;
(b) a separate table keyed by column name; (c) policy on the data source's
"current" columns only, with no history.
Decision: (b), and the split is by *what kind of fact it is*.
  * **Statistics describe a sample of a snapshot** — null fraction, distinct
    estimate, min/max, top values, semantic role, and the sensitivity the
    classifier *suspects*. These live on `catalog_columns` and die with it.
  * **A policy is a judgement about a column by name** — schema, table, column —
    and lives in `column_policies`, which discovery never touches. Copying
    forward (option a) would work until the day a column is renamed or a refresh
    half-fails, and then it would work wrongly.
Consequences: `effective_policy` resolves in one order — a person's decision,
else `mask` if suspected, else `allow` — so "nobody has decided" is still a safe
answer, and `policy_decided` tells a screen which is which. A second profiling
pass never overrules a person. Two tests hold this: an Admin's `allow` survives
a schema change that rebuilds every catalog row, and a re-profile leaves it
alone. Architecture Part 10.1 updated; plan §6 WP4.2 was already right.

## D-012 — The snapshot is the unit of catalog consistency, and of incrementality
Date: 2026-08-12 · Phase: 4 · PR: #21
Context: Plan §6 WP4.1 lists `catalog_schemas … catalog_relationships,
discovery_runs`; architecture Part 10.1 lists `catalog_snapshots … ` with no
schemas table and no runs table, and Part 5.2 wants both *versioned snapshots*
("running agents keep their snapshot for run consistency") and *incremental
refresh* ("re-discover only changed objects"). Taken literally the two pull
apart: a snapshot per crawl copies every row each time, which makes the plan's
own acceptance test — "re-run with no schema change touches zero rows" —
impossible.
Options: (a) one mutable catalog per data source, dropping snapshot consistency;
(b) a new snapshot every crawl, dropping row-level incrementality; (c) a new
snapshot only when something actually changed.
Decision: (c), plus two smaller alignments with the architecture.
  * **`catalog_snapshots` is also the run record.** It carries `status`
    (building|active|failed|superseded), `captured_at`, `completed_at`,
    `object_count` and `error`, so a failed crawl is a snapshot that never went
    active rather than a row in a second table. `discovery_runs` is not built.
  * **`catalog_schemas` is not built.** A schema is a column on `catalog_tables`
    until something is stored *about* a schema, which nothing yet is.
  * **A crawl that finds no change creates nothing.** Every table gets a
    `structural_hash` over its columns and keys; if every hash matches the active
    snapshot, the crawl records that it checked and exits, leaving the snapshot
    active and untouched. Only a real change builds a new snapshot, copying
    unchanged tables forward — so the expensive work Phase 4.2 and 4.3 add
    (profiling, cards, embeddings) is inherited rather than repeated.
  * **`catalog_relationships` hangs off `snapshot_id`, not `data_source_id`.**
    Otherwise a new snapshot's edges would collide with the previous one's, and
    the FK graph an agent reasons about would not be pinned to the catalog it
    was reasoning about.
Consequences: "what did the agent see" is answerable for any past run, and the
common refresh is cheap and provably so — `test_a_refresh_that_finds_no_change_writes_nothing`
asserts it at the row level. Architecture Part 10.1 updated to match; plan §6
WP4.1's table list is superseded on this point.

## D-011 — Encryption to a customer database is policy, not per-source freedom
Date: 2026-08-12 · Phase: 3 · PR: #18 · Requested by the owner (B-013)
Context: WP3.2 connected with `ssl="prefer"`: TLS when the server offers it,
plaintext when it does not, and no way to tell which happened. Correct for a
compose container with no certificate, wrong for a managed database over a
network the customer does not control, and invisible in both cases. B-013 filed
it for Phase 12; the owner pulled it forward and asked for a setting with a safe
default, and for the mode to be shown rather than assumed.
Options: (a) one global mode; (b) a free per-source field; (c) a policy that
decides by address, which a source may tighten but not loosen.
Decision: (c). `TLS_MODE` (typed as the *encrypted* subset, so no configuration
can turn encryption off for a remote address) applies to every host that is not
loopback or named in `TLS_LOCAL_HOSTS`; `TLS_MODE_LOCAL` applies to the ones that
are. In prod nothing is local, whatever the list says. A source may name any
stricter mode; an optional one for a remote address is a 422 at registration.
The mode lives in `data_sources.settings` — the non-secret half of a connection,
which architecture Part 10.1 already gives that column for — so no schema change
and no deviation. Rows written before this default to the policy on read, so an
old remote row tightens rather than being grandfathered.
Consequences: the demo stack still works (compose declares its two databases
local); a remote source that cannot do TLS now fails loudly instead of leaking
quietly. `require` is documented everywhere as *encrypted, not authenticated* —
only the verify modes check the certificate, and a test result says which. What
remains open is per-source certificate material for a private CA: B-015.

## D-001 — Local secrets backend before Key Vault (pre-approved)
Date: 2026-08-10 · Phase: 3 · PR: #16
Context: Arch M3 lists Key Vault as a dependency, but Azure arrives in Phase 12.
Decision: Implement SecretsProvider with an encrypted local-file backend
(Fernet, key from .env) for dev; KeyVaultSecretsProvider lands in WP12.2
behind the same interface. Prod images refuse to start with the local backend.
Consequences: zero Azure cost until Phase 12; interface proven early.

## D-002 — GitHub repository is named `DChat`; the project stays `data-agent`
Date: 2026-08-10 · Phase: 0 · PR: — (WP0.1, direct push to main)
Context: Architecture Part 2.3 and 13.6 name the monorepo `data-agent/`. The
GitHub repository that already exists for this build is `50ur48h/DChat`.
Options: (a) rename the GitHub repo to `data-agent`; (b) rename every internal
identifier to `dchat`; (c) let the remote name and the project name differ.
Decision: (c). The repository *name* on GitHub is `DChat`. Everything inside it
keeps the names the plan and architecture use: `apps/api`, `apps/web`, the Python
package `dataagent`, compose services, Azure resource naming (`rg-dataagent-*`).
Consequences: one cosmetic mismatch between the clone directory and the project
name, recorded here so no future session "fixes" it by renaming packages. Any
Azure/ACR/Key Vault naming in Phase 12 follows `dataagent`, not `dchat`.
Architecture doc updated: Part 2.3 carries a one-line note.

## D-003 — Branch-protection status checks are attached in WP0.5, not WP0.1
Date: 2026-08-10 · Phase: 0 · PR: — (WP0.1, direct push to main)
Context: Plan §4.5 sets `required_status_checks.contexts = [hygiene, api, web]` in
WP0.1, but those CI jobs are not created until WP0.5. GitHub treats a required
check that has never reported as permanently "Expected — waiting for status",
so every Phase 0 PR before WP0.5 would be unmergeable without an admin bypass.
Options: (a) set the contexts now and bypass protection on each early PR;
(b) protect main now without status contexts, and attach the real job names in
WP0.5 — which the plan already schedules ("update branch protection required
checks to the real job names").
Decision: (b). WP0.1 applies: PRs required, linear history, no force-push, no
deletion, squash-only merges. WP0.5 adds the three required contexts.
Consequences: between WP0.2 and WP0.5 a PR can be merged without CI, because CI
does not exist yet; the protection that matters in that window (no direct pushes,
no force-push, no branch deletion) is live from commit one. Plan §4.5 updated to
say the same thing so the document does not lie.

## D-010 — One API, more than one audience value
Date: 2026-08-12 · Phase: 2 · PR: #13
Context: The first real Entra sign-in failed every call with `bad_audience`.
Signature and issuer verified; only `aud` disagreed. Entra issues a **v2** access
token whose audience is the resource's client-ID GUID, while its `api://` URI is
what a **v1** token carries. Both name the same app registration, and which one
arrives is a property of the tenant, not of our code.
Options: (a) document the correct spelling and let each environment guess;
(b) accept every value that names this one registration.
Decision: (b). `OIDC_AUDIENCE` takes a comma-separated list and
`resolve_audiences()` hands all of them to the validator.
Consequences: token version stops being something an operator has to know. This
is not a widening — a token naming any other resource is still refused, and
`test_a_third_partys_audience_is_still_refused` exists so the list cannot quietly
become "accept anything".

## D-009 — The expected issuer is discovered, not configured
Date: 2026-08-11 · Phase: 2 · PR: WP2.3
Context: Plan §3.2 and arch Part 6.1 treat the issuer as a value the operator
supplies. Checked against the real Entra external tenant before writing any
config, and the assumption does not hold: the tenant publishes its discovery
document at `https://dchat.ciamlogin.com/<tenant-id>/v2.0` but issues tokens
claiming `https://<tenant-id>.ciamlogin.com/<tenant-id>/v2.0` — a different host.
The same tenant *also* answers on `login.microsoftonline.com/<tenant-id>/v2.0`
with a third issuer value. Any of the three looks plausible in a `.env`.
Options: (a) document the correct string and hope nobody picks another;
(b) read `issuer` from the discovery document, which is what OIDC defines it for.
Decision: (b). `OIDC_AUTHORITY` says where to discover; the expected `iss` comes
from that document. `OIDC_ISSUER` remains as an optional pin for a provider whose
metadata is not trusted, and a test covers both paths.
Consequences: one fewer hand-copied string that silently rejects every token, and
rotation of the issuer host by the provider stops being a breaking change.
`.env.example` documents `OIDC_AUTHORITY` and leaves `OIDC_ISSUER` commented out.

## D-008 — 401s are logged, 403s are audited, and orphan denials get their own table
Date: 2026-08-11 · Phase: 2 · PR: WP2.1b · Approved by the owner before implementation
Context: Plan §6 WP2.1 says "every 401/403 writes an `audit_log` row", but
`audit_log.org_id` is NOT NULL and RLS-scoped. A 401 has no trustworthy identity
and therefore no organization; taking one from the URL would let an
unauthenticated caller choose whose audit log to fill, which is a denial-of-service
against the very record meant to detect abuse.
Options: (a) make `audit_log.org_id` nullable; (b) attribute 401s to the
organization named in the path; (c) split by how much is actually known.
Decision: (c), three destinations.
  * **401, no trustworthy identity** → application log only. Nothing stored.
  * **403 with a resolved membership** → `audit_log`, scoped to that org. This is
    the row an admin expects to find, and the M2 acceptance criterion.
  * **403 with no resolvable organization** (unknown subject, or a known account
    asking for a tenant it does not belong to) → `security_events`, a new
    platform-level table added in revision 0003. Not tenant-scoped, append-only
    for the app role, indexed on `actor_subject` and `attempted_org_id`.
Consequences: "which accounts are probing for tenants they do not belong to" is
one indexed query, and nothing is lost. `security_events` names its organization
column `attempted_org_id` rather than `org_id` on purpose — it records what was
asked for, not what the row belongs to, and the RLS proof suite treats any
`org_id` column as a tenant scope that must be declared and protected, so
misnaming it would either break that guard or quietly weaken it. Plan §6 WP2.1's
wording is superseded on this point.

## D-007 — CI path filters gate steps, not jobs
Date: 2026-08-11 · Phase: 0 · PR: WP0.5
Context: Plan §4.1 puts `if: needs.changes.outputs.api == 'true'` on the `api`
and `web` **jobs**. Plan §4.5 also makes `hygiene`, `api` and `web` required
status checks. Those two are incompatible: GitHub never reports a context for a
skipped job, so a required check that skips leaves the PR permanently
"Expected — waiting for status". Every PR here touches `docs/plan/STATUS.md`, and
a docs-only PR matches neither filter, so this would fire immediately and often.
Options: (a) drop path filtering and always run everything; (b) require only
`hygiene` plus an aggregate gate job; (c) keep the jobs unconditional and move
the filter onto the steps inside them.
Decision: (c). `changes` still computes the filters and the expensive steps still
skip, so a docs-only PR finishes in seconds — but all three contexts always
report, so branch protection works as §4.5 intends.
Consequences: two extra `if:` lines per job and a no-op "Nothing to do" step that
makes the skip visible in the log. Plan §4.1's YAML is superseded on this point.

## D-006 — CI's Postgres service arrives with the migrations that need it
Date: 2026-08-11 · Phase: 0 · PR: WP0.5
Context: Plan §4.1 gives the WP0.5 `api` job a `pgvector/pgvector:pg16` service
and a `DATABASE_URL`, but the API has no database code until WP1.1 — the same
section also says "never build pipeline for components that don't exist yet".
Options: (a) add the service now and leave it unused for a phase; (b) add it in
WP1.1 alongside the first migration and the migration up/down test that needs it.
Decision: (b). Dead configuration in a security-sensitive pipeline is a liability:
it is never exercised, so nobody notices when it breaks or drifts.
Consequences: WP1.1 must add the service, `DATABASE_URL`, and the migration
up/down step in the same PR as revision 0001 — recorded in STATUS under Phase 1.

## D-005 — `orjson` dropped from the API dependencies
Date: 2026-08-10 · Phase: 0 · PR: WP0.2
Context: Plan §6 WP0.2 lists `orjson` as a runtime dependency. Its only purpose
there is FastAPI's `ORJSONResponse`, which the installed FastAPI marks
**deprecated**: the framework now serialises directly to JSON bytes through
pydantic when a route declares a return type, which is faster than the custom
response class. pyright in strict mode fails the build on the deprecation.
Options: (a) keep `ORJSONResponse` and suppress the deprecation; (b) keep the
dependency unused "for later"; (c) drop it until something actually needs it.
Decision: (c). Routes declare return types, so serialisation is already the fast
path. `orjson` returns as a direct dependency in the phase that genuinely needs
it — result-artifact serialisation (P5) or event payloads (P7) — not before.
Consequences: one fewer unused dependency in the image and in the audit surface
of a public repo. Plan §6 WP0.2's dependency list updated to match.

## D-004 — Decision records live in `docs/plan/DECISIONS.md`, not `docs/adr/`
Date: 2026-08-10 · Phase: 0 · PR: — (WP0.1, direct push to main)
Context: Architecture Part 13.6 puts decision records in a `docs/adr/` directory;
the implementation plan (§1.6, §2.4) defines a single append-only
`docs/plan/DECISIONS.md`. Both cannot be the home, and a reader of the
architecture doc would look for a directory that will never exist.
Options: (a) create `docs/adr/` and one file per decision; (b) keep the plan's
single-file DECISIONS.md and correct the architecture doc's tree.
Decision: (b). One file, `D-###` entries, append-only, alongside STATUS and
BACKLOG in `docs/plan/`.
Consequences: decisions are diffable in one place and are trivially reviewed as
part of the PR that causes them, which is the actual requirement (§1.6 step 4).
Architecture Part 13.6 tree updated to point at `docs/`.
