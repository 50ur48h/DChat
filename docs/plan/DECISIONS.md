# DECISIONS — deviations & choices made during the build

Format (plan §1.6): context → options → decision → consequences, 5–15 lines.
Any deviation from `docs/architecture.md` needs an entry here **and** an edit to the
architecture doc, both in the same PR as the code.

## D-051 — a relative period is resolved against the clock, and the platform checks the data can answer it
Date: 2026-08-27 · Phase: 13 · PR: this one (spec only; built as WP13.12)
Context: asked *"what were the sales last month"* on 2026-08-27 against the MiseQ
source, the deployed product resolved **July 2026**. That database holds
**2025-01-01 to 2025-12-31** and nothing else. The run queried an empty month.

Three things had to line up for that, and all three are ours:

* `as_of` defaults to the wall clock (`agent/context.py`), which **D-027 chose
  deliberately** and is not the defect.
* `TODAY_RULE` carries the only mitigation — *"if the range runs past the end of
  the data, say so in the answer"* — as **one sentence of prose with nothing
  enforcing it**.
* The critic does not merely permit the empty range, it **requires** it:
  `stated_range` resolves "last month" against `as_of` and `_range_matches`
  **blocks** an answer whose SQL does not cover 2026-07. The platform mandates a
  query it has everything it needs to know is empty.

And it does know. `catalog_columns.min_val`/`max_val` are profiled on every
refresh, and `semantic_role` already marks time columns. That coverage reaches
the model **only as card prose at L4** — droppable to fit a budget, and framed by
`REFERENCE_FRAME` as the customer's records and *explicitly not instructions* —
while `Today is {as_of}` sits at **L0** and is never dropped. The two disagree and
the weaker one is the true one.

Options: (a) resolve relative periods against `MAX(date)`, which is what the
partner's patch asks for; (b) keep the clock and add a deterministic coverage
check; (c) tell the model to check and hope.
Decision: **(b)**.

**(a) is refused and D-027's reason still holds.** A period taken from the data
moves when data is appended: the same question means something different next
week, no answer is reproducible, and an eval pinned to `as_of` — the whole point
of B-005's seam — stops meaning anything. Their fix trades one irreproducibility
for another, and the one it trades to is harder to see. **(c) is what we have**,
and it is the shape of every entry on CLAUDE.md's defect list: a rule stated in a
prompt, verified by nothing, failing silently.

Coverage becomes a **platform-established fact**, assembled the way the capability
check is and rendered at **L0** beside it — not a hint the model weighs against
the anchor, because that is the argument it is currently losing. Three outcomes,
and the middle one is what earns the design:

* **covered** — nothing said, which is almost every run;
* **partly covered** — answer the overlap, and say which slice was used and what
  was asked for;
* **not covered at all** — refuse, naming the period asked and the period held.
  A refusal here is a *correct* answer to a question the data cannot support, and
  it is the outcome the current behaviour turns into a confident zero.

Consequences: the critic's range rule has to be told, or it will block the
corrected range as not matching the question — the check and the critic must
agree on one resolved period. `min_val`/`max_val` stop being card decoration and
become load-bearing, so the profiler's honesty about them matters more than it
did. And per CLAUDE.md this ships with proof it is **reached**: a test that drives
an out-of-range period through a run and asserts what the API returns, not one
that calls the checker directly.

## D-052 — a customer's join catalog is a third provenance: imported, then measured
Date: 2026-08-27 · Phase: 13 · PR: this one (spec only; built as WP13.13)
Context: the MiseQ v6.3 drop ships `v_join_catalog`, 69 rows the partner intends
an LLM to read before writing SQL. We will not do that — the join graph is
deterministic and architecture 4.3 says the model cannot talk its way past it —
but the *content* is evidence about a schema, and evidence belongs in the catalog
where the deterministic checks already read it.

Measured against the file, the case is quantitative rather than a matter of
taste. Of the 60 `ALLOWED` rows, **47** are within one type family and D-050's
inference can find them. **Ten cross a type family and it structurally cannot** —
every one of them `dim_outlet.outlet_key` (`TEXT`) against an `INTEGER` outlet key
on `fact_sale_line`, `fact_waste`, `fact_stock_count`, `fact_stockout_event`,
`fact_member_visit`, `fact_stock_move_outlet`, `fact_transfer` (both ends),
`dim_member.home_outlet_key` and `fact_reconciliation_gap`. **Three more are
composite** (date plus outlet), and inference measures single columns only.

So measurement reaches `dim_outlet` from `fact_sale` **only because that one
column happens to be `TEXT`**. Waste by outlet, stockouts by outlet, visits by
outlet: all still refuse. That is what import buys, and it is 13 of 60.

Options: (a) import them as `declared`, trusting the file; (b) ignore the file and
rely on measurement; (c) import as a distinct provenance and measure what was
imported.
Decision: **(c).** A customer assertion is neither the engine's constraint nor our
measurement, and flattening it into `declared` would make a hand-maintained file
indistinguishable from a foreign key the database enforces. `kind` gains a third
value.

**Imported and measured, not one or the other.** `_orphan_count` already exists,
so an imported edge can carry the claim *and* the check in `evidence`. Precedence
when several sources speak: **declared foreign key → imported-and-verified →
inferred → imported-but-unverified.** Where a claim and a measurement disagree,
the disagreement is **recorded and surfaced, never silently resolved** — a
customer whose dictionary says two columns join when 12% of rows do not has a data
problem worth being told about, and picking a winner hides it.

**The `DISALLOWED` row gets its own representation**, and this is the part to get
right. `fact_sale ↔ fact_sale_line` — *summing both double-counts revenue* — is a
**negative** edge, and the join graph has no way to say "these must not be
combined" as distinct from "no link is known". **#130 removed the sentence "the
catalog explicitly prohibits" three days ago because no prohibition existed.**
Importing this creates one that does, and the two sentences must stay visibly
different: one is the customer's rule, the other is the limit of our knowledge,
and collapsing them again would undo B-145a for the sake of a shorter code path.
Partial consolation: D-026's chasm reasoning already refuses to join two facts
under a shared parent, so the shape is caught even where the row is absent.

**Only 61 of the 69 rows are joins.** Six `READ-FIRST` and two `DEPRECATED` rows
are prose about how to answer — they are knowledge (D-053), and importing them as
edges would be a category error that puts sentences in a graph.

Consequences: a migration widening `RELATIONSHIP_KINDS`, an explicit
**Admin-initiated** import rather than discovery sniffing for a table called
`v_join_catalog` — a convention over a table name should not quietly decide what
may be joined — and one more caveat the composer can attach: *the data dictionary
says these join; we checked and found no unmatched values*, which is a different
claim from either a foreign key or an inference.

## D-055 — one vocabulary for a partial answer, and a refusal that records what it refused
Date: 2026-08-27 · Phase: 13 · PR: this one (spec only; built as WP13.12)
**Extends D-051 rather than replacing it.** This file is append-only, and none of
D-051's coverage reasoning changes — what changes is that it acquires a second
trigger and they are built once.

Context: on dev, *"how to improve sales for outlet A"* was refused with a
paragraph about missing causal inputs, having run **no query at all**. There is
no causal-question rule anywhere in the API: that paragraph is the model's own
prose. The planner's `sql` role returns `answerable: false` with a `reason`, and
`loop.py` ends the run on the spot — no query, no iteration, no second attempt.

Two things came out of diagnosing it.

**The product already has the ending this needs, and the refusal path walks past
it.** `run_state` (D-044) derives `partly` from two facts the model already
reports: what backs its answer, and what it could not answer. *"Here is what
December looked like; which actions would raise sales cannot be established from
this data"* is exactly that shape. No new state, no migration, no UI work — the
screen already renders "partly answered" and shows the caveat.

**And the platform's own checks and the model's opinion are given the same
finality, which they should not be.** The join-graph refusal three lines below
`plan.answerable` is a fact about the schema the model cannot argue with;
`plan.answerable` is a model's judgement about a question. The asymmetry is
visible in the code: the capability branch writes `state.capability` and emits
`capability_checked`; the model-judgement branch emits nothing and leaves prose.
**In the trace the two are indistinguishable, and only one of them is a fact.**
Options: (a) prompt the planner to answer descriptively where it can — which is
enforcement by prompt, judged by the same model that just refused; (b) make a
model-judgement refusal non-terminal, once, under platform control; (c) leave it.
Decision: **(b)**, bounded, with three guards, and built as one mechanism with
D-051's partial-coverage outcome because they resolve to the same two fields.

* **Strictly bounded.** At most one retry, and only where nothing has executed
  yet, so it cannot loop and cannot fire mid-investigation. **A capability
  refusal stays terminal** — the platform's own facts are not sent back for a
  second opinion.
* **The guard is `unanswered`, not the answer.** A retried run must still name
  the gap. *An honest refusal that becomes a padded non-answer is worse than the
  refusal* (owner, 2026-08-27), so the acceptance test is not "it answered" but
  "it answered **and** still said what it could not establish". A retry that
  produces no citations falls back to `refused` through D-044's existing
  derivation, with no special case written for it.
* **The refusal is recorded structurally.** A model-judgement refusal carries
  `reason` in `plan_created`'s payload beside the `answerable` it already emits —
  a JSONB addition, so no migration and no new event type, and
  `STEP_SENTENCES` can then say *why* the platform stopped rather than leaving
  the reader to infer it from an absence.

**On putting a model's sentence in a payload**, since WP13.16 has just drawn that
line for the trace: architecture 10.3 forbids *raw model reasoning*, not every
model-authored string. `plan_created.purpose` and `reflection.public_rationale`
are both short, public, model-written and already there. `reason` sits on exactly
that footing — a sentence written to be read by the person who asked — and not on
the footing of chain-of-thought, which stays out.
Consequences: one extra `sql`-role call on a genuine refusal, at the strong tier;
the owner accepted that cost on 2026-08-27 explicitly. This is a **global**
behaviour change to the honest-refusal path Phase 8's gate signed off on, and the
thing that keeps it honest is the `unanswered` guard rather than the retry being
rare. D-051's three coverage outcomes and this one share `partly` + `unanswered`,
so the product grows one vocabulary rather than two — which is why WP13.12 builds
both and why this entry exists instead of a separate work package.

## D-054 — a catalog is a function of the schema **and** of the logic that read it
Date: 2026-08-27 · Phase: 13 · PR: this one (spec only; built as WP13.15)
Context: D-050 shipped measured inference on 2026-08-26 and dev was deployed with
it on 2026-08-27. Clicking **Refresh catalog** on the MiseQ source answered *"No
change. The catalog is still version 1, describing 29 tables."* — and no inferred
relationship was written. The database had not changed; the platform had.

**The finding is not the skip. It is that a stated invariant was quietly
falsified by a feature, and nothing noticed.** `structural_hash` says, in its own
docstring:

> *"Everything WP4.1 stores about a table goes in, and nothing else does — so two
> crawls that agree on this hash agree on every catalog row, and a refresh can
> skip the table without hoping."*

That was **true when it was written and correct as reasoning**. The hash covers
table name, kind, comment, and every column's name, ordinal, type, nullability,
primary-key flag and comment — which was exactly what a snapshot stored at WP4.1.
D-050 then added something to the snapshot that is *not* a function of table
shape, and the sentence became false three days later. Nothing failed, no test
went red, and the only symptom was a feature that could never reach any catalog
built before it.

