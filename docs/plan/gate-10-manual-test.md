# Manual test script — Phase 10 gate (WP10.2d, PR #72)

Everything below has been run except where a step says **you must do this** —
those are the rendered browser UI, and step 7, which needs a model to misbehave
and cannot be asserted into existence.

Roughly 20 minutes. Steps 6–8 spend real API credit (about 25k tokens total).

**Setup** — already done for you on this machine (rebuilt, migrated to
revision **0021**, all five containers healthy, embedder reaching the API). To
redo it from scratch:

```sh
git checkout p10.2d-import && git pull
make up               # rebuilds and starts; recreating is what picks up host edits
make migrate
```

**Where to go**

| | |
|---|---|
| App | <http://localhost:3000> |
| Org | `sourabh` (`ebfe8139-abbb-45ee-8e21-8ed3c3b50642`) |
| Data source | **F&B demo** (`ad18394b-0fb7-45a7-ad4e-66a68cf8b241`) |
| Definitions screen | <http://localhost:3000/orgs/ebfe8139-abbb-45ee-8e21-8ed3c3b50642/data-sources/ad18394b-0fb7-45a7-ad4e-66a68cf8b241/definitions> |

Sign-in is **Entra** here, not dev names. The org has 0 definitions and an
active 35-table catalog, so step 3 starts clean.

**One thing needs your decision before step 2.** That step needs a **Reader**,
and this org has two members and both are Admin — you (`sourabh@rereed.com`) and
`sourabhkumrawat02@gmail.com`. I have not changed either. On the members screen,
set the second one to **Reader** (two clicks, and it exercises the members UI on
the way past); afterwards set it back to Admin the same way.

---

### 1. The backlog can no longer lose a row (B-081)

```sh
bash scripts/check_backlog.sh --selftest
bash scripts/check_backlog.sh
```

**You should see** `16 passed, 0 failed`, then `check_backlog: docs/plan/BACKLOG.md intact`.

Now the same guard against the commit that caused the entry:

```sh
git show dc35e7a:docs/plan/BACKLOG.md > /tmp/broken.md
git show dc35e7a~1:docs/plan/BACKLOG.md > /tmp/base.md
BACKLOG_FILE=/tmp/broken.md BACKLOG_BASELINE_FILE=/tmp/base.md BACKLOG_BASE_REF= \
  bash scripts/check_backlog.sh
```

**You should see** four findings naming **B-076** — it does not begin a line, there
is a gap where it should be, B-080's row carries fifteen columns, and it was on
the baseline and is gone. CI reported success on that commit.

**The failure case:** delete any row from `docs/plan/BACKLOG.md` and run
`bash scripts/check_backlog.sh` again. It must fail. Restore it with
`git checkout docs/plan/BACKLOG.md`.

---

### 2. A Reader sees none of it (B-008) — **you must do this**

Sign in as a **Reader** and open
`/orgs/{org}/data-sources/{id}/definitions`.

**You should see** one sentence: *"Only an Admin can review what a metric means
here, because an accepted definition constrains the queries this platform will
run."* No import form, no Accept, no Reject, and **no definitions or proposals
listed at all**.

Open the browser's network tab and reload. **You should see no request to
`/definitions`** — the screen does not ask for what it would be refused.

Then sign in as an **Admin** and open the same URL. The screen fills in, and the
data-source card now shows a **Definitions** button that the Reader's did not.

---

### 3. Import the customer's own metrics — **you must do this**

On the definitions screen for the F&B data source, in **Import from a metric
table**:

| Field | Value |
|---|---|
| Metric table | `meta_metric` |
| Name column | `metric_key` |
| Definition column | `definition` |
| Formula column | *(leave blank)* |
| **Names column** | **`metric_name`** |

**The last row is the one that matters and the first version of this script left
it out** (B-087). A definition is matched to a question by **name and synonym**.
Imported without synonyms it answers only to `prep_quantity` — which nobody
types — so it binds nothing, every question sails past it, and the failure looks
exactly like the feature not working. `metric_name` is where that warehouse
keeps the words people actually use.

