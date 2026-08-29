"use client";

/**
 * The semantic layer for one data source (plan WP10.2d, **B-059**, **D-033**).
 *
 * This is the screen where **prose becomes a constraint**, and everything about
 * it follows from that one sentence.
 *
 *   1. **An Admin sees the whole review, and nobody else sees any of it**
 *      (B-008). Every route behind this screen is Admin, because an accepted
 *      definition constrains generated SQL. An unknown role fails closed, which
 *      costs an Admin one page load — the cheaper mistake, since the other way
 *      round offers buttons that earn a 403 and teach people the product is
 *      broken. Whether a Reader should see the *list* is B-082, open.
 *   2. **A proposal shows where it came from.** The first question anybody
 *      reviewing an imported sentence has is *who wrote this*, and the answer is
 *      the customer's own table — named on the card, from the provenance the
 *      import recorded. A proposal whose origin cannot be shown is one nobody
 *      can responsibly accept.
 *   3. **The screen says what accepting will do, before it is done.** Accepted
 *      with filters, a definition **binds**: the critic blocks a query that
 *      ignores them. Accepted without, it **informs** and is checked by nothing,
 *      and an answer resting on it carries a limitation saying so. Those are
 *      different acts with different consequences and one button, so the button
 *      says which one it is about to perform. This is the disclosure D-033 asks
 *      for, at the moment a person can still change their mind.
 *   4. **`binds` comes from the API.** Not inferred here from an empty filter
 *      array — the distinction is too important to be re-derived by every screen
 *      that shows it, and re-derived means eventually derived wrongly.
 *   5. **A decision can be revised** (**B-088**). Accepting used to be final:
 *      no edit, no un-accept, and the only way to correct a filter was deleting
 *      the row in `psql`. The likeliest moment to get a filter wrong is the
 *      first time you write one, which is exactly when the product locked you
 *      out. Editing sends **only what changed**, because the API reads an absent
 *      field as *leave it alone* — resending a description nobody touched is how
 *      quietly loses a sentence. Retiring keeps what the
 *      definition said, so an answer checked against it last month is still
 *      explainable this month, and it asks twice because it changes what the
 *      platform enforces.
 *   6. **A refusal appears where the action was.** This screen is long — an
 *      import form, a review queue and every definition in force — and it used
 *      to put every error in one region at the top. A save refused from the
 *      editor at the bottom then looked like nothing happening at all: the API
 *      had named the column it could not find, and the sentence was a screen
 *      away. Found by the owner on the manual walk. Errors still share one
 *      state, so the same sentence is never on the page twice; what moved is
 *      *where* it renders.
 *   7. **What is out of force is still visible** (**B-094**). Retiring used to
 *      make a definition vanish from every view, and nothing could bring one
 *      back — so an Admin could neither see that anything was recoverable nor
 *      recover it. The owner hit that on their first real use, one verb after
 *      B-088. **Out of force** lists them and offers a single **Reinstate**;
 *      being listed puts nothing back into force, and the card says so.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { Row, Stack } from "@/components/ui/page";
import { SkeletonList } from "@/components/ui/skeleton";
import {
  createApi,
  type DefinitionProposal,
  type DefinitionVersion,
  type RequiredFilter,
  type SemanticDefinition,
} from "@/lib/api-client";
import { useSession } from "@/lib/auth/session";

import styles from "./definitions.module.css";

/**
 * The operators the critic can check, in the API's own order.
 *
 * Mirrored for the picker only. The set is closed on the API side because an
 * operator the critic cannot check is one the product would claim to enforce
 * and would not; offering a wider list here would just produce 400s.
 */
const OPERATORS = ["in", "not_in", "eq", "ne", "gt", "gte", "lt", "lte"] as const;

const READABLE: Record<string, string> = {
  in: "one of",
  not_in: "none of",
  eq: "equal to",
  ne: "not equal to",
  gt: "greater than",
  gte: "at least",
  lt: "less than",
  lte: "at most",
};

/** One filter as a person reads it, matching the words the prompt uses. */
export function describeFilter(filter: RequiredFilter): string {
  const readable = READABLE[filter.op] ?? filter.op;
  return `${filter.table}.${filter.column} ${readable} ${filter.values.join(", ")}`;
}

/**
 * Where an imported proposal came from, in one line.
 *
 * Returns null when there is nothing to say, so the card can leave the line out
 * rather than print "Imported from undefined" — a definition typed by hand has
 * no provenance and that is not a defect.
 */
export function describeProvenance(provenance: Record<string, unknown>): string | null {
  const table = provenance.table;
  if (typeof table !== "string" || table.length === 0) return null;
  return `Imported from ${table}`;
}

/**
 * What accepting this proposal, right now, would mean.
 *
 * The sentence changes with the staged filters because the two outcomes are
 * genuinely different, and the moment to say so is before the click.
 */
export function acceptanceSummary(filters: RequiredFilter[]): string {
  if (filters.length === 0) {
    return (
      "Accepted as it stands, this informs the model and binds nothing — " +
      "no query will be checked against it, and an answer resting on it will say so."
    );
  }
  return (
    `Accepted with ${filters.length === 1 ? "this filter" : `these ${filters.length} filters`}, ` +
    "a query that ignores it is blocked before the answer is written."
  );
}