**And it is worse than a skip**, which is the part worth reading twice. Inference
runs inside `_crawl`, and `_crawl` runs *before* `_store` decides whether anything
changed. So the refresh opened the customer's database, scanned 251 columns for
distinct counts, ran up to 97 containment scans — and then discarded every result
because the hashes matched. The platform is paying the full price of the
capability on every no-op refresh and storing none of it.
Options: (a) a `force` flag, and rely on someone remembering; (b) fold the
inference result into the change comparison; (c) stamp each snapshot with the
discovery logic that built it, and treat a different stamp as a change.
Decision: **(c), with (a) kept as an operator escape hatch and never as the
mechanism.**

(b) is tempting and wrong: the inferred edges are the *expensive* output, so
comparing them means computing them, which is the cost this is trying not to pay
twice. The stamp is comparable **before** any scan.

(a) alone is the pattern this repository keeps filing bugs about — correctness
that depends on a person remembering, on a path where forgetting is silent. It is
genuinely useful for *"show me it working now"*, so it ships; it is not what makes
the guarantee.

**What the stamp is, precisely.** A snapshot records the version of the discovery
logic that produced it. `_unchanged` becomes a conjunction: the schema hashes
match **and** the stamp matches. Bumping the constant invalidates every existing
catalog's skip exactly once, and the next refresh rebuilds with the current logic.
The bump is one line, greppable, and visible in review — which is the strongest
property available here, and worth stating plainly rather than overselling: **a
capability whose author forgets to bump is exactly as invisible as today.** The
stamp converts a permanent silent gap into a gap that lasts until someone bumps a
constant, and that is a real improvement rather than a proof.
Consequences: three things, kept separate because they expire differently.

* **The sweep moves after the change decision** (WP13.15), so a no-op refresh
  costs a schema read rather than a measurement run against someone's database.
  This is a correctness-neutral change with a real operational cost attached to
  *not* doing it.
* **Staleness is not fixed by any of this and must not be sold as if it were**
  (**B-150**). `structural_hash` deliberately excludes data — *"a table whose
  contents changed has not changed shape"* — while an inferred edge is a claim
  **about data**. An edge measured in January can be false in June with a
  byte-identical schema, and both the hash and the stamp will match. That is a
  different problem needing a different answer.
* **The class has at least one other live instance** (**B-151**):
  `inference.py` calibrates its confidence constants against
  `capability.MIN_CONFIDENCE` in a **comment**, and nothing couples them. Raising
  that threshold above 0.95 would silently drop every inferred edge out of the
  join graph while discovery kept writing them and the catalog kept showing them.

The wider question the owner asked — whether a documented invariant with nothing
enforcing it has other instances — was **checked rather than assumed**, and the
answer is reassuring about the habit and not about the exceptions.
`TENANT_TABLES`, `EVENT_TYPES` and `OUTCOME_STATES` each have a test that counts
them, and `RELATIONSHIP_KINDS` builds its own CHECK from one constant so it cannot
drift. The two found without a counter are the two above. **The habit is sound;
these are the gaps in it.**

## D-053 — their prose becomes an enforced object where it can be, and knowledge where it cannot
Date: 2026-08-27 · Phase: 13 · PR: this one (spec only; built as WP13.14)
Context: the rest of the MiseQ contract — `v_question_playbook` (12 archetypes),
`meta_data_quality` (27 rows), the revenue rule, and `source_mode` on every table.
The partner's design puts all of it in a system prompt and asks the model to
comply. Ours cannot: a prompt is not a boundary here, and a rule the platform
states but never checks is the defect this repository keeps filing.

Options: (a) take the contract as prompt text; (b) drop what cannot be enforced;
(c) map each item to the strongest mechanism that fits it, and say plainly which
ones are advisory.
Decision: **(c)**, and the mapping is the decision:

* **`v_question_playbook` → `verified_queries`**, which is what that table is for.
  It does not fit cleanly and the gap is worth stating: these are twelve
  *skeletons keyed by archetype*, not approved question-to-SQL pairs, so they are
  weaker grounding than the feature assumes. `required_caveat` has no column there
  at all and belongs with limitations.
* **`meta_data_quality` → knowledge documents.** A clean fit — retrieved by
  meaning, cited, already built. **And advisory by construction**: knowledge is
  L4, framed as the customer's own records and not instructions. It will shape how
  an answer is written and it will not stop anything, which is the right power for
  framing guidance and the wrong power for a prohibition. Saying so here is the
  point; the failure mode is believing a knowledge document enforces something.
* **Rule 5 — `fact_sale` for revenue, never unioned with `fact_sale_line` → a
  semantic definition with required filters.** D-033's critic already checks a
  statement against the definitions a question matched, so this one is genuinely
  enforceable rather than merely stated.
* **`source_mode` → a composer caveat derived from what the run read**, which is
  the same seam D-050 added for inferred joins and needs no new concept. It is
  fully derivable: `fact_sale` is 112,327 rows all `real`, `fact_sale_line` 471,786
  all `synthetic`, `gold_dish_cost_margin` 49 all `derived`. An answer that read a
  modelled table says so, and it says so because of what it read rather than
  because a prompt asked it to.

**Not taken, with reasons** (owner, 2026-08-27): the contract's control flow
(*"read the playbook before writing SQL"*), which is the whole objection; the
Competition UX disclosure, which is demo staging and not product behaviour; the
progressive-disclosure UX prescription, which is a second and conflicting answer
to a design the owner already chose (D-047); and the "never say" list as prompt
text, whose intent is right and whose mechanism would read as a guarantee nobody
verifies. The deprecated objects — `map_ingredient_alias`, `fact_waste.stage` —
are loaded but must not be reachable by a question.

Consequences: four small features rather than one contract, each landing where
something already checks it, and one explicit admission — the data-quality corpus
is guidance, not enforcement. B-147 and B-148 carry what this defers.

## D-050 — an inferred join is measured, and the answer that uses one says so
Date: 2026-08-26 · Phase: 13 · PR: this one
Context: B-145. `miseq` declares **0 foreign keys and 0 primary keys**, so
`catalog_relationships` was empty and the capability check refused almost every
real question. The schema anticipated this — `kind` has allowed `inferred` with a
`confidence` since 0006 — and nothing ever wrote one.
Options: (a) match on column name and type, which is what most catalog tools do;
(b) measure containment against the data; (c) ask the model to guess from the
schema.
Decision: **(b)**, and this dataset is why the other two are not close. Name
matching would create `map_item_key.item_key -> dim_item.item_key` with total
confidence; the two columns match **0.0%** of rows. Meanwhile the edge the
product actually needs, `fact_sale.outlet_key -> dim_outlet.outlet_key`, is
**112,327 of 112,327** — and a name matcher gets that one right too, so on this
schema it would look like it worked. **A rule that is right for the wrong reason
is indistinguishable from a rule that works, until it isn't.**

The evidence is two measurements and nothing else:

* **the parent side is unique** — `count(DISTINCT c) = count(c)`. Without this
  the edge is not many-to-one and has **no direction**, and direction is what
  D-026's chasm-trap reasoning needs to tell a narrowing hop from a fanning one.
* **the child side is contained** — an exact `NOT EXISTS` scan returning **0**
  orphans. Not "mostly contained": an edge at 99% is a join that silently drops
  rows, which is a wrong answer with no symptom and therefore worse than a
  refusal.

**And containment on its own is worth almost nothing, which the first live run
against miseq is what proved.** It produced ten edges. **Eight were rubbish**,
and every one of the eight was a correct measurement: `outlet_key` holds five
values, `1` to `5`, so it is contained in `fact_transfer.transfer_id` (1 to 9),
in `fact_sale.sale_id` (1 to 112,327), and in seven more. *Every dense integer
range contains a small one.* The test the owner set would have passed on the
`map_item_key` half while the feature was, in fact, inventing joins.

Two further rules came out of that run, both arithmetic on counts phase 1 has
already measured, so both cost nothing and run before any scan:

* **Coverage** — the child's distinct values must account for at least 90% of
  its parent's. Five values explain a five-row `dim_outlet` completely and a
  112,327-row key not at all, and the difference is the whole distinction
  between a key and a number that happens to fit. It is set that high because a
  **lone candidate is never contradicted**: 0.5 left
  `fact_sale_line.outlet_key -> fact_transfer.transfer_id` standing at 0.556,
  since `dim_outlet.outlet_key` is `text` in that schema while
  `fact_sale_line.outlet_key` is `bigint` — the real parent was excluded on type
  and a nine-row surrogate counter ran unopposed.
* **Many-to-one** — the child must repeat some value. A foreign key *is* a
  fan-in, and a child that never repeats has not shown one. This is what rejects
  the case coverage leaves standing: `dim_outlet.outlet_key` sits inside
  `fact_transfer.transfer_id` at 0.56 coverage with no competitor to lose to,
  and unique-inside-unique is exactly what two independent surrogate counters
  look like.

Where a child is contained in several parent *tables*, the better-covered one
wins and the losers are recorded in the evidence; where nothing dominates, **no
edge is written** — two equally good candidates are not half an answer. A column
that fits more than twelve tables is skipped whole rather than resolved, since
resolution over a truncated candidate list can crown a winner the real parent
never ran against.

Names are used for **nothing** in any of this. Candidates are narrowed by type
family and by distinct-count arithmetic, and two identically named columns get
exactly the scrutiny two differently named ones do.

Confidence is scored from how much data stood behind the measurement, not from
how plausible the pair looks: `0.95` above 100 child rows, `0.92` above 10,
`0.80` below that — under `MIN_CONFIDENCE`, so a three-row coincidence is
**recorded and not relied on**. Every edge stores its evidence in
`catalog_relationships.evidence`, so a wrong one is traced rather than argued
about.
Consequences: the coverage floor **will miss a real key**, and on this very
dataset it already does — `fact_coupon.member_key` binds to
`gold_member_rfm.member_key` (coverage 1.0) rather than to `dim_member`
(coverage 0.63), because only 63% of members hold a coupon. Not a wrong edge, but
not the better parent either. Recorded as B-146 rather than smoothed over. It is the safe direction: a
missing edge refuses, an invented one answers, and the wrong join returns a
cartesian product rather than an error.

Also a new nullable JSONB column (revision 0032) and a discovery step
that runs **only when the engine declared nothing** — a database with real
constraints pays none of this. The work is bounded (`MAX_PAIRS_CHECKED`,
`BUDGET_SECONDS`, and a containment scan that walks the child's **distinct**
values rather than its rows — the same claim, and the difference between asking
a parent 471,786 questions and asking it five) and reports honestly when it stops
early rather than presenting a partial sweep as a complete one. `JoinGraph` now carries which edges were inferred; the planner is
told, and `limitations_for` adds a caveat to any answer whose executions actually
read one — silently passing an inference off as a declaration is how B-057's
cartesian product gets back in. Architecture 10.1 updated for the new column.

## D-049 — waiting, emptiness, and showing something before it is true
Date: 2026-08-26 · Phase: 13 · PR: this one
Context: sixteen places in the product either said `Loading…`, said `None yet.`,
or said nothing at all while something was happening. The owner picked all six
candidates from the `C-states` mockup. The interesting part is not that they were
built — it is that **three different situations were being served by one muted
sentence**, and they make different claims.
Options: (a) one spinner everywhere, which is what most products do; (b) three
patterns chosen by **what is actually knowable**; (c) skeletons everywhere,
including for things that are not lists.
Decision: **(b)**, and the choosing rule is the whole entry.