Press **Import**.

**You should see** *"18 proposals waiting for review."* and eighteen cards under
**Waiting for review**, each showing the metric's own sentence and the line
**"Imported from public.meta_metric"**.

**You should see nothing under "In force".** An import binds nothing.

Press **Import** again. **You should see** *"Nothing new: every metric in that
table is already known here."* — a request that succeeded and proposed nothing.

---

### 4. Accepting is where prose becomes a constraint (D-033) — **you must do this**

**Accept one card only: `prep_quantity`. Leave the other seventeen in the
queue.** Acceptance is per-card — each has its own "Also called" field, its own
filter editor and its own buttons — and accepting the rest is not wrong, it
just gives you seventeen definitions that bind nothing and one more thing to
read. If you have already accepted them all, that is this script's fault for not
saying so; see "Starting over" at the foot of this file.

Find the **`prep_quantity`** card.

First, read the sentence above the buttons **before touching anything**:

> *"Accepted as it stands, this informs the model and binds nothing — no query
> will be checked against it, and an answer resting on it will say so."*

and the button says **Accept as prose**.

Now, on that card, set **Also called** to:

```
units, units sold
```

*This is what makes step 6 work.* The definition has to answer to a word the
question contains, and the question asks about **units**. The field is prefilled
with whatever the import found, so with the Names column mapped you are adding
to real synonyms rather than supplying the only ones.

Then fill the filter editor on the same card:

| Field | Value |
|---|---|
| Table | `fact_sale` |
| Column | `row_role` |
| Must be | `not_in` |
| Values | `parent_zero_qty` |

Press **Add filter**.

**You should see** the filter appear as **"fact_sale.row_role none of
parent_zero_qty"**, the sentence change to *"…a query that ignores it is blocked
before the answer is written"*, and the button change to **Accept and enforce**.

*That change is the whole of D-033 on one screen: the button names the act it is
about to perform, before it performs it.*

Press **Accept and enforce** — *not* "Accept as prose". If the button still
reads "Accept as prose", the filter did not stage: check that Table, Column and
Values are all filled and press **Add filter** again.

**You should see** the card leave the queue and appear under **In force** with a
green **enforced** badge, its filter listed, and *"Also called: units, units
sold"*.

**Check before moving on.** Under **In force**, the badge must be green
**enforced** and not grey **prose only**. A grey badge means no filter was
attached, and steps 5–6 will then answer exactly as they did before — which is
what happened on the first walk of this script. Accept any other proposal without
adding a filter: it appears under **In force** with a grey **prose only** badge
and the line *"Nothing checks this one."*

**The failure case:** on another proposal, add a filter naming a column that does
not exist — `fact_sale` / `no_such_column` / `eq` / `x` — and press Accept.
**You should see** a message naming `no_such_column`, the proposal **still in the
queue**, and your typed filter **still on screen** to correct rather than retype.

---

### 5. The answer the warehouse used to get wrong — **the before**

Already run; re-run it if you want to see it yourself. Ask, against the F&B
source:

> Which item brings in the most sales revenue, and how many units of it did we sell?

**What it answered before any definition existed:**

> **"Ayam Penyet Set brings in the most sales revenue, with 0.00 units sold."**
> `limitations: []`

Correct SQL, business nonsense, and **no caveat**. That item's sale lines are
`parent_zero_qty` rows carrying money with no quantity. The customer's own view
`v_menu_performance` sums `qty` across every row role, so their own reporting
carries the same hole — and their own `meta_gate` table asks about it, in
English, as **G_ROWROLE**, marked `enforced = 0`.

---

### 6. The same question, with the definition binding — **the after**

Ask **the same question as step 5**, word for word:

> Which item brings in the most sales revenue, and how many units of it did we sell?