const EMPTY_FILTER: RequiredFilter = { table: "", column: "", op: "in", values: [] };

/** What an Admin has typed into the editor for one definition in force. */
interface Draft {
  description: string;
  expression: string;
  caveat: string;
  synonyms: string;
  filters: RequiredFilter[];
}

/** What an Admin has typed into the *new* definition form (**B-169**). */
export interface Written {
  name: string;
  description: string;
  expression: string;
  caveat: string;
  synonyms: string;
  filters: RequiredFilter[];
}

export const EMPTY_WRITTEN: Written = {
  name: "",
  description: "",
  expression: "",
  caveat: "",
  synonyms: "",
  filters: [],
};

/**
 * The body a written definition sends, with empty optional fields omitted.
 *
 * **Omission rather than empty string**, for `create`'s own reason: the API
 * treats `expression: ""` as a formula that is the empty string, and `caveat:
 * ""` as a caveat nobody can read. Neither is what an untouched field means.
 */
export function bodyFrom(written: Written) {
  const expression = written.expression.trim();
  const caveat = written.caveat.trim();
  const synonyms = words(written.synonyms);
  return {
    name: written.name.trim(),
    description: written.description.trim(),
    ...(expression ? { expression } : {}),
    ...(caveat ? { caveat } : {}),
    ...(synonyms.length > 0 ? { synonyms } : {}),
    ...(written.filters.length > 0 ? { required_filters: written.filters } : {}),
  };
}

/** Whether the form has enough to send. Name and description are required. */
export function canWrite(written: Written): boolean {
  return written.name.trim().length > 0 && written.description.trim().length > 0;
}

function draftOf(definition: SemanticDefinition): Draft {
  return {
    description: definition.description,
    expression: definition.expression ?? "",
    caveat: definition.caveat ?? "",
    synonyms: definition.synonyms.join(", "),
    filters: definition.required_filters,
  };
}

function words(value: string): string[] {
  return value
    .split(",")
    .map((word) => word.trim())
    .filter((word) => word.length > 0);
}

function sameFilters(left: RequiredFilter[], right: RequiredFilter[]): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

/**
 * The fields this edit actually changes, and nothing else.
 *
 * **The omission is the contract, not an optimisation.** The API reads an absent
 * field as *leave it alone* and a present one as *replace it*, so sending the
 * whole form back would make every save a rewrite of fields nobody touched —
 * and `expression` is the one place where `null` and absent differ, since a
 * metric with no formula is a real thing to say.
 */
export function changesFrom(definition: SemanticDefinition, draft: Draft) {
  const changes: {
    description?: string;
    expression?: string | null;
    caveat?: string | null;
    synonyms?: string[];
    required_filters?: RequiredFilter[];
  } = {};
  if (draft.description.trim() !== definition.description) {
    changes.description = draft.description.trim();
  }
  const expression = draft.expression.trim();
  if (expression !== (definition.expression ?? "")) {
    changes.expression = expression === "" ? null : expression;
  }
  const caveat = draft.caveat.trim();
  if (caveat !== (definition.caveat ?? "")) {
    changes.caveat = caveat === "" ? null : caveat;
  }
  const synonyms = words(draft.synonyms);
  if (synonyms.join("\u0000") !== definition.synonyms.join("\u0000")) {
    changes.synonyms = synonyms;
  }
  if (!sameFilters(draft.filters, definition.required_filters)) {
    changes.required_filters = draft.filters;
  }
  return changes;
}

/**
 * What saving this edit will do, said before the click, as accepting does.
 *
 * **Written in the conditional**, and that is not fussiness: the first draft
 * opened with *"Saved, …"*, which reads as a confirmation, and on the manual
 * walk it sat above an editor whose save had just been refused. A line that
 * describes a consequence must not be mistakable for a receipt.
 */
export function editSummary(definition: SemanticDefinition, draft: Draft): string {
  const changes = changesFrom(definition, draft);
  if (Object.keys(changes).length === 0) return "Nothing has changed yet.";
  if (changes.required_filters === undefined) {
    return "Saving this changes what the model is told and leaves what is enforced alone.";
  }
  if (draft.filters.length === 0) {
    return (
      "Saving this stops enforcing anything: the definition stays as guidance, " +
      "and no query will be checked against it."
    );
  }
  return (
    `Once saved, a query that ignores ${
      draft.filters.length === 1 ? "this filter" : "these filters"
    } is blocked before the answer is written.`
  );
}

/** One version of a definition, as a line of history. */
export function describeVersion(version: DefinitionVersion): string {
  const what: Record<string, string> = {
    created: "written by hand",
    accepted: "accepted from a proposal",
    updated: "edited",
    retired: "taken out of force",
    reinstated: "brought back into force",
  };
  const filters =
    version.required_filters.length === 0
      ? "enforced nothing"
      : `enforced ${version.required_filters.map(describeFilter).join("; ")}`;
  return `v${version.version} — ${what[version.change] ?? version.change}, ${filters}`;
}