* **Skeleton — the shape is known, the content is not.** A list whose rows will
  arrive. It says *how much* is coming and stops the page jumping; it makes no
  claim about progress, so unlike a spinner it cannot turn out to have been
  wrong. Never for a non-list, and **prefer too few rows**: three skeletons
  where one item arrives is a small lie about the shape of the answer, and the
  page still jumps — upwards, which is worse, because the reader has started
  reading.
* **Pending — even the shape is unknown.** A one-shot request that returns once:
  a catalog refresh, a column profile, a connection test. A shimmering word, an
  optional indeterminate bar, then the API's own result sentence. **It must never
  be given steps**, because an operation that reports no steps can only be given
  invented ones, which is what D-048 refuses.
* **Meter — there is a real number.** A proportion may be drawn only where two
  real counts exist. Document ingestion is the one place in this product that
  qualifies (`chunk_count`, `embedded_count`). Before any passage is stored the
  denominator does not exist, so no bar is drawn at all — a bar at 0% claims a
  total nobody knows.

**Empty states carry presence and exactly one action**, and the action slot is
also where a Reader is told *who* can act instead — never a disabled control,
which looks operable and is not (B-008). And **an empty state is not a loading
state**: `null` is *not asked yet*, `[]` is *there is nothing*, and rendering
"Nothing yet" during a request tells somebody something false about their own
data. This product has made that mistake before.

**Showing a question before the server has stored it.** The chat home creates a
thread, posts the question and navigates, and for that moment the screen showed
nothing the person had typed. The question is now rendered immediately — and
**faint**. The faintness is the honest part: it is the interface saying *I have
not been told this worked yet*. If the write fails the bubble goes and the reason
takes its place; it never quietly hardens into something that looks saved.
Anything shown optimistically anywhere in this product owes the same two
behaviours.
Consequences: three primitives in `components/ui/` — `SkeletonList`,
`EmptyState`, `Pending` — and `docs/design.md` gains *Waiting, and having nothing
to show* plus *Showing something before the server has confirmed it*, both
binding. A skeleton announces itself **once** through a single `role="status"`
label, with the bars `aria-hidden`: eight anonymous boxes read out in sequence is
worse than silence. Sixteen call sites changed and five existing tests moved from
the old wording to the new.

## D-048 — the working state shows the run, and never a script
Date: 2026-08-25 · Phase: 13 · PR: this one
Context: the owner supplied a design for the thinking/working state — an
expandable agent trace with a shimmering status word, steps appearing with a
spinner and settling to checks, collapsing to *"Thought for N seconds"*. The
reference implementation drives it from a hard-coded `STAGES = [800, 600, 1800,
2600, 1600]` and a fixed list of rows: a sequence that looks like work and is a
timer. This product already streams the real thing — `agent_events`, append-only,
architecture 10.3 — and the owner's own framing was that it "shouldn't be a fake
spinner".
Options: (a) port the reference as given, with the scripted timings, and swap in
real data later; (b) port only the *look*, and drive every row from the event
stream that already exists; (c) a hybrid — real events, but hold each row for a
minimum time so the sequence "reads" better.
Decision: **(b)**, and **(c) is explicitly refused**. A minimum display time is
the same lie as a scripted one, told more carefully: it makes the interface's
account of the work disagree with the work. This product's entire claim is that
its account of itself is checkable — the trace is append-only *precisely* so it
can be shown as a record rather than a story (`trace.tsx` has said so since
WP8.3) — and a progress display that runs ahead of what happened would be the
most convincing lie the interface is capable of telling. So: every row is one
durable event, in the order it was written, with the wording the trace already
maps type names to. If the stream stops, the display stops.
**The duration is held to the same standard.** *"Thought for N seconds"* comes
from the run's `started_at` and `finished_at`, falling back to the first and last
event timestamps. When neither is knowable the word is `Thought` with no number,
because a rounded-up guess is a fabricated measurement and this is a product that
refuses those elsewhere.
Consequences: `docs/design.md` gains *The working state* as a binding pattern,
written **before** the code, including the rule above and the bound on motion —
a shimmer, a fade-up and a grid-rows transition, all off under
`prefers-reduced-motion`, and the trace fully readable with every one of them
disabled. `Trace` is rewritten rather than replaced, so both call sites and every
import keep working; its rows stopped being badges and became words with colour,
which is rule 4 applied one level down.
**One defect this produced, caught before it shipped, and worth the entry on its
own.** The status word was first wrapped in `role="status"` with
`display: contents` — and the result was a button with **no accessible name at
all**: the text was trapped inside the live region, so a screen reader announced
"button" and nothing else. It was found by an e2e locator that could not match
the button by name, which is the only reason it was found. The label is a plain
child of the button now and `aria-live` does the announcing without removing the
text from the name computation. **A live region belongs beside an interactive
control, not inside one**, and `display: contents` is not safe on anything whose
text has a job.
The pattern is deliberately **not** applied to operations that cannot report
progress. A catalog refresh, a column profile and a connection test are single
requests that return once; giving them steps would mean writing the steps in the
client, which is the thing this entry refuses. Those get an indeterminate
shimmer and their real result. Document ingestion is the honest second home, and
which other states get the treatment is the owner's pick from `C-states`.

## D-047 — warm paper, a serif answer, and chrome that outranks nothing
Date: 2026-08-25 · Phase: 13 · PR: this one
Context: WP13.1b shipped the chat product against the design system as written —
cool greys, borderless cards floating on `#f7f8fa`, an indigo primary. The owner
looked at it and said it "looks weird", and, importantly, that they were not sure
`design.md`'s direction was what they wanted. That is a different problem from a
screen being wrong: the binding document was the thing in question, so restyling
against it would have been building more of what was being doubted.
Options: (a) tune the existing direction — refused, because the doubt was about
the direction rather than its execution; (b) two static HTML mockups, same screen
and same content, one applying `design.md` properly and one taking a different
aesthetic, and let the owner choose; (c) a redesign chosen by whoever was
writing it.
Decision: **(b)**, and the owner chose the alternative over four rounds of
revision. `docs/design.md` is rewritten to match, **before any code**, because it
is binding and a document that trails the screens is a document nobody trusts.

**What changed.** Warm paper (`--paper #faf9f5`) instead of cool grey; surfaces
in tints of the same warmth; **depth that is layered rather than dropped** — a
hairline *and* a wide faint shadow, because a shadow alone is what made the old
cards look like they were hovering. One terracotta accent replaces indigo. A
**serif for the words the agent produced** and a sans for every piece of chrome,
which is the change that does the most: it separates *what the machine said* from
the interface around it. A longer spacing scale, actually used.

**Three structural fixes the owner named, which are not aesthetic and would have
been owed under either direction.** The sidebar is `position: fixed` and full
height, with only its chat list scrolling — sticky left it in the flow, so a long
thread could still scroll it away. The identity block is a
`grid-template-columns: 32px minmax(0, 1fr)`, so the avatar and the address
cannot overlap at any width or any address length. And the thread lost its page
title and Back button: in a chat product the thread *is* the panel and the rail
is the way back.

**One column, and the reason it was two.** The answer sat in a `28px | 1fr` grid
so the agent's mark could stand in a left gutter, which inset the prose while the
composer stayed full width. The mark now heads the answer instead of flanking it.
The consequence is that **the column width is the reading measure**, since the
answer fills it: at the answer size that tops out near 936px before a line passes
~95 characters, which is long by convention and acceptable for a product whose
answers are a sentence or two. A screen that rendered paragraphs would need to
cap its prose and accept the ragged edge.

**Rule 4 is new and is the one that will be cited most**: the content outranks
the chrome. It was added because both of the owner's last two rounds were the
same complaint — the attribution row and the evidence bars were heavier than the
answer. Attribution is now a 12px caption with no fill and nothing at badge
weight, and the run's ending is a coloured **word** with a dot beside it rather
than a pill.

**The working is folded away and revealed on hover**, the way message actions
behave in Claude and ChatGPT, in native `<details>`. Hover is a de-emphasis and
never the only route: `opacity` rather than `display: none` keeps the control in
the tab order and in the accessibility tree; `:focus-within` reveals it for a
keyboard; `@media (hover: none)` shows it always on touch; and `[open]` is set on
the element itself rather than on an ancestor via `:has()`, so an opened panel
can never sit beneath an invisible summary. Because touch has no hover, the
summaries are full 40px targets — quiet is the fill, not the size.
**A caveat is never folded**, and that exception is the point: a limitation
changes what the answer means, and hiding the qualification while showing the
claim is the defect B-133 and D-044 exist about.
Consequences: `docs/design.md` is rewritten; `globals.css` redefines the legacy
token names in place rather than renaming them, because a dozen CSS Modules
consume `--bg`, `--fg`, `--primary` and `--space-N` and repainting by value is a
smaller change than editing every file. **The chart ramp is untouched and that is
deliberate** — it was validated against the card surface, `--surface` is still
`#ffffff`, so the validation still holds; rule 3's "one series uses the primary"
now yields clay, which resembles slot 2's orange, and that resemblance is
recorded as accepted because the two cannot appear in the same chart. `--clay` is
`#b55231` rather than the `#c15f3c` in the approved mockup: white on the brighter
tone is 4.23:1 and fails AA on the Send button, and the correction was made
rather than shipped. A finding with a single citation now shows its query as soon
as *Evidence* is opened, so the common case is one click rather than two.
**The accepted cost, recorded rather than discovered later**: a first-time reader
may not notice that evidence exists at all. That is the price of a focused
answer; the cheap retreat is a resting opacity near 40% instead of 0.

