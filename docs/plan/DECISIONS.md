# DECISIONS — deviations & choices made during the build

Format (plan §1.6): context → options → decision → consequences, 5–15 lines.
Any deviation from `docs/architecture.md` needs an entry here **and** an edit to the
architecture doc, both in the same PR as the code.

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
