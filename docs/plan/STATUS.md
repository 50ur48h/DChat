# STATUS — data-agent build

Current position: **Phases 0–4 complete and signed off.** Next: Phase 5 / WP5.1
Next step:        `p5.1-dal-validator` — the SQL policy engine. Read plan §6
                  Phase 5 and architecture Part 7.1 and 7.5 **before writing any
                  code**; this is the security boundary, it gets human review on
                  every PR, and the full brief is under "## Next step

Phase 5 / **WP5.1 — validator + policy pipeline** (`p5.1-dal-validator`).
**⚠ This is the security boundary.** Human review on every PR, the highest test
density in the repository, and the one phase where "it works" is not the bar —
the bar is arch Part 7.5's property table, proven per dialect.

**Take B-016 first.** WP5.3 adds `--cov-fail-under=90` on `dal/` and §4.4
ratchets the overall number, and the `api` job currently measures the SQL Server
connector at 27% because it has no SQL Server. An hour now; an emergency during
WP5.3 otherwise.

Then, in order (plan §6 Phase 5, architecture Part 7.1 diagram 6 and 7.5):

- `dal/validator.py` on sqlglot. Parse in the connector's dialect, walk the AST,
  and enforce **in this order**: one statement only; type ∈ {SELECT, EXPLAIN};
  no DML, DDL or transaction control anywhere — including inside CTEs and
  subqueries, which is where a checker that only looks at the top level fails;
  no system schemas (`pg_catalog`, `information_schema`, `sys`), dialect-aware;
  no denied functions (`pg_read_file`, `pg_sleep`, `xp_*`, `openrowset`); every
  table and column resolved against the org's catalog; denied columns rejected
  **wherever they appear**, not only in the select list; star expansion resolved
  against the catalog *before* column checks.
- Output: the `ValidatedQuery` that has existed since WP3.2. Errors are
  structured `PolicyViolation`s with a machine-readable code and a message that
  is safe to show the LLM (arch 7.4).
- `dal/policy.py`: the per-org context loader — column policies and per-source
  caps — with a small TTL cache.

What the previous phases hand it, and what it must not re-invent:

- **The type gate is already built and already sanctioned.**
  `dataagent.dal.validator` is on `SANCTIONED_VALIDATORS` today, and
  `test_only_sanctioned_modules_build_queries` scans `src` to keep the list
  honest. No widening is needed; the seam was cut in WP3.2 for exactly this.
- **Grounding has an authority.** `catalog_tables` / `catalog_columns` via
  `browse.active_catalog`. An unknown identifier must produce a structured error
  that *names* it — the agent repairs from that message, so it is a feature.
- **Column policy is decided, stored, and durable.** `effective_policy` answers
  allow | mask | deny, survives a refresh (D-013), and distinguishes a person's
  decision from the safe default. The validator consumes it; it does not
  re-derive it.
- **`Caps` states per-engine truth** — dialect, `limit_syntax`,
  `max_identifier_length` — so nothing in `dal/` should branch on an engine name.
- **Masking already happened once, at write time, for *catalog samples*.** That
  is not the same thing as masking *query results*, which is WP5.2's job. Do not
  assume the first makes the second unnecessary.

WP5.2 then adds the executor, result masking and the audit hook; WP5.3 is the
adversarial corpus per dialect and the 90% gate, and it is the Phase 5 gate PR.

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