## D-046 — light is the default, and the operating system does not get a vote
Date: 2026-08-25 · Phase: 13 · PR: this one
Context: `globals.css` defined dark under `@media (prefers-color-scheme: dark)`,
which is the conventional default and made the operating system the decision
maker. The consequence is easy to miss and was: **anyone whose desktop is set to
dark had never seen the design this product is actually designed in.**
`docs/design.md` describes light — "light backgrounds, generous space, rounded
cards that lift off the page", `--bg #f7f8fa` chosen so cards can lift off it —
and the dark tokens are a translation of that, validated separately but never the
subject. The owner set light as the default for the chat product on 2026-08-25;
this records how, and what it costs.
Options: (a) keep the media query and add an override, so three states exist —
OS-dark, explicit-dark, explicit-light — refused below; (b) drop the media query
and gate dark on an explicit choice alone; (c) drop dark entirely, which throws
away a validated palette and a real preference.
Decision: **(b)**. Dark is defined under `[data-theme="dark"]` and under nothing
else. There is no `prefers-color-scheme` rule left in `globals.css`, which is the
point: **one route into the dark values, and no combination of media query and
attribute that can disagree.** (a) reads as more considerate and is the version
with a bug in it — a person on a dark desktop who chooses light needs an
attribute that beats the media query, so every token needs two selectors and
`:root:not([data-theme="light"])` guards, and a token defined in only one of the
three places is a colour that is right in two states and wrong in the third.
That failure is invisible in review and appears as one unreadable card.
**A pre-paint script, and it is the one script this app injects.** The attribute
has to be on `<html>` before the browser paints, and the earliest React can run
is after hydration — several hundred milliseconds of white for someone who chose
dark. `app/layout.tsx` therefore carries a `dangerouslySetInnerHTML` script that
reads storage, sets one attribute, and stops. Nothing in it is dynamic: the only
interpolation is `THEME_STORAGE_KEY`, a compile-time constant from our own
module, and it writes an attribute rather than markup. It is wrapped in
`try/catch` because a browser with site data blocked **throws** on
`localStorage` rather than returning null, and an exception there would run
before anything else on the page. `<html>` carries `suppressHydrationWarning`
for exactly this, scoped to that element.
**Read through `useSyncExternalStore`, not an effect.** The obvious shape —
default in `useState`, correct it from an effect — is a cascading render that
`react-hooks/set-state-in-effect` rejects, and it flashes the wrong value for one
frame. `lib/persisted.ts` wraps `localStorage` as an external store with a
separate server snapshot, which keeps the server HTML and the first client render
identical. It deliberately does **not** cache: `Object.is` on a string already
compares by value, so a cache buys React nothing and adds a way for the module to
disagree with storage that nothing invalidates — clearing site data does not fire
a `storage` event in the window that did it. The sidebar's collapsed flag uses
the same store.
Consequences: `docs/design.md`'s "Dark mode" section is rewritten and now says
the attribute is the only selector; adding a `prefers-color-scheme` rule back is
a change to that section first. Dark remains fully supported and is reachable
from Settings → Appearance. A person on a dark desktop who has never opened
Settings now sees the light design, which is the intended change and the whole
cost of the decision. The choice is per browser and never leaves it: there is no
server-side preference, and one is not owed until a person asks why their phone
disagrees with their laptop.

## D-045 — an Admin chooses the database once; a member never picks
Date: 2026-08-25 · Phase: 13 · PR: this one · Migration: 0031
Context: today a member signs in, picks an organization, clicks **Ask**, picks a
dataset, and only then lands on a conversation. That is a database tool's flow.
The product being built is a chat product — a member opens it and starts talking
to their data — and in that product the database is something an Admin configures
once, not something every member chooses at the start of every thread. The owner
set this direction on 2026-08-25; this entry records how it is done without
losing what **D-022** bought.
Options: (a) leave the choice on the conversation and hide the picker behind a
default in the browser — refused, because a default the client invents is a guess
with a nicer name, and D-022 exists to stop the platform guessing; (b) drop
`conversations.data_source_id` and resolve the organization's source at each run
— refused, because a thread would stop recording what its answers were drawn
from, and an Admin changing the organization's database would silently re-point
every conversation ever written, including ones whose answers are already on
screen; (c) an **organization-level** choice, stamped onto the conversation when
the thread is created; (d) infer it from the question, which is (a) with more
steps and D-022 already refused it.
Decision: **(c)**. Revision 0031 adds `organizations.active_data_source_id`,
nullable, `REFERENCES data_sources(id) ON DELETE SET NULL` — deliberately the
same shape D-022 chose one table over. `create_conversation` stamps it onto the
new thread when the caller names no source, so **the conversation still records
the database it is about** and D-022's reasoning is untouched: a follow-up
question still reaches the same source as the question it follows, and two
answers in one thread still cannot come from two databases. What moves is where
a person makes the choice, not whether the platform records it.
**A column, not a key in `organizations.settings`.** The JSONB column was already
there and would have needed no migration — and no foreign key, so a deleted
source would leave a plausible id behind for every reader to re-check
defensively. `ON DELETE SET NULL` degrades the pointer to *none chosen*, which is
a state the resolver already handles correctly, instead of to a dangling id,
which is a state nothing handles.
**The refusal is kept, not replaced.** `resolve_data_source` consults the
organization's choice first and otherwise behaves exactly as it did: one
registered source resolves, several refuse and name the candidates, none refuses
and says what to do. An Admin naming the database is **not a tie-break** — it is
the person deciding that WP7.2c's refusal asked for. When no Admin has chosen,
every organization from before this revision reaches the same two refusals it
reached yesterday.
**The foreign key is not the tenant check**, and this is the sharper case of the
warning D-022 wrote: a constraint check does not consult row-level security, so
another organization's source id satisfies the database perfectly well, and here
it would point *an entire organization* at another tenant's data — every question
every member asks, answered confidently and with citations, from the wrong
company's database. `orgs/service.set_active_data_source` looks the id up through
the org session and answers 404, and a test registers a second organization's
source and proves it.
Consequences: `docs/architecture.md` 10.1 gains the column on `organizations` and
10.2 gains `GET`/`PUT /v1/orgs/{org_id}/active-data-source`. The read is open to
**any member** — the chat screen has to know whether asking is possible before it
offers a composer, and it discloses only the name of a database every member
already queries. The write is Admin-only and audited as
`org.active_data_source_changed`. An explicit `data_source_id` on
`POST …/conversations` still wins: this fills a blank, it does not override a
caller who said what they meant. The member-facing picker this makes redundant is
removed in the shell work package, not here.

## D-044 — a run has three endings, and the platform derives which
Date: 2026-08-25 · Phase: 13 · PR: this one · Migration: 0030
Context: WP7.2b's rule is *"a run that could not answer completes with
`answered=false` and a reason"*, which assumes a run either answers or does not.
The first engine trial found three runs of *"which outlet wastes the most, and
what does it cost?"* that recorded `answered=false` while answering the volume
half — *"Outlet C, 3.398 kg across 2 waste events"* — two of them with a **cited,
verified** finding. The platform's own record asserted the run produced nothing,
in the same transaction as a claim it stood behind (**B-134**). Revision 0029 had
put that boolean on a column and the API one day earlier, so anything built next
would inherit a distinction that does not hold.
Options: (a) keep the boolean and word the badge better — refused because the
record is wrong, not the label; (b) add a third state the **model** chooses; (c)
add a third state the **platform derives** from what the run produced.
Decision: **(c)**, three states — `answered` | `partly` | `refused` — and
`FinalizeIn.answered` is **deleted** rather than supplemented.
**A model free to pick "partly" would be as arbitrary as the boolean was wrong**,
which is the owner's objection to (b) and the reason the judgement is removed
instead of reworded. The model reports two *facts*: what backs its answer
(`supported_by`, already there) and what it could not answer (`unanswered`, new,
a few words). `composer.run_state` derives the rest:
* `unanswered` empty → **answered**;
* `unanswered` named and something cited → **partly**;
* `unanswered` named and nothing cited → **refused**.
**`unanswered` is the primary signal and citations only split the remainder, and
that is a correction.** The first rule made *no citations* mean `refused`
outright; three existing tests went red and were right to — whether an answer is
*backed* is a different question from whether it was *given*, and widening a
refusal to cover it would have smuggled a behaviour change into a change about
vocabulary. The critic and **B-138** are where the first question lives.
**The card names the missing half, never just the state.** A CHECK constraint
makes `partly` impossible without `unanswered`, so *"could not answer the cost"*
is always renderable and a bare *"partly answered"* badge is unreachable **by
construction** rather than by convention — which is what survives somebody
editing the UI later.
Consequences:
**WP7.2b's rule is amended**, and `runner.py`'s header says `refused` where it
said `answered=false`. Four critic rules used `draft.answered` to mean *"is this
a draft making a claim I should check"*; they now read `claims_an_answer`, which
is observable — and a **partial** answer with citations is now checked, where the
boolean skipped it precisely when its cited half deserved review.
**Revision 0030 replaces 0029's column** rather than joining it: two fields that
must agree are two fields that will not, and the boolean has no true value for a
partial run. Back-fill asserts what each row already said — `true → answered`,
`false → refused`, `null → null` — which is a record of what was stated when
partial was not representable, not a claim those runs were total refusals. The
alternative lost the badge for every historical refusal to avoid a claim no
reader would misread. Owner's call, 2026-08-25.
**The residual risk is a model that leaves `unanswered` empty when something is
missing**, which silently downgrades a partial answer to an answer. Nothing here
guards it. **The critic is where that guard belongs** — it already blocks a
dropped required filter and could check that an answer addresses each quantity
the question asked for. Deliberately not built here: it is a rule about question
decomposition and deserves its own review.
**Not built here either**: the eval harness maps `state != "refused"` onto its own
`answered` flag, because that flag means *"did the run produce an answer at all"*
and a partial one did.

## D-043 — Phase 12 stops after WP12.2; WP12.3 and WP12.4 are deferred, not cancelled
Date: 2026-08-25 · Phase: 12 · PR: this one · Migration: none
Context: WP12.2 has taken far longer than planned and produced eleven merged PRs,
because every defect it found was real and none was visible from the repository —
B-120, B-123, B-124, B-125, B-126, B-127, B-128, B-130. The deployment path is now
proven end to end bar one blocker (**B-131**). What has *not* moved in that time is
the product: the owner's F&B trial found five genuine defects in the agent's
schema understanding, and the UI is still the Phase 7 skeleton. The owner's
judgement on 2026-08-25 is that further hardening of an environment nobody uses is
worth less than either of those.
Options: (a) finish Phase 12 as written — observability, quotas, the restore
drill, ASVS-lite, `v1.0.0`; (b) stop after WP12.2 and pivot to product work,
cancelling 12.3 and 12.4; (c) stop after WP12.2 and **defer** 12.3 and 12.4 with
their remaining scope written down.
Decision: **(c)**. Phase 12 ends at WP12.2. The next two work packages are an
**engine trial loop** and a **UI rebuild**, in that order.
Consequences, stated plainly because the cost is real:
**There is no `v1.0.0` tag**, and there will not be one until WP12.4 is resumed.
Nothing in the repository should claim otherwise; architecture Part 14's
acceptance list stays as written and stays unmet.
**The deployed app has no quotas** (**B-025**) **and no retention sweep**
(**B-021**). Dev is externally reachable with a $1.00 per-run cost cap as the only
spend control, and `LLM_RUN_COST_LIMIT_USD` is a per-run ceiling, not a budget —
n runs cost n dollars. The Azure budget alert is the backstop and it alerts rather
than stops.
**Observability is wired and unread.** `APPLICATIONINSIGHTS_CONNECTION_STRING` is
set on both containers and declared in `PLATFORM_ENV` as belonging to an exporter
that does not exist yet; no OpenTelemetry code is in `apps/api`. Container console
logs reach Log Analytics and that is the whole of it.
**Neither deferral is a scope cut in disguise.** What each still owes is written
out in STATUS under *"Deferred: what WP12.3 and WP12.4 still owe"*, at the
granularity needed to resume without rereading plan §6.