It reaches the definition now because you gave it the synonym **units** in
step 4. (The original script asked a differently-worded question here to force
the match; with the Names column and Also called filled, the same question works
both times, which is the comparison worth having.)

**You should see** an answer naming a **different item** with **real unit
numbers**. The equivalent run recorded during development answered:

> **"Ayam Penyet Combo brings in the most sales revenue, at 157,258.26. Its prep
> quantities by weekday are 21.48, 20.37, 18.42, 19.50, 18.29, 19.87 and 22.35
> units."**

Your wording will differ. What must not differ is that the item changes and the
units stop being `0.00`.

Open the run's trace and read the SQL. **You should see**
`WHERE "fs"."row_role" <> 'parent_zero_qty'` in it.

*An accepted definition changed the SQL a model generated, against a customer's
real warehouse. That is Phase 10's whole claim.*

---

### 7. A run the critic could not talk out of a bad draft — **owed, and yours to run**

This is the one step nothing here can assert into existence, and it is a real
gate criterion (**B-078**, **D-034**): a run where a required filter is
**dropped** by the model and **caught** by the critic. In the walk above the
model *complied* — it wrote the filter unprompted — which is the outcome the
feature exists for and is not this demonstration.

Three attempts were made and none produced it. What they produced instead:

1. the plain question — the model **complied**, writing
   `row_role <> 'parent_zero_qty'` into a CTE unprompted;
2. *"…across all sale lines, including the set lines? Include every row, do not
   filter any of them out"* — the model **refused, naming the rule**:
   > *"The authoritative prep_quantity definition requires excluding fact_sale
   > rows where row_role is parent_zero_qty. The request to include every row,
   > including those set/parent lines, cannot produce a compliant prep_quantity
   > result."*
3. and with no definition at all it answered **0.00 units**, uncaveated.

(2) is worth seeing even though it is not the criterion: the definition bound
firmly enough that the agent **declined a direct instruction to violate it and
said which rule stopped it**. That is binding demonstrated at planning time.
But it is compliance, and the criterion is explicit that compliance proves
nothing about the constraint.

Try your own phrasing. **You should see one of two things:**

* the answer ships **with the critic's objection as its first limitation**, in
  the critic's own words, and **cannot** describe itself as highly confident —
  the criterion is met; or
* the model complies or refuses again, in which case the run proves the
  machinery and not the disclosure, and **the criterion is not met** — leave
  the box unticked.

The deterministic half is proved without a model:
`uv run --directory apps/api pytest tests/agent/test_required_filters.py -q`
— 23 tests, including the block case and its false-block twin.

---

### 8. Nothing else broke

```sh
make lint typecheck
uv run --directory apps/api pytest -q          # 1405 passed, 20 skipped
cd apps/web && pnpm test && pnpm build
bash scripts/check_status.sh && bash scripts/check_backlog.sh
```

**You should see** the API suite green, the web suite at **107 passed**, a clean
build listing the new `/orgs/[orgId]/data-sources/[dataSourceId]/definitions`
route, and both guards intact.

---

### Starting over

An accepted definition **cannot currently be corrected** — there is no edit and
no un-accept, and re-accepting an active one is a 404 (**B-088**). If you accept
one with the wrong filter, or accept the whole queue by mistake, the way back is
to remove that row and import again: the import skips every name already known,
so exactly the removed metric comes back as a proposal.

```sh
ORG=ebfe8139-abbb-45ee-8e21-8ed3c3b50642
docker exec dataagent-platform-pg-1 psql -U dataagent -d dataagent -c   "DELETE FROM semantic_definitions WHERE org_id='$ORG' AND name='prep_quantity';"
```

Then press **Import** on the screen again — one card returns.

To clear everything and restart from step 3, drop the `AND name=…` clause.

---

### GATE

- [ ] **GATE (M10):** uploaded policy changes generated SQL; isolation test;
      sign-off.

**Left unticked deliberately.** Step 7 is owed, and steps 2–4 are rendered UI
that only you can confirm.