export function Definitions({
  orgId,
  dataSourceId,
  role,
}: {
  orgId: string;
  dataSourceId: string;
  role: string | null;
}) {
  const session = useSession();
  const api = useMemo(() => createApi(session.getToken), [session.getToken]);

  // Every route behind this screen is Admin. Unknown role fails closed.
  const isAdmin = role === "admin";

  const [definitions, setDefinitions] = useState<SemanticDefinition[] | null>(null);
  //: What has been taken out of force. Loaded with the rest rather than behind
  //: a click, because the defect B-094 records is that nobody could *see* there
  //: was anything to bring back (the card hides itself when there is nothing).
  const [retired, setRetired] = useState<SemanticDefinition[]>([]);
  const [proposals, setProposals] = useState<DefinitionProposal[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  //: Which card the current error belongs to, or null for one that belongs to
  //: the screen. The message renders there rather than at the top, because an
  //: editor at the bottom of a long page is where the person is looking.
  const [errorAt, setErrorAt] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  //: Where the notice belongs, for the reason `errorAt` exists: the screen had
  //: one render site for every success message, so accepting a proposal at the
  //: bottom of the page reported it under the import form at the top. Taken
  //: from whatever anchor `run` was given, so the existing callers — which
  //: pass none — keep landing exactly where they did.
  const [noticeAt, setNoticeAt] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  //: Filters staged against one proposal, by proposal id. Kept here rather than
  //: inside the card so that a re-render after accepting cannot resurrect them.
  const [staged, setStaged] = useState<Record<string, RequiredFilter[]>>({});
  //: What an Admin says people actually call this metric, by proposal id.
  //: An imported definition answers only to its key and to the label its own
  //: table carried, and nobody asks a question in those words (B-085).
  const [alsoCalled, setAlsoCalled] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<Record<string, RequiredFilter>>({});

  //: Which definition in force is being edited, and what has been typed into it
  //: (B-088). One at a time: two open editors is two ways to lose an edit, and
  //: the thing being changed is what the platform enforces on generated SQL.
  const [editing, setEditing] = useState<string | null>(null);
  const [edit, setEdit] = useState<Draft | null>(null);
  const [editFilter, setEditFilter] = useState<RequiredFilter>(EMPTY_FILTER);
  //: Retiring takes a metric out of force, so it is asked twice. The second
  //: click is the confirmation; anything else cancels it.
  const [retiring, setRetiring] = useState<string | null>(null);
  //: Loaded on demand, because most visits to this screen are not audits.
  const [history, setHistory] = useState<Record<string, DefinitionVersion[]>>({});

  const [table, setTable] = useState("");
  const [nameColumn, setNameColumn] = useState("");
  const [descriptionColumn, setDescriptionColumn] = useState("");
  const [expressionColumn, setExpressionColumn] = useState("");
  //: The column holding what people actually call each metric. Optional, and
  //: the most valuable optional field on this form: a definition is matched to
  //: a question by name and synonym, so an import that carries no synonyms
  //: produces metrics no question can reach (B-085, B-087).
  const [synonymsColumn, setSynonymsColumn] = useState("");
  //: The column saying what an answer using each metric has to disclose. The
  //: only imported field that reaches the reader rather than the prompt.
  const [caveatColumn, setCaveatColumn] = useState("");

  //: Writing one by hand (**B-169**). `createDefinition` existed on the client
  //: from the beginning and no screen ever called it, so the only ways to get a
  //: definition into a source were to import a table that already held one or
  //: to spend a bearer token from a script. Somebody trying the product on
  //: their own data has neither.
  const [written, setWritten] = useState<Written>(EMPTY_WRITTEN);
  const [writtenFilter, setWrittenFilter] = useState<RequiredFilter>(EMPTY_FILTER);

  const load = useCallback(async () => {
    const [active, waiting, outOfForce] = await Promise.all([
      api.definitions(orgId, dataSourceId),
      api.definitionProposals(orgId, dataSourceId),
      api.definitions(orgId, dataSourceId, "retired"),
    ]);
    setDefinitions(active);
    setProposals(waiting);
    setRetired(outOfForce);
  }, [api, orgId, dataSourceId]);

  useEffect(() => {
    // Nothing is fetched for a role the API would refuse. A 403 in the console
    // for a screen that never offered the action is noise, and the audit row it
    // writes is a denial nobody attempted. Nothing is *set* either — the
    // non-admin render never reads these lists.
    if (!isAdmin) return;
    // Guarded so a slow response cannot write into an unmounted screen.
    let active = true;
    void (async () => {
      try {
        const [current, waiting, outOfForce] = await Promise.all([
          api.definitions(orgId, dataSourceId),
          api.definitionProposals(orgId, dataSourceId),
          api.definitions(orgId, dataSourceId, "retired"),
        ]);
        if (active) {
          setDefinitions(current);
          setProposals(waiting);
          setRetired(outOfForce);
        }
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "Could not load");
      }
    })();
    return () => {
      active = false;
    };
  }, [api, orgId, dataSourceId, isAdmin]);

  const run = async (action: () => Promise<void>, anchor: string | null = null) => {
    setBusy(true);
    setError(null);
    setErrorAt(null);
    setNotice(null);
    setNoticeAt(anchor);
    try {
      await action();
      await load();
    } catch (cause) {
      // The API's own message, which names the column a filter got wrong. A
      // generic "that did not work" would send an Admin back to guess.
      setError(cause instanceof Error ? cause.message : "That did not work");
      setErrorAt(anchor);
    } finally {
      setBusy(false);
    }
  };

  const writeOne = async () => {
    await run(async () => {
      const created = await api.createDefinition(orgId, dataSourceId, bodyFrom(written));
      setWritten(EMPTY_WRITTEN);
      setWrittenFilter(EMPTY_FILTER);
      // Says which of the two it is, because they are different objects: one
      // constrains generated SQL and the other is guidance the critic ignores
      // (D-033). An Admin who meant to write a rule should find out now.
      setNotice(
        created.binds
          ? `${created.name} is in force: a query that ignores its filters is blocked.`
          : `${created.name} is in force as prose. Nothing checks it — add a filter to make it bind.`,
      );
    }, "written");
  };

  const importFromTable = async () => {
    await run(async () => {
      const created = await api.importDefinitions(orgId, dataSourceId, {
        table: table.trim(),
        name_column: nameColumn.trim(),
        description_column: descriptionColumn.trim(),
        ...(expressionColumn.trim() ? { expression_column: expressionColumn.trim() } : {}),
        ...(synonymsColumn.trim() ? { synonyms_column: synonymsColumn.trim() } : {}),
        ...(caveatColumn.trim() ? { caveat_column: caveatColumn.trim() } : {}),
      });
      // An import that proposed nothing succeeded — every name was already
      // known. Saying so is the difference between "done" and "did my mapping
      // fail?", and an empty list alone answers neither.
      setNotice(
        created.length === 0
          ? "Nothing new: every metric in that table is already known here."
          : `${created.length} proposal${created.length === 1 ? "" : "s"} waiting for review.`,
      );
    });
  };

  const stageFilter = (proposalId: string) => {
    const pending = draft[proposalId] ?? EMPTY_FILTER;
    if (!pending.table.trim() || !pending.column.trim() || pending.values.length === 0) return;
    setStaged((current) => ({
      ...current,
      [proposalId]: [...(current[proposalId] ?? []), pending],
    }));
    setDraft((current) => ({ ...current, [proposalId]: EMPTY_FILTER }));
  };

  const editDraft = (proposalId: string, patch: Partial<RequiredFilter>) => {
    setDraft((current) => ({
      ...current,
      [proposalId]: { ...(current[proposalId] ?? EMPTY_FILTER), ...patch },
    }));
  };

  const accept = async (proposal: DefinitionProposal) => {
    const filters = staged[proposal.id] ?? [];
    const words = (alsoCalled[proposal.id] ?? "")
      .split(",")
      .map((word) => word.trim())
      .filter((word) => word.length > 0);
    await run(async () => {
      await api.acceptProposal(orgId, dataSourceId, proposal.id, filters, words);
      // Cleared inside the action, so it only happens on success: a rejected
      // filter leaves the Admin's work on screen to correct rather than retype.
      setStaged((current) => {
        const next = { ...current };
        delete next[proposal.id];
        return next;
      });
      setNotice(
        filters.length > 0
          ? `${proposal.name} now binds: a query that ignores it is blocked.`
          : `${proposal.name} is in force as prose. Nothing checks it.`,
      );
    }, proposal.id);
  };

  const startEditing = (definition: SemanticDefinition) => {
    setEditing(definition.id);
    setEdit(draftOf(definition));
    setEditFilter(EMPTY_FILTER);
    setRetiring(null);
  };

  const stopEditing = () => {
    setEditing(null);
    setEdit(null);
    setEditFilter(EMPTY_FILTER);
  };

  const save = async (definition: SemanticDefinition) => {
    if (edit === null) return;
    const changes = changesFrom(definition, edit);
    if (Object.keys(changes).length === 0) {
      stopEditing();
      return;
    }
    await run(async () => {
      const saved = await api.updateDefinition(orgId, dataSourceId, definition.id, changes);
      // Closed inside the action, so it only happens on success: a filter the
      // catalog refused leaves the Admin's work on screen to correct rather
      // than retype, which is the whole reason this screen exists.
      stopEditing();
      // Reloaded by `run`, so a stale history would be the one thing on screen
      // that had not caught up.
      setHistory((current) => {
        const next = { ...current };
        delete next[definition.id];
        return next;
      });
      setNotice(
        saved.binds
          ? `${saved.name} now binds at version ${saved.version}: a query that ignores it is blocked.`
          : `${saved.name} is in force as prose at version ${saved.version}. Nothing checks it.`,
      );
    }, definition.id);
  };

  const retire = async (definition: SemanticDefinition) => {
    await run(async () => {
      await api.retireDefinition(orgId, dataSourceId, definition.id);
      setRetiring(null);
      if (editing === definition.id) stopEditing();
      setNotice(
        `${definition.name} is out of force. What it said is kept, so an answer ` +
          "checked against it is still explainable.",
      );
    }, definition.id);
  };

  const reinstate = async (definition: SemanticDefinition) => {
    await run(async () => {
      const back = await api.reinstateDefinition(orgId, dataSourceId, definition.id);
      setNotice(
        back.binds
          ? `${back.name} is back in force at version ${back.version}, binding what it bound.`
          : `${back.name} is back in force at version ${back.version}, as prose. Nothing checks it.`,
      );
    }, definition.id);
  };

  const showHistory = async (definition: SemanticDefinition) => {
    if (history[definition.id]) {
      setHistory((current) => {
        const next = { ...current };
        delete next[definition.id];
        return next;
      });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const versions = await api.definitionVersions(orgId, dataSourceId, definition.id);
      setHistory((current) => ({ ...current, [definition.id]: versions }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not read the history");
    } finally {
      setBusy(false);
    }
  };

  if (!isAdmin) {
    return (
      <Card title="Definitions">
        <p className={styles.empty}>
          Only an Admin can review what a metric means here, because an accepted
          definition constrains the queries this platform will run.
        </p>
      </Card>
    );
  }

  return (
    <Stack>
      {/* One error *state* for the whole screen, so the same sentence is never
          on the page twice — but it renders beside the action that earned it.
          A save refused from the editor at the bottom of this page used to put
          its message up here, where nobody editing was looking. */}
      {error && errorAt === null ? <p className={styles.error}>{error}</p> : null}

      <Card
        title="Import from a metric table"
        subtitle="Most warehouses already carry their definitions. Nothing imported takes effect until you accept it."
      >
        <div className={styles.importForm}>
          {/* "Metric table", not "Table": the filter editor below has a Table
              field of its own, and two controls with one name is how somebody
              types a column into the wrong box. */}
          <Input
            label="Metric table"
            value={table}
            placeholder="meta_metric"
            onChange={(event) => setTable(event.target.value)}
          />
          <Input
            label="Name column"
            value={nameColumn}
            placeholder="metric_key"
            onChange={(event) => setNameColumn(event.target.value)}
          />
          <Input
            label="Definition column"
            value={descriptionColumn}
            placeholder="definition_text"
            onChange={(event) => setDescriptionColumn(event.target.value)}
          />
          <Input
            label="Formula column (optional)"
            value={expressionColumn}
            placeholder="calculation"
            onChange={(event) => setExpressionColumn(event.target.value)}
          />
          {/* Worth more than it looks. A definition is found by name and synonym,
              so a metric imported without the words people use is one no question
              reaches — it binds nothing however carefully its filters are
              written (B-085). Most metric tables already have this column. */}
          <Input
            label="Names column (optional)"
            value={synonymsColumn}
            placeholder="metric_name — what people call it"
            onChange={(event) => setSynonymsColumn(event.target.value)}
          />
          {/* The customer's own limits on what each metric may be used to claim.
              Where they exist they are usually the most carefully written column
              in the table, and until now there was nowhere to put them. */}
          <Input
            label="Caveat column (optional)"
            value={caveatColumn}
            placeholder="what an answer must disclose"
            onChange={(event) => setCaveatColumn(event.target.value)}
          />
          <Button
            onClick={() => void importFromTable()}
            disabled={busy || !table.trim() || !nameColumn.trim() || !descriptionColumn.trim()}
          >
            {busy ? "Working…" : "Import"}
          </Button>
        </div>
        {notice && noticeAt === null ? <p className={styles.notice}>{notice}</p> : null}
      </Card>

      {/* **B-169.** `createDefinition` was on the API client from the start and
          no screen called it, so a source whose warehouse has no metric table —
          which is most of them, and every one somebody is trying the product on
          for the first time — could not be given a definition at all without a
          bearer token and a script. */}
      <Card
        title="Write one by hand"
        subtitle="For a database that does not already carry its definitions. Unlike an import, this takes effect as soon as you save it."
      >
        <div className={styles.importForm}>
          <Input
            label="Name"
            value={written.name}
            placeholder="net_revenue"
            onChange={(event) => setWritten({ ...written, name: event.target.value })}
          />
          <Input
            label="What it means"
            value={written.description}
            placeholder="Sales after discounts, excluding cancelled orders."
            onChange={(event) => setWritten({ ...written, description: event.target.value })}
          />
          <Input
            label="Formula (optional)"
            value={written.expression}
            placeholder="sum(orders.total_amount)"
            onChange={(event) => setWritten({ ...written, expression: event.target.value })}
          />
          <Input
            label="Answers must say (optional)"
            value={written.caveat}
            placeholder="what an answer using this metric has to disclose"
            onChange={(event) => setWritten({ ...written, caveat: event.target.value })}
          />
          {/* The same warning the import form carries, and it matters more here:
              an imported definition at least answers to the label its own table
              gave it, while a hand-written one answers to nothing but what is
              typed in this box (B-085). */}
          <Input
            label="Also called"
            value={written.synonyms}
            placeholder="the words people use when they ask"
            onChange={(event) => setWritten({ ...written, synonyms: event.target.value })}
          />
        </div>

        {written.filters.length > 0 ? (
          <ul className={styles.filters}>
            {written.filters.map((filter, index) => (
              <li key={`${filter.table}.${filter.column}.${index}`}>
                {describeFilter(filter)}{" "}
                <Button
                  variant="ghost"
                  onClick={() =>
                    setWritten({
                      ...written,
                      filters: written.filters.filter((_, at) => at !== index),
                    })
                  }
                  disabled={busy}
                >
                  Remove
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className={styles.prose}>
            With no filter this is prose: it reaches the model as guidance and the critic checks
            nothing. Add one to make it bind.
          </p>
        )}

        <div className={styles.filterForm}>
          {/* Prefixed, for the reason the import form gives above: this card
              puts a second filter editor on a screen that already has one, and
              two controls with one name is how a column ends up in the wrong
              box. */}
          <Input
            label="Filter table"
            value={writtenFilter.table}
            onChange={(event) => setWrittenFilter({ ...writtenFilter, table: event.target.value })}
          />
          <Input
            label="Filter column"
            value={writtenFilter.column}
            onChange={(event) => setWrittenFilter({ ...writtenFilter, column: event.target.value })}
          />
          <Select
            label="Filter must be"
            options={OPERATORS}
            value={writtenFilter.op}
            onChange={(event) => setWrittenFilter({ ...writtenFilter, op: event.target.value })}
          />
          <Input
            label="Filter values"
            value={writtenFilter.values.join(", ")}
            placeholder="completed"
            onChange={(event) =>
              setWrittenFilter({ ...writtenFilter, values: words(event.target.value) })
            }
          />
          <Button
            onClick={() => {
              if (
                !writtenFilter.table.trim() ||
                !writtenFilter.column.trim() ||
                writtenFilter.values.length === 0
              ) {
                return;
              }
              setWritten({ ...written, filters: [...written.filters, writtenFilter] });
              setWrittenFilter(EMPTY_FILTER);
            }}
            disabled={busy}
          >
            Add this filter
          </Button>
        </div>

        <Row>
          <Button onClick={() => void writeOne()} disabled={busy || !canWrite(written)}>
            {busy ? "Working…" : "Create definition"}
          </Button>
        </Row>
        {/* Beside the action that earned it, like every other message here. */}
        {notice && noticeAt === "written" ? <p className={styles.notice}>{notice}</p> : null}
        {error && errorAt === "written" ? <p className={styles.error}>{error}</p> : null}
      </Card>

      <Card
        title="Waiting for review"
        subtitle="What the database says these metrics mean. None of it constrains anything yet."
      >
        {proposals === null ? (
          <SkeletonList rows={3} label="Loading definitions" />
        ) : proposals.length === 0 ? (
          <p className={styles.empty}>Nothing waiting. Import a metric table to propose some.</p>
        ) : (
          <ul className={styles.list}>
            {proposals.map((proposal) => {
              const filters = staged[proposal.id] ?? [];
              const pending = draft[proposal.id] ?? EMPTY_FILTER;
              return (
                <li key={proposal.id} className={styles.item}>
                  <div className={styles.itemHead}>
                    <div>
                      <h3 className={styles.title}>{proposal.name}</h3>
                      <p className={styles.meta}>{describeProvenance(proposal.provenance)}</p>
                    </div>
                    <Badge tone="peach">proposed</Badge>
                  </div>

                  <p className={styles.description}>{proposal.description}</p>
                  {proposal.expression ? (
                    <p className={styles.expression}>
                      <code>{proposal.expression}</code>
                    </p>
                  ) : null}
                  {proposal.caveat ? (
                    <p className={styles.caveat}>
                      <strong>Answers must say:</strong> {proposal.caveat}
                    </p>
                  ) : null}
                  {proposal.synonyms.length > 0 ? (
                    <p className={styles.meta}>Also called: {proposal.synonyms.join(", ")}</p>
                  ) : null}

                  {filters.length > 0 ? (
                    <ul className={styles.filters}>
                      {filters.map((filter, index) => (
                        <li key={`${filter.table}.${filter.column}.${index}`}>
                          {describeFilter(filter)}
                        </li>
                      ))}
                    </ul>
                  ) : null}

                  <div className={styles.filterForm}>
                    <Input
                      label="Also called"
                      value={alsoCalled[proposal.id] ?? proposal.synonyms.join(", ")}
                      placeholder="the words people use when they ask"
                      onChange={(event) =>
                        setAlsoCalled((current) => ({
                          ...current,
                          [proposal.id]: event.target.value,
                        }))
                      }
                    />
                  </div>

                  <div className={styles.filterForm}>
                    <Input
                      label="Table"
                      value={pending.table}
                      onChange={(event) => editDraft(proposal.id, { table: event.target.value })}
                    />
                    <Input
                      label="Column"
                      value={pending.column}
                      onChange={(event) => editDraft(proposal.id, { column: event.target.value })}
                    />
                    <Select
                      label="Must be"
                      options={OPERATORS}
                      value={pending.op}
                      onChange={(event) => editDraft(proposal.id, { op: event.target.value })}
                    />
                    <Input
                      label="Values"
                      value={pending.values.join(", ")}
                      placeholder="completed"
                      onChange={(event) =>
                        editDraft(proposal.id, {
                          values: event.target.value
                            .split(",")
                            .map((word) => word.trim())
                            .filter((word) => word.length > 0),
                        })
                      }
                    />
                    <Button onClick={() => stageFilter(proposal.id)} disabled={busy}>
                      Add filter
                    </Button>
                  </div>

                  <p className={filters.length > 0 ? styles.binds : styles.prose}>
                    {acceptanceSummary(filters)}
                  </p>

                  {errorAt === proposal.id && error ? (
                    <p className={styles.error}>{error}</p>
                  ) : null}

                  <Row>
                    <Button onClick={() => void accept(proposal)} disabled={busy}>
                      {filters.length > 0 ? "Accept and enforce" : "Accept as prose"}
                    </Button>
                    <Button
                      onClick={() =>
                        void run(async () => {
                          await api.rejectProposal(orgId, dataSourceId, proposal.id);
                        }, proposal.id)
                      }
                      disabled={busy}
                    >
                      Reject
                    </Button>
                  </Row>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      {/* Hidden when there is nothing out of force, so an ordinary organization
          never sees it — but present the moment there is something to bring
          back, which is the half of B-094 that made a mis-click permanent. */}
      {retired.length > 0 ? (
        <Card
          title="Out of force"
          subtitle="Retired, and kept. None of this constrains anything or reaches the model; an answer checked against one is still explainable."
        >
          <ul className={styles.list}>
            {retired.map((definition) => (
              <li key={definition.id} className={styles.item}>
                <div className={styles.itemHead}>
                  <div>
                    <h3 className={styles.title}>{definition.name}</h3>
                    {definition.synonyms.length > 0 ? (
                      <p className={styles.meta}>Also called: {definition.synonyms.join(", ")}</p>
                    ) : null}
                  </div>
                  <Row>
                    <Badge tone="neutral">v{definition.version}</Badge>
                    <Badge tone="peach">retired</Badge>
                  </Row>
                </div>
                <p className={styles.description}>{definition.description}</p>
                {definition.required_filters.length > 0 ? (
                  <ul className={styles.filters}>
                    {definition.required_filters.map((filter, index) => (
                      <li key={`${filter.table}.${filter.column}.${index}`}>
                        {describeFilter(filter)}
                      </li>
                    ))}
                  </ul>
                ) : null}
                <p className={styles.prose}>
                  {definition.required_filters.length > 0
                    ? "Reinstated, it binds again: a query that ignores its filters is blocked."
                    : "Reinstated, it reaches the model as guidance. Nothing checks it."}
                </p>

                {errorAt === definition.id && error ? (
                  <p className={styles.error}>{error}</p>
                ) : null}

                <Row>
                  <Button onClick={() => void reinstate(definition)} disabled={busy}>
                    {busy ? "Working…" : "Reinstate"}
                  </Button>
                  <Button
                    variant="ghost"
                    onClick={() => void showHistory(definition)}
                    disabled={busy}
                  >
                    {history[definition.id] ? "Hide history" : "History"}
                  </Button>
                </Row>

                {history[definition.id] ? (
                  <ul className={styles.filters}>
                    {history[definition.id]!.length === 0 ? (
                      <li>Nothing recorded.</li>
                    ) : (
                      history[definition.id]!.map((version) => (
                        <li key={version.version}>{describeVersion(version)}</li>
                      ))
                    )}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      <Card
        title="In force"
        subtitle="What the agent is told these metrics mean, and which of them the critic enforces."
      >
        {definitions === null ? (
          <SkeletonList rows={3} label="Loading definitions" />
        ) : definitions.length === 0 ? (
          <p className={styles.empty}>No definitions yet.</p>
        ) : (
          <ul className={styles.list}>
            {definitions.map((definition) => {
              const open = editing === definition.id && edit !== null;
              const versions = history[definition.id];
              return (
                <li key={definition.id} className={styles.item}>
                  <div className={styles.itemHead}>
                    <div>
                      <h3 className={styles.title}>{definition.name}</h3>
                      {definition.synonyms.length > 0 ? (
                        <p className={styles.meta}>Also called: {definition.synonyms.join(", ")}</p>
                      ) : null}
                    </div>
                    <Row>
                      {/* The version is next to the badge because they answer
                          one question together: what is in force, and which
                          version of it. */}
                      <Badge tone="neutral">v{definition.version}</Badge>
                      <Badge tone={definition.binds ? "mint" : "neutral"}>
                        {definition.binds ? "enforced" : "prose only"}
                      </Badge>
                    </Row>
                  </div>

                  {open && edit !== null ? (
                    <>
                      <div className={styles.importForm}>
                        <Input
                          label="What it means"
                          value={edit.description}
                          onChange={(event) =>
                            setEdit({ ...edit, description: event.target.value })
                          }
                        />
                        <Input
                          label="Formula (optional)"
                          value={edit.expression}
                          placeholder="sum(orders.total_amount)"
                          onChange={(event) => setEdit({ ...edit, expression: event.target.value })}
                        />
                        {/* Not a formula and not a synonym: the sentence every
                            answer using this metric carries. Empty clears it. */}
                        <Input
                          label="Answers must say (optional)"
                          value={edit.caveat}
                          placeholder="what an answer using this metric has to disclose"
                          onChange={(event) => setEdit({ ...edit, caveat: event.target.value })}
                        />
                        <Input
                          label="Also called"
                          value={edit.synonyms}
                          placeholder="the words people use when they ask"
                          onChange={(event) => setEdit({ ...edit, synonyms: event.target.value })}
                        />
                      </div>

                      {edit.filters.length > 0 ? (
                        <ul className={styles.filters}>
                          {edit.filters.map((filter, index) => (
                            <li key={`${filter.table}.${filter.column}.${index}`}>
                              {describeFilter(filter)}{" "}
                              <Button
                                variant="ghost"
                                onClick={() =>
                                  setEdit({
                                    ...edit,
                                    filters: edit.filters.filter((_, at) => at !== index),
                                  })
                                }
                                disabled={busy}
                              >
                                Remove
                              </Button>
                            </li>
                          ))}
                        </ul>
                      ) : null}

                      <div className={styles.filterForm}>
                        <Input
                          label="Table"
                          value={editFilter.table}
                          onChange={(event) =>
                            setEditFilter({ ...editFilter, table: event.target.value })
                          }
                        />
                        <Input
                          label="Column"
                          value={editFilter.column}
                          onChange={(event) =>
                            setEditFilter({ ...editFilter, column: event.target.value })
                          }
                        />
                        <Select
                          label="Must be"
                          options={OPERATORS}
                          value={editFilter.op}
                          onChange={(event) =>
                            setEditFilter({ ...editFilter, op: event.target.value })
                          }
                        />
                        <Input
                          label="Values"
                          value={editFilter.values.join(", ")}
                          placeholder="completed"
                          onChange={(event) =>
                            setEditFilter({ ...editFilter, values: words(event.target.value) })
                          }
                        />
                        <Button
                          onClick={() => {
                            if (
                              !editFilter.table.trim() ||
                              !editFilter.column.trim() ||
                              editFilter.values.length === 0
                            ) {
                              return;
                            }
                            setEdit({ ...edit, filters: [...edit.filters, editFilter] });
                            setEditFilter(EMPTY_FILTER);
                          }}
                          disabled={busy}
                        >
                          Add filter
                        </Button>
                      </div>

                      <p className={edit.filters.length > 0 ? styles.binds : styles.prose}>
                        {editSummary(definition, edit)}
                      </p>

                      {/* Beside the button that earned it. The 400 names the
                          column the catalog does not have, and it is written to
                          be repaired from — which needs it to be read. */}
                      {errorAt === definition.id && error ? (
                        <p className={styles.error}>{error}</p>
                      ) : null}

                      <Row>
                        <Button onClick={() => void save(definition)} disabled={busy}>
                          {busy ? "Working…" : "Save changes"}
                        </Button>
                        <Button variant="ghost" onClick={stopEditing} disabled={busy}>
                          Cancel
                        </Button>
                      </Row>
                    </>
                  ) : (
                    <>
                      <p className={styles.description}>{definition.description}</p>
                      {definition.expression ? (
                        <p className={styles.expression}>
                          <code>{definition.expression}</code>
                        </p>
                      ) : null}
                      {/* Above the filters on purpose. The filters say what the
                          platform enforces; this says what the answer has to
                          admit, and it is the half a reader of the answer sees. */}
                      {definition.caveat ? (
                        <p className={styles.caveat}>
                          <strong>Answers must say:</strong> {definition.caveat}
                        </p>
                      ) : null}
                      {definition.required_filters.length > 0 ? (
                        <ul className={styles.filters}>
                          {definition.required_filters.map((filter, index) => (
                            <li key={`${filter.table}.${filter.column}.${index}`}>
                              {describeFilter(filter)}
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className={styles.prose}>
                          Nothing checks this one. It reaches the model as guidance, and an answer
                          resting on it says its definition was not verified.
                        </p>
                      )}

                      <Row>
                        <Button onClick={() => startEditing(definition)} disabled={busy}>
                          Edit
                        </Button>
                        <Button
                          variant="ghost"
                          onClick={() => void showHistory(definition)}
                          disabled={busy}
                        >
                          {versions ? "Hide history" : "History"}
                        </Button>
                        {/* Asked twice, because it changes what the platform
                            enforces on every query that follows. */}
                        {retiring === definition.id ? (
                          <>
                            <Button
                              variant="danger"
                              onClick={() => void retire(definition)}
                              disabled={busy}
                            >
                              Confirm: take out of force
                            </Button>
                            <Button
                              variant="ghost"
                              onClick={() => setRetiring(null)}
                              disabled={busy}
                            >
                              Keep it
                            </Button>
                          </>
                        ) : (
                          <Button onClick={() => setRetiring(definition.id)} disabled={busy}>
                            Retire
                          </Button>
                        )}
                      </Row>
                    </>
                  )}

                  {errorAt === definition.id && error && !open ? (
                    <p className={styles.error}>{error}</p>
                  ) : null}

                  {versions ? (
                    <ul className={styles.filters}>
                      {versions.length === 0 ? (
                        <li>
                          Nothing recorded. This definition was written before the platform kept a
                          history, so what it said before its next edit is not knowable.
                        </li>
                      ) : (
                        versions.map((version) => (
                          <li key={version.version}>{describeVersion(version)}</li>
                        ))
                      )}
                    </ul>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </Stack>
  );
}