## D-042 — WP12.4's gate, amended for a dev that holds no data
Date: 2026-08-24 · Phase: 12 · PR: #107 · Migration: none
Context: the owner ruled on 2026-08-24 that **dev proves the deployment path and
never hosts a demo** — no customer data, no fixtures, no registered data source,
because registering one means a credential to a real database in a subscription
with a public hostname. That rule is right and it makes three lines of WP12.4's
gate unsatisfiable as written, each of which assumes dev can answer a question
about somebody's data.
Options: (a) leave the gate as written and quietly fail it, or quietly seed dev
to pass it — the second is what the rule exists to prevent and the first makes
the gate decorative; (b) amend the three lines and say what replaces each; (c)
drop the three criteria, which loses the property each was protecting.
Decision: **(b)**, three amendments.
**1. "Quota hard-stop proven in dev" → proven against the deployed API with a
seeded ledger.** A quota stops a run; a run needs a data source; dev has none.
Seeding `usage_ledger` directly and watching the deployed API refuse proves the
same enforcement — the ledger is what the quota reads — without a query behind
it. Still proven in dev, still against real infrastructure.
**2. "Nightly evals enabled against dev" → they stay local and in CI.** The
evals need the pizza fixture, which is a compose service; running them against
dev would mean deploying a seed database, which is precisely the forbidden thing.
This is the one criterion that is genuinely dropped rather than restated, and the
honest reason is that it was written before the dev rule existed.
**3. The restore drill records that it restored an empty schema.** Restoring,
running the migration check and the RLS proof against a fresh server still proves
the backup and the recovery path. What it cannot prove is that customer data
survives, because there is none — and the evidence in `docs/hardening-v1.md` says
so, or a later reader takes it for a guarantee it never made.
Consequences: the gate is weaker in one place (no live eval run against a
deployed environment) and unchanged in the rest — ASVS-lite, dependency audit,
rate limits, managed identity, `v1.0.0` from dev. The lost property is that
nothing exercises a real model against real infrastructure before v1.0; the
compensating position is that the local live walks do exercise it, and that
**B-029** already tracks the provider coverage gap. Architecture Part 14's
acceptance list is edited in the same PR, as §1.6 requires.

## D-041 — Phase 12 stands up dev only; prod is a documented next step
Date: 2026-08-22 · Phase: 12 · PR: #92 (WP12.1) · Migration: none
Context: Architecture 9.1 says in as many words *"two environments (`dev`,
`prod`)"*, and plan WP12.4's gate is *"dev+prod live via Bicep only"* with
`v1.0.0` tagged after a prod deploy. That is a deviation this entry has to
record, because the code is about to stop matching the sentence.
Options: (a) both environments, as written — two of everything, an approval
gate, a second set of secrets, and a prod Postgres running from the day it is
created; (b) dev only, prod deferred, `v1.0.0` tagged from dev; (c) dev only and
delete the prod parameter file, so nothing suggests a prod that does not exist.
Decision: **(b)**, the owner's call of 2026-08-22, with the gate rewritten rather
than relaxed. **Every WP12.4 criterion still applies — the restore drill, the
quota hard-stop, managed identity everywhere, the ASVS-lite checklist — just
against dev.** Nothing is dropped; one environment is. (c) was refused because
the point of the deferral is that prod costs an approval and a parameter file
later, not a rewrite, and a parameter file nothing compiles is one that has
already drifted.
Consequences: `infra/params/prod.bicepparam` exists, is **not** deployed and is
**not** referenced by any pipeline — and CI compiles it on every `infra/**` PR
precisely because nothing else would notice it rotting. A parameter added to
`main.bicep` and not to prod fails the build on the day it is added rather than
on the day somebody needs prod. Its values are recorded with their reasoning and
are explicitly a starting point rather than a decision: GeneralPurpose rather
than Burstable, because a burstable tier accrues credits and then throttles,
which is survivable in dev and is a latency cliff under load; `minReplicas: 1`
rather than 0, because scale-to-zero costs a cold start that is a customer
waiting rather than a developer. **Architecture 9.1 is amended in this PR** and
plan WP12.4's gate is rewritten. The thing to be careful of when prod does
arrive: this decision means `v1.0.0` will have been tagged from a subscription
that never ran a production workload, so the first prod deploy is a *new* risk
rather than a repeat of a proven one — and the restore drill and hardening
checklist should be re-run there rather than inherited.

## D-040 — CI's model is a stub inside the shipped image, refused twice everywhere else
Date: 2026-08-20 · Phase: 11 · PR: #87 (WP11.2b) · Migration: none
Context: The M11 gate wants a browser driving the **real** product in CI — real
API, real platform database under RLS, real DAL, real catalog, real seeded
customer database. Every part of that chain runs in CI for free and
deterministically except one: the model. Architecture 8.3 has no offline mode
and the product deliberately has none either (the README says so), so something
has to stand in.
Options: (a) a real key — no fork's PR can use a secret, and a green build would
then depend on a provider's uptime and a model's mood; (b) leave CI on `FakeLLM`
inside pytest, which is where it already is and which never starts a browser, so
"the stack wires up" stays unproven; (c) a scripted provider selected by
environment, shipped in the ordinary image.
Decision: **(c)**, the owner's call of 2026-08-20. `LLM_PROVIDERS=scripted`
selects `llm/scripted.py`, which answers each role with the smallest thing that
satisfies that role's schema. It is in the **image**, not in a test harness, on
purpose: a CI-only image would prove a build nobody deploys.
Consequences: **a stub reaches the shipped image, and that is the whole risk.**
`ProviderCaps.is_stub` already names the hazard — a stub in production would not
fail, it would fabricate, confidently, in a product whose entire claim is that
its answers are evidenced. So **two independent guards**, and they stay separate
because they see different things. At boot,
`Settings.assert_llm_providers_are_production_safe` refuses to start a production
build or environment naming a stub in `LLM_PROVIDERS` — boot rather than first
call, for the reason the auth and secrets assertions give: a service that starts,
answers its health check, accepts a question and *then* refuses has already told
an operator it was fine. At first use, `registry.get_provider` refuses any
provider whose capabilities report `is_stub`, which catches what configuration
cannot see — one handed to `register_provider` at runtime. The test that carries
this **boots a real app** with production settings and the scripted provider
selected, across both halves of `is_production`; calling the checker directly
would prove the function refuses and say nothing about whether the boot path asks
it. **And the gate wording is part of this decision**: plan WP11.2 now says in as
many words that the CI smoke proves the stack wires up and can never show that a
question was understood, with the chart criterion met by a live walk against a
real model. A gate signed off on a canned answer would be B-087's failure at the
level of the gate itself.

## D-039 — A conversation is archived, never deleted
Date: 2026-08-20 · Phase: 11 · PR: #86 (WP11.2a) · Migration 0026
Context: Plan WP11.2 lists "conversation history list + rename/**delete**". A
conversation is the root of everything the product promises to be able to show
afterwards: its runs, their events, their findings, their query executions.
Architecture 0.2.4 makes that trace durable and revision 0002 holds
`agent_events` append-only *by grant* — the application role cannot rewrite it.
A cascading delete would destroy the evidence behind answers a person may
already have acted on, and it would do it from a list screen, which is the
surface where a misclick is cheapest to make.
Options: (a) delete the row and let the cascade run; (b) archive — a timestamp,
hidden from the list, everything underneath untouched; (c) archive now and add
true erasure later.
Decision: **(b)**, taken by the owner on 2026-08-20, with two conditions.
**The UI must say Archive, not Delete** — in the owner's words, *"a button that
says delete and hides instead is a lie to the user"*. A test asserts the word and
asserts that no control says "delete", because the word is the promise.
**True erasure is named as a Phase 12 retention story** rather than left as an
implied someday: a customer asking for their data to be gone needs every table,
a receipt that it happened, and a retention window — none of which is a button on
a list screen.
Consequences: revision 0026 adds `conversations.archived_at`, nullable, a
timestamp rather than a flag because the question worth answering later is
*when*. `list_conversations` takes `archived` and returns one list or the other,
never both — an archived thread left in the default list would make the button
look broken. The route is `POST …/conversations/{id}/archive` rather than
`DELETE`: nothing is removed, and the reverse direction exists, which a DELETE
could not offer. Archiving is idempotent and keeps the first timestamp. The test
that carries this decision asserts against the *record* — that the run, its
answer, its method and its events all survive — because asserting `archived_at is
not None` would pass just as happily on an implementation that had cascaded
everything away. **Plan §6 WP11.2's wording is corrected in this PR.**

## D-038 — A run whose every query failed is refused, not answered
Date: 2026-08-20 · Phase: 11 · PR: #85 (B-095) · Migration: none
Context: **B-095.** Both executions of a live run ended in `gaierror` — the
platform never reached the database — and the answer read *"I couldn't show
total net sales by month because **no data was returned** from the queries"*
with `limitations: []`. The reader was told their data was empty when nothing
had been asked of it: a claim about the customer's data where the fact was about
the platform. Tracing it turned up a second, sharper half. The loop had **already
written the true sentence**, quoting the connector's own sanitized message, and
the runner discarded it: the test guarding the no-compose path asks whether
anything *ran* (`not state.executions`), and a failed execution is still an
execution, so the run fell through to `_compose` — where a model is handed a list
of refusals, no results, and an instruction to answer.
Options: (a) refuse without composing, ending on the sentence the loop wrote;
(b) still compose, so the prose is question-shaped, and have the platform force
`answered=false` the way `_confidence` caps a disputed draft; (c) leave
`answered` to the model and only add the missing limitation.
Decision: **(a)**, taken by the owner on 2026-08-20.
(c) was refused because it fixes the silence and leaves the misdirection: a run
that reached the database zero times could still present itself as answered.
(b) buys question-shaped prose for a model call, and buys it in the one situation
where the model has nothing to write from — the case that produced the
confabulation in the first place. A model given no evidence does not decline; it
describes the absence as a finding. (a) is also the only option that is *cheaper*
than the defect: it spends no composing call at all.
Consequences: `every_query_failed()` on `ResearchState` is the predicate, and it
is deliberately not "nothing ran" — a run that answered from the catalog or from
a document without querying is a legitimate ending and still composes. The
refusal carries the budget caveat through `_finalize_refusal`, which it did not
need to before this decision gave that path a second caller. `ExecutionRef` gains
`error`, set **only** for a failure no rewrite could fix, because the loop records
policy refusals as failed executions too and those are routinely corrected by the
next iteration — a limitation on every repaired run would be a caveat about a
self-correction. The pre-existing test of this path asserted `answered` but never
`status`, so it had been passing while the run ended `failed`; the new one asserts
both.

## D-037 — A chart is asked for with the answer, kept with the answer, and refused in the answer's own place
Date: 2026-08-19 · Phase: 11 · PR: #82 (WP11.1) · Migration 0024
Context: WP11.1 had three placement questions and none of them is visible in the
code once the choice is made, so each would be re-litigated by the next person
who looked. **Where the request rides**: `Plan` is a closed schema (B-033), so a
chart field there costs tokens on *every* planning step for a feature few
questions use. **Where the outcome is kept**: plan WP11.1 said the spec goes on
the finding. **Where a refusal renders**: the obvious home is `limitations`,
beside B-093's source line.
Options: (a) the request on `Plan`, once per step; (b) on `FinalizeIn`, once per
run; (c) no model involvement — infer the chart from the result's shape. For
storage, the finding or the run. For the refusal, `limitations` or the chart's
own slot in the card.
Decision: **(b), the run, and the card's own slot.**
*The request rides on `FinalizeIn`* — once per run rather than once per step, at
the moment the model knows what it answered and which execution backs it, which
is also B-048's argument for the chart living inside the answer. (c) was refused
on the owner's reasoning: `charts.decide` can refuse an impossible chart but
cannot know *which* chart answers the question — the same numbers are a
comparison or a trend depending on what was asked, and choosing from the data
alone is the silent choice B-060 was filed for. Measured cost of the closed
schema: **+1,357 chars, ~339 tokens per run**, on the finalize call only.
*The outcome is kept on the run* (`agent_runs.chart`), because a refusal can
exist on a run that reached no finding at all — attaching it to a finding would
give the drawn half a home and the refused half none. Architecture 4.2 already
carries chart specs on the `ComposedAnswer`, so this corrected the **plan text**
and left the architecture untouched.
*A refusal renders where the chart would have been.* `limitations` is titled
*"What this answer does not establish"* in its accessible name, and every other
member of that list bears on whether the answer is **true**; a line about a
picture under that heading teaches a reader to skim the region that also carries
an unresolved critic block (B-079, from the other side).
Consequences: `charts.decide` returns a spec **or** a sentence and the type
enforces it, so a rule nobody has written yet cannot produce silence. **The spec
is assembled server-side** from a closed vocabulary and the result's own column
names — there is no field a URL can arrive in, which is why a browser test can
assert the page fetches nothing off its own origin. That property is the reason
**Recharts was declined** on 2026-08-19 when the owner raised it: a client-side
chart library builds the picture from data in the browser and dissolves the
validated-spec contract architecture Part 3 chose over a code sandbox. A future
change of renderer has to keep the server-built spec or replace that test with
something as strong.

## D-036 — An edited definition is versioned, not overwritten
Date: 2026-08-18 · Phase: 11 · PR: B-088 · Migration 0022
Context: **B-088** asked for an edit route, because a definition was write-once —
no edit, no un-accept, re-accepting a 404 — and the owner hit it mid-gate-walk
with `psql` as the only way back. It also asked whether an edit should be
**versioned**, which architecture 5.4 has said since it was written (definitions
are *"validated against the catalog at save time and versioned"*) and which
nothing implemented, because until editing existed there was nothing to version.
That changes the day the route ships: a definition **binds** — `required_filters`
are enforced against the AST of generated SQL — so *"what did this metric require
when that answer was written"* is a question about whether an answer was right,
and every overwrite between shipping the edit and shipping the history is a
question that can no longer be answered.
Options: (a) overwrite the row and treat the audit trail as the history —
cheapest, no migration, but `audit_log.details` is a generic blob and
reconstructing a past state from it is versioning reimplemented badly, on data
nobody promised to keep in that shape; (b) copy-on-write rows in
`semantic_definitions` — breaks `(data_source_id, name)` uniqueness and makes
every reader filter for the live one; (c) a monotonic `version` on the live row
plus an append-only snapshot table.
Decision: (c). `semantic_definitions.version` counts the states a definition has
been **in force** in, and `semantic_definition_versions` holds each of them in
full — name, description, expression, filters, synonyms, status — with the
change that produced it (`created`, `accepted`, `updated`, `retired`), who made
it and when. The whole state rather than a diff, because the person asking has an
answer they distrust in front of them and will not replay a chain. A **proposal
is not a version**: it binds nothing while it waits, so version 1 is the state
that first took effect.
Consequences: the table is append-only in the database, not by convention — 0022
revokes UPDATE and DELETE from `dataagent_app` (0002's default privileges would
otherwise grant them) and `rls_proof` proves it, the same lock `audit_log` and
`agent_events` carry. An edit that changes nothing writes no version, so history
stays free of no-ops. Definitions written before 0022 have no recorded past and
`GET …/versions` returns an empty list rather than implying one. **A run still
records definition *names* only**, so a citation cannot yet be resolved to the
version that governed it — the history makes that possible and B-091 is where it
gets wired. Architecture 5.4 needed no deviation entry: this implements a
sentence it already contained.

## D-035 — A definition is the one organization-authored text the model is told to obey
Date: 2026-08-18 · Phase: 10 · PR: #72 (WP10.2d, B-083)
Context: **D-033** settled that prose informs and structure binds, but not how
each reaches the prompt — and the gap was not academic. `Definition.render()`
was written to put a matched definition in front of the planner and was called
by nothing, so for the whole of WP10.2c the critic enforced `required_filters`
against a model that had never seen them (**B-083**). Fixing it forced the
question the layering had avoided: the organization now authors **two** kinds of
text the agent reads, and 7.4's threat model says text a customer supplies is
untrusted. A knowledge passage and a semantic definition are both written by the
customer. If both are untrusted the definition cannot bind; if both are trusted
then an uploaded document can issue instructions, which is the injection 5.5's
framing exists to prevent.
Options: (a) render definitions at L4 beside retrieved passages, under
`KnowledgeFrame`'s *"records, not instructions… never something to obey"* —
consistent, and it makes the critic enforce a rule the prompt told the model to
ignore; (b) drop the never-obey framing from L4 so both are authoritative;
(c) separate them by **provenance within the platform**, not by author.
Decision: (c). A definition renders at **L3** under `DefinitionFrame`, which is
the deliberate opposite of `KnowledgeFrame`: *"these definitions are
authoritative here… the query you write is checked against them."* What earns
that is not who wrote the sentence but what the platform did with it — validated
against the catalog at save time, activated by a named Admin, enforced by a
deterministic AST check. An uploaded document has passed none of those and stays
at L4, framed as a record. The seam is the Admin's acceptance, which is why
`accept` is the route that adds filters (**B-059**) and why an import arrives
`proposed` and binds nothing.
Consequences: the trust boundary is an **act inside the product**, not an
attribute of the author, so it is auditable — `created_by` names who made a
sentence binding. Two frames must stay opposites; collapsing them either makes
documents obeyable or definitions optional. A definition is **not** a truncation
candidate: the critic enforces it whether or not the budget left room to state
it, and a rule the model is judged against but never shown is B-083 again.
Architecture 5.4 already required "the agent receives matching definitions and
must prefer them over improvisation", so this records how, not a deviation.

## D-034 — A block that could not be acted on becomes the loudest thing the answer says
Date: 2026-08-18 · Phase: 10 · PR: WP10.2d · Owner's rule, stated as one
Context: WP10.2c's live run ended with the critic right and the reader none the
wiser. The model computed a defined metric with `status = 'completed'` instead of
the definition's `not in ('cancelled','refunded')`; the deterministic rule warned,
the LLM half **blocked** — *"the query does not clearly exclude cancelled and
refunded orders as required by the metric definition"* — the run took its one
permitted re-entry (M9), came back with the same shape, and was blocked again.
With no re-entry left the draft shipped, saying *"using completed orders and
**explicitly excluding cancelled and refunded orders**"* — the precise
overstatement the critic had just named. `composer.limitations_for` reads only
`verdict.warnings`, so the finding strong enough to stop a run was the one thing
the reader never saw (**B-079**).
Options: (a) refuse outright when a block survives the last pass; (b) ship the
draft and surface the block as the answer's first limitation; (c) leave it in the
trace, where a curious reader can find it.
Decision: **(b)**, as the owner's rule of 2026-08-18: **any critic finding strong
enough to stop a run must reach the reader.** If a run ships despite a block, the
block becomes the **loudest limitation on the answer**, not a silent trace entry.
It goes **first**, ahead of the budget caveat, because a caveat about
incompleteness and a finding that the answer may be *wrong* are not the same kind
of doubt and the second one has to be read first.
**An answer that overstates its own rigour is worse than one that admits doubt.**
That is the whole of it. A hedged answer costs a reader some confidence; an
answer carrying a confident sentence the platform has already judged false costs
them the ability to tell the two apart, and it does so in the product's own voice.
Why not the others: **(a)** throws away work that is often mostly right — the
number was correct in the live case and only its description was overstated — and
turns 4.5's "violations become warnings in V1" into a refusal engine, which is
the false-block failure standing note 5 exists for, arriving by a different road.
**(c)** is what shipped and is what B-079 is: a trace nobody reads is not a
disclosure.
Consequences: `limitations_for` takes the unresolved blocks first and in the
critic's own words, so the sentence a reader sees is the one the critic wrote
rather than a paraphrase. `confidence` is lowered when a block survives, because
a draft the platform disputes cannot be `high`. And this makes the **Phase 10
gate criterion** for WP10.2d, at the owner's direction: the demo shows a blocked
answer whose block is the first thing on the card.

## D-033 — Prose informs the model; a structured definition binds it
Date: 2026-08-18 · Phase: 10 · PR: WP10.2b · Owner's principle, stated as such
Context: WP10.2a made the agent able to consult a document mid-run, and the live
run that proved it also broke it. Asked what an *anchor order* was, the agent
retrieved the policy — *"a completed order of more than 40 pounds placed on a
weekday… neither is anything placed on a Saturday or a Sunday"* — wrote exactly
that as SQL, and then over two further iterations reasoned its way **out** of the
weekday clause and answered 1,054 where the document says 747. It discarded the
definition in the open, with a rationale, and nothing in the system could object
(**B-078**).
Options: (a) treat retrieval as sufficient grounding and rely on the model to
honour what it read; (b) make every definition structured before it may be used,
so nothing rests on prose; (c) two kinds of grounding with two different
strengths, and say which is which.
Decision: **(c)**, in the owner's words on 2026-08-18: **"prose informs the
model, a structured definition binds it."** A retrieved passage is *evidence the
agent may use*. A semantic definition with machine-readable filters is a
*constraint the critic enforces*. They are different objects with different
guarantees and the product must not blur them.
Three things follow, and each is a requirement rather than a nicety.
**The Phase 10 gate's central criterion is the enforcement, not the compliance:**
the demo must show a run where a definition's filter is *required*, the model
*drops* it, and the critic *catches* it. A run where the model happens to comply
demonstrates nothing about the constraint, which is precisely the trap the
anchor-order run fell into — it complied at iteration 2 and stopped complying at
iteration 4.
**An answer grounded only in prose is unverifiable, and says so.** If the critic
can enforce structured definitions alone, then a run that leaned on a retrieved
passage carries a **limitation** in its answer stating that its definition was
not machine-checked. That is WP9.2's assembled kind of limitation — a fact the
run knows, not a hedge the model writes.
**Blessing prose into structure is the path between them** (B-059's import,
pointed at documents as well as at a customer's metadata tables): an Admin turns
a passage into a definition, and the answer stops carrying the limitation because
the claim stopped being unverifiable.
Why not the others: **(a)** is what WP10.2a shipped and B-078 is the evidence
against it. **(b)** would make the corpus useless until somebody structured all
of it, which is the same mistake as a product that only accepts definitions
retyped (B-059) — most organizations would be left with the honest half of
nothing.
Consequences: two grounding paths, two strengths, and a user-visible difference
between them. The composer must be able to say *why* an answer is less certain
than it looks, which means the limitation has to name the term rather than
gesture at "a document". A definition that is imported and blessed removes the
limitation, so the import path is not a convenience — it is how a customer buys
enforcement for the definitions they already wrote down.

## D-032 — The planner asks for a definition, and a lookup costs an iteration rather than a call
Date: 2026-08-18 · Phase: 10 · PR: WP10.2a · Owner's direction on the criterion
Context: **B-075** — `loop.research` dispatches `run_sql` and nothing else, so
`search_knowledge` is registered, described in every prompt, and unreachable. The
backlog entry left two shapes open: **(a)** retrieve knowledge into the context
deterministically, as architecture 4.4's *Context* stage describes, or **(b)** let
the agent choose to look something up, as 4.4's *Execute* stage describes. The
entry recommended (a) as the smaller change.
Options: (a) context-stage retrieval keyed on the question's words; (b) the
planner may request a lookup and the loop dispatches the tool; (c) neither —
deregister `search_knowledge` so the prompt stops advertising it.
Decision: **(b)**, on the owner's direction of 2026-08-18: *"an agent that's told
it can search documents but can't dispatch the tool means Phase 10 ships a
feature the product can't reach"*, and the gate demo must show the agent
**consulting a document mid-run**. `Plan` gains an optional request for a term to
be defined; when it is set the loop calls `search_knowledge`, puts the passages in
front of the next plan, and records both in the trace.
**A lookup consumes an iteration rather than adding a model call to one.** That is
the load-bearing detail: an iteration that looks something up costs one plan call
and no reflect (nothing ran to reflect on), so it is *cheaper* than an ordinary
iteration and **D-024's and D-028's arithmetic is untouched** — the worst case
this build can spend is unchanged. Bounded further by a per-run cap on lookups
and by refusing a lookup already made, which is the duplicate-query hash's shape
applied to a second kind of repetition.
Why not the others: **(a)** answers a different question. It retrieves on the
agent's behalf, keyed on the words of the question, so a definition is found only
when the question happens to name it — and the trace shows a retrieval the agent
did not choose, which is not what *"the agent consulted a document"* means. It
remains the right mechanism for **semantic definitions** in WP10.2b, which are
structured objects matched by entity rather than prose matched by meaning; the
two can coexist. **(c)** is honest and cheap and was rejected by the owner as
shipping less product: the corpus would exist and never reach a run.
Consequences: `Plan` grows a field, so the planner's prompt and schema change and
every agent test that asserts on a plan sees it. A model that asks for a lookup on
every iteration spends its iteration budget on lookups and answers nothing — which
the per-run cap bounds and the barren-iteration rule already ends. The tool list
is now honest: everything in it can be dispatched.
**A twenty-first event type**, `knowledge_consulted` (revision **0019**), and
architecture 10.3 is edited to name it. 10.3 fixes the vocabulary deliberately —
a trace UI has to render each type — so widening it is a decision. The argument
is the gate criterion itself: `tool_called` records the *asking*, and there is no
execution row to carry the *answer*, so what was asked, whether anything was
written down, and which documents replied would otherwise be invisible.
Overloading `result_summarized` would make the timeline lie about what kind of
step it was.

## D-031 — An embedding is a spend like any other, and a refused one degrades the search
Date: 2026-08-17 · Phase: 10 · PR: B-073
Context: WP10.1 built hybrid retrieval and then called it from the agent's tool
**without an embedder**, because putting one on `ToolContext` puts a spending
capability inside the loop and neither guard around spending could see it:
**D-019**'s per-run ceiling reads `usage_ledger` rows *for the run*, and the
query embedding was charged to no run at all; **B-040**'s test guard wraps
`registry.get_provider`, and an embedder does not come out of there. Retrieval
therefore ran on its lexical arm alone (**B-073**), and the question this PR had
to answer first is what should happen when the ceiling *does* see an embedding
call and refuses it.
Options: (a) let the tool fail, as a chat call does when the ceiling stops it;
(b) run the lexical arm and return its passages silently; (c) run the lexical arm
and **say** the other one did not.
Decision: **(c)**, plus two structural halves — `embed_texts` takes a `run_id` and
checks D-019's ceiling before **each batch**, so an embedding is metered and
capped exactly as a completion is; and `get_embedder` becomes the one door an
embedder comes out of, wrapped by the B-040 guard in the same fixture and with
the same message as the provider door.
Why not the others: **(a)** contradicts 8.5, which calls budget exhaustion *not a
failure*, and it trades a working half of retrieval for consistency — the lexical
arm has already been paid for and still answers. **(b)** is the worse failure of
the two: a search that quietly halved itself returns *"nothing is written down
about that"*, which reads as a fact about the customer's documents and invites a
model to stop looking and answer from its own knowledge. `Retrieval.degraded`
exists so the two cannot be confused, and the tool puts the degradation **before**
the "nothing found" sentence for that reason.
Consequences: **an unpriced embedding model under a ceiling is refused**, which is
D-019's existing rule arriving through a new door — so `LLM_PRICES` must name the
embedding model or every capped run silently loses its vector arm (`.env.example`
says so). `search_knowledge` now returns a `Retrieval` rather than a list, because
*how much of the search ran* is a separate fact from *what it found*. A search
with no run — a person on the documents page — is uncapped, which is the same
hole `llm/budget.py` already states plainly and which B-025's org quotas close.

## D-030 — PDF text comes from pypdf, not pymupdf: the license decides it
Date: 2026-08-17 · Phase: 10 · PR: WP10.1a
Context: architecture 5.5 names the extraction stack as *"pymupdf, python-docx,
plain md/txt"*, and WP10.1 ships md/txt/pdf-text for V1. PyMuPDF is dual-licensed
**AGPL-3.0 or commercial**. This repository is **public** and its own license is
still undecided (**B-001**, open since 2026-08-10, "all rights reserved by
default until it is chosen"). Adding an AGPL dependency to a public repository
with no license of its own does not merely add a dependency: it constrains what
B-001 may later decide, and it does so silently, in a `pyproject.toml` line
nobody would think to re-read when the license question is finally taken up.
Options: (a) pymupdf as the architecture says, and note the licence implication;
(b) pypdf, a pure-Python BSD-3-Clause reader; (c) no PDF in V1, deferring it.
Decision: **(b) pypdf**, verified as `License-Expression = BSD-3-Clause` from the
installed package's own metadata rather than from memory or a web page — the same
habit B-027 applies to model ids, for the same reason: what a thing *is* has to
be checked, not recalled. Architecture 5.5 is edited in this PR to name pypdf and
to say why.
Why not the others: **(a)** makes a licensing decision as a side effect of a
feature, which is exactly the kind of choice B-001 exists to have made
deliberately; the owner may still choose a licence that is AGPL-compatible, and
this decision does not prevent that — it only refuses to prejudge it. **(c)**
would narrow the WP's stated scope, and PDF is the format an operations policy
actually arrives in.
Consequences: pypdf is slower and extracts a plainer text layer than pymupdf —
no layout reconstruction, no table structure. For **chunked retrieval** that
costs little, because chunking discards layout anyway and what matters is
sentences in reading order. What it does not do is OCR, so a **scanned** PDF
yields nothing; `extract.py` treats a near-empty extraction as a **failure with a
reason naming OCR** rather than as a successful upload of nothing, which is the
failure mode that would otherwise look identical to success. If layout fidelity
or OCR is ever needed, revisit this with the licence question settled first.

## D-029 — A conversation is a conversation: the thread renders at L5, as reference material
Date: 2026-08-17 · Phase: 9→10 · PR: B-064 · Owner's direction on the first question
Context: **B-064 (P1)**, found by the owner in the Phase 9 gate demo — a question
asked, then *"check again"*, answered with "no business question has been given".
Traced end to end, nothing was broken: `_question_of` read one string off
`agent_runs.question`, `ContextBundle` had no field for a prior turn, and L5
rendered that one question. **No message but the current one had ever reached any
prompt.** It is a **specification** gap as much as an implementation one —
architecture 4.8's six layers have no slot for a thread, and the only
"conversation history" in the plan is WP11.2's *list* of past conversations,
which is navigation. What was anticipated is the half **D-022** built: 10.1 says
a conversation carries its `data_source_id` *"because a follow-up must reach the
same source as the question it follows"*, so follow-ups were foreseen as a
concept and only their routing was built.
Four questions had to be settled before any code, and they are settled here.
Decision, in order:
**(1) Which layer — L5, inside the question turn, above the question itself.**
The owner's direction: it is user-supplied text and gets the same treatment as a
retrieved RAG chunk, so it is framed as records rather than directions and
nothing in it may outrank the platform rules. Any higher and 4.8's precedence —
soft everywhere else by design — would have one place where it was simply absent.
The question is rendered **last**, so a crafted earlier turn is never the final
word. The alternative considered and rejected was replaying prior turns as real
`user`/`assistant` messages, which reads more naturally to a chat model and is
worse here for exactly that reason: an `assistant` turn is the model's own voice
and carries more authority than any framing we could wrap it in.
**(2) How many — three turns, clipped to 400 characters each.** A ceiling rather
than a guess, and the same argument 4.4 makes about an investigation: a prompt
must not grow with the length of a thread, because the cost is paid on every
iteration of every run from here on. Three carries the follow-ups people actually
write — *"and by store?"*, *"why?"*, *"same for June"* — and stops short of a
transcript. It is also a **truncation candidate**, dropped oldest-first *before*
any table card is dropped: a follow-up read without its thread is a question
misunderstood, while a question with no cards is one that cannot be answered at
all.
**(3) The answer goes back in, not only the question.** *"Why?"* means nothing
without it. The hazard the backlog names is real — restating numbers invites the
model to re-cite what it did not run — and it is answered twice: the frame says
in words that a number in an earlier answer is not a result you obtained, and
`runner._verified_citations` already drops any citation this run did not produce.
The prompt half discourages; the structural half is what actually holds.
**(4) A follow-up may not cite the previous run's executions. It re-queries.**
Unchanged from WP7.2b, and now deliberate rather than incidental. A citation
resolves through `GET …/runs/{r}/executions/{q}`, which reads the execution
**through** its run — so a cross-run citation would open onto nothing. Evidence
that 404s is worse than no evidence, because it looked like proof.
One thing was found while building it and fixed in the same PR: *"check again"*
names no table, so the card search returned **nothing** and the planner was to be
handed an empty catalog — which is **B-041**, the defect that cost the M7 gate,
arriving by a new road. The fix takes B-041's own shape: the strict search on the
question keeps every promise it makes, and only when it matches nothing at all is
the thread searched instead. The trace records which happened
(`tables_found_via`), because "which words chose these tables" is precisely the
silent choice **B-060** was filed for.
Consequences: `ContextBundle` gains `history` and `cards_from_thread`;
`runs.service.conversation_history` reads the turns under RLS through the run
being executed; three prompts render the thread through **one** function
(`context.history_block`) — the layered prompt, the loop's reflection and the
critic's rubric — because a thread worded three ways is three chances for one of
them to read as an instruction. The critic gets it for a specific reason: asked
whether a draft answers *"check again"* with no idea what was being checked, a
model says it does not, and a **false block on a correct answer** is this
component's characteristic failure (standing note 5). No migration: every row
this needs has existed since revision 0012. **A first question's prompt is
byte-for-byte what it was**, which is what makes this safe to ship under twenty
golden evals, none of which is a follow-up. Architecture 4.8 and 10.1 are edited
in this PR to say the thread is part of L5. What is *not* done: no query
rewriting, no summarised "conversation memory", no cross-conversation recall —
each is a design of its own and none is needed for a thread to hold together.

## D-028 — The call ceiling moves to 24, because the critic is the stage D-024 predicted
Date: 2026-08-16 · Phase: 9 · PR: WP9.1
Context: **D-024** fixed architecture 4.4's arithmetic — an iteration costs two
model calls, so a run using all eight spends 16, plus intake and compose, giving
18 against a ceiling of 20 — and said in as many words what would happen next:
*"if a stage is ever added to the loop, or Observe is ever given a model, the
iteration ceiling and the call ceiling stop fitting and one of them has to
move."* WP9.1 adds that stage. A critic costs one call per composed draft, and
architecture M9's bounded re-entry means a run may compose twice, so the worst
case grows by four. Counting what the code actually does today rather than what
4.4 describes: there is **no intake call** — nothing emits `intent_classified`
and no role is asked for one — so a full run today is 16 + 1 = 17, and with the
critic 16 + 2 + 2 = 20. Exactly at the ceiling, with nothing spare, and 21 the
day intake is built.
Options: (a) leave the ceiling at 20 and let `llm_calls` end the longest runs;
(b) take the re-entry out of the iteration budget by stopping the loop at seven;
(c) raise the ceiling.
Decision: (c), to **24**. The arithmetic, worst case: 8 iterations x 2 = 16,
compose twice = 2, critic twice = 2, intake when it exists = 1, total **21**
against **24**. Why not the others: **(a)** would mean a run that used its
iterations honestly is reported as `budget_exhausted` for an accounting reason
rather than for the reason a person would recognise — 4.4's own principle is
that a run stops at the ceiling describing what it did, and "reasoning limit"
would be a lie about a run that simply thought eight times; **(b)** hides the
cost of a feature inside a different feature's budget, and the iteration ceiling
is the one number in 4.4 a reader can reason about.
Consequences: `DEFAULT_LLM_CALLS` is 24 and architecture 4.4's arithmetic
paragraph is rewritten to match, in this PR. `MAX_OVERRIDES["llm_calls"]` stays
60, so the ceiling an organization may raise to is unchanged. The guard that
made this deliberate rather than incidental was
`test_the_defaults_are_the_numbers_the_architecture_names`, which failed the
moment the constant moved — it now also asserts the **sum**, so the next stage
added to the loop fails with the arithmetic in front of whoever added it rather
than with a number to edit. The three spare calls are the same headroom D-024
argued for and are not a rounding: a stage costing two calls per draft would fit,
and one costing three would not, which is the signal the headroom exists to give.

## D-027 — The run is told what "today" is; the model does not choose
Date: 2026-08-16 · Phase: 8 · PR: B-005 · Owner's direction
Context: **B-005** was filed as an eval problem — the seed pins
`END_DATE = 2026-07-31`, so *"last full month"* stops meaning July as real time
moves on. Investigating it before deciding showed the defect is a product one.
Nothing in the prompt said what the current date was, so the model picked an
anchor per question and picked **differently**: one live run resolved *"revenue
last full month"* with `DATE_TRUNC('MONTH', CURRENT_DATE)` — the database
server's clock — and another, minutes later, resolved *"why did revenue decline
recently"* with `DATE_TRUNC('MONTH', MAX(order_date))` — the data's own last
date. The first drifts with the wall clock, the second does not drift at all, and
on the day they were measured **both were right**: today is 2026-08-16, so
`CURRENT_DATE` lands in August and "last full month" is July, which is exactly
where the fixture stops. A right answer for a reason nobody chose, which is the
same shape as **B-051** (a card that lied) and **B-060** (a silent choice between
two defensible tables). Eight of the twenty golden evals depend on "now".
Options: (a) pin every eval question to absolute dates; (b) a `SEED_END_DATE`
override that CI fixes while local demos track today; (c) give the run an
explicit `as_of` and make the model resolve every relative period against it;
(d) wait until it breaks.
Decision: (c), with (a) kept for the evals that were always about a fixed window.
`as_of` is a field on `ContextBundle` and on `ToolContext`, defaulted to the wall
clock — which is what a person asking in a browser means — and passed explicitly
by anything that needs determinism. It is rendered at **L0** as its own titled
block, so a tight budget can never drop it, and it names both escapes: no clock
function, and no `MAX(some_date)` either. Why not the others: **(a)** cannot
express golden eval #19 at all, since nothing is permanently in the future, and
it stops the evals testing the relative-time handling every real question uses;
**(b)** makes CI and local into different products, which is the configuration
that hides bugs rather than catching them, and it still leaves the anchor
undefined — `CURRENT_DATE` against `MAX()` would remain a coin flip; **(d)** was
two weeks of runway into the middle of Phase 9.
The seed's `END_DATE` stays frozen and `truths.json` is untouched, which was the
point of pinning them in the first place.
Consequences: the prompt grows by ~132 tokens on **every** call, L0 being
undroppable and the loop paying it per iteration — 2.2% of the default budget,
and the cost of the property. `as_of` is written to `context_selected` so a
reader of a trace can see what "last month" meant, and onto the checkpoint so a
resumed run keeps the anchor it started with rather than silently changing what
"recently" means halfway through. Verified live: the same question at the same
`as_of` produces **byte-identical SQL** with literal dates and no clock function,
and at `as_of = 2027-03-09` it resolves February 2027 and answers *"the orders
data ends on 2026-07-31, so it does not cover the required month"* — the drift
made visible instead of silently returning zero rows. **What this does not do:**
there is no way for a user to say *"as of 30 June"* in the UI, which is a real
want and is **B-062**; and WP9.1's deterministic critic can now check that the
range in the SQL matches the range the question stated, which it could not have
done against a range nobody defined.

## D-026 — A join path is safe by direction, not by degree
Date: 2026-08-16 · Phase: 8 · PR: WP8.4 (B-057) · Owner's direction
Context: `capability.py` collapsed every declared foreign key into undirected
adjacency and treated *reachable* as *joinable*. In a star schema that is false.
Loading a real F&B warehouse exposed it: every fact and dimension carries
`business_key` referencing a **one-row** `dim_business`, giving that node degree
15 and a path between almost any two tables. The check therefore called
`fact_sale → dim_business → fact_purchase` answerable — 112,327 rows against
13,660 sharing a single key value, which is a 1.5-billion-row cartesian product.
That is the exact failure `capability.py`'s own docstring opens with, arriving
*through* the check rather than around it. The DAL's row cap bounds what comes
back and does nothing whatever for the aggregate, which is the number a person
reads. The pizza fixture has no hub table, so no amount of testing against it
could have shown this.
Options: (a) refuse to route a path through a node above some degree; (b) refuse
to route through a dimension with one row, or one distinct key; (c) cap path
length; (d) keep the direction the foreign keys already declare and refuse only
the shape that actually fans out.
Decision: (d), and the verdict becomes **three-valued** rather than gaining a
second refusal. A foreign key is many-to-one *by construction* — its target is a
unique key or the engine would have rejected the constraint — so every edge
carries a direction the undirected adjacency was discarding. Child→parent
**narrows**; parent→child **fans out**. A path is a safe join exactly when it
never turns *up then down* at an intermediate node, because such a node is a
shared parent whose two children are being multiplied rather than matched — the
textbook **chasm trap**. A pair with a chasm-free path is `joinable`; a pair
reachable only through a chasm is `comparable`; a pair with no path is
`unreachable`, and only that last one refuses.
Why not the other three: each treats a symptom. **(a)** is arbitrary and wrong
in both directions — a legitimate `dim_date` referenced by twelve facts trips it
while a two-row hub slips under. **(b)** fixes one instance: a five-row
`dim_business` still yields a fifth of a cartesian product, stated just as
confidently, and it makes a structural property depend on today's row counts.
**(c)** cannot separate the cases at all, since `payments → orders → customers`
is two hops and safe while `fact → dim → fact` is two hops and fatal. (d) is
structural — no threshold to tune, no statistics to sample, nothing that drifts
as the data grows — stays deterministic as 4.3 requires, and reads columns
`catalog_relationships` already stores.
**`comparable` must not refuse.** Two facts over a shared dimension genuinely
are comparable: aggregate each to the common grain, then join the aggregates. So
the planner is handed that instruction rather than a prohibition. Turning the
middle case into a refusal would trade B-057 for **B-058** — a fluent refusal of
an answerable question — which is the worse defect, because a wrong answer is at
least checkable in principle while a false refusal teaches people the product is
broken.
Consequences: `JoinGraph` gains `parents` (empty means direction unknown, which
reads as the pre-D-026 behaviour so a hand-built graph is never *more* strict
than the catalog); `safe_path` searches over `(node, arrival direction)`;
`CapabilityChasm` carries the hub so the guidance can name the key to aggregate
to. 10.3's event vocabulary is untouched — a chasm rides in the existing
`capability_checked` payload rather than earning a type no UI can render.
`answerable` still means "no unreachable pair", so the Phase 8 gate's criterion
is unchanged and its sign-off stands. **What this does not do:** `graph.check`
sees the *set* of tables a statement names, not how it joins them, so a correct
aggregate-then-join CTE is indistinguishable here from a direct join of detail
rows. Blocking at that point would refuse the right answer with the wrong one,
so stage 1 steers the planner up front — where 4.3 puts it — and records the
chasm in the trace. Blocking needs the join predicates, which means a
`join_pairs()` sibling to `tables_named` inside `dal/validator.py`, and that is a
security-boundary change owed its own reviewed PR.

## D-025 — A card's figures come from the engine, or they are absent
Date: 2026-08-16 · Phase: 8 · PR: B-051 · Owner's direction
Context: `profiler.py` computed `min_val`/`max_val` from the sampled values.
`pg_sample` returns the **first** n rows by design — it must not sort a
customer's production table — so the "range" was the range of the oldest rows.
On the demo catalog `orders.order_date` was recorded as ending **2025-03-11**
when the column runs to **2026-07-31**, and the M8 revenue-decline scenario then
refused an answerable question, correctly reasoning from a card that lied. This
is the second time this class has bitten: WP4.3 fixed the same thing for row
counts ("about 5,000 rows" about a 71,798-row table).
Options: (a) keep the sampled range and label it as sampled — cheap, and leaves
a figure that is wrong for any table larger than the cap; (b) take min/max from
the engine; (c) publish no range at all.
Decision: **(b), falling back to (c)** — the owner's rule: *an absent figure is
safe, a wrong one is not.* One `MIN/MAX` aggregate **per table**, not per column,
so the cost is the same order as the sample beside it; bounded by the connector's
own timeout, and **any failure means the range is omitted rather than falling
back to the sample**. Only numeric and temporal columns are asked for one — an
aggregate over free text costs a scan and buys nothing, and "email runs from
a\*\*\* to z\*\*\*" was never information — so those columns now carry no range at
all rather than a sampled one.
Consequences: two new sanctioned queries (`pg_ranges`, `tsql_ranges`) in
`connectors/introspection.py`, which holds the policy grant and is therefore
review-sensitive. Profiling does one more query per table. **The general rule is
now testable rather than remembered**: a figure a card states as a fact about the
column must come from the engine, and a figure that can only come from the sample
is allowed only where the card says so — `distinct in sample` and `examples:`
both already did, which is why only the range was wrong. `profile_column` has no
code path from sampled values to a range, so it *could not* publish one; a test
reintroducing the old line fails on the exact wrong dates the demo catalog held.
Existing catalogs keep their wrong ranges until re-profiled, so the demo catalog
is re-profiled as part of this change.

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
**Closed 2026-08-17 by B-018**, and the condition this decision set is exactly
what was met: the column arrives in revision **0018**, in the same PR as the
backfill that fills it and the rerank that reads it. The thing that unblocked it
was not a key — the key had been available since WP10.1a — but **B-073**, which
made an embedder reachable from `build_context` as a metered, capped spend rather
than an unwatched one. The index question this entry left open is still open and
still deliberate: revision 0018 creates no vector index, for the reason revision
0016 gave and one more of its own (a missed *card* costs the planner the table
its question is about, which is worse than a missed chunk).

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
