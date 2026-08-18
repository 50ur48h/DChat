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
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { Row, Stack } from "@/components/ui/page";
import {
  createApi,
  type DefinitionProposal,
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
  const [proposals, setProposals] = useState<DefinitionProposal[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  //: Filters staged against one proposal, by proposal id. Kept here rather than
  //: inside the card so that a re-render after accepting cannot resurrect them.
  const [staged, setStaged] = useState<Record<string, RequiredFilter[]>>({});
  const [draft, setDraft] = useState<Record<string, RequiredFilter>>({});

  const [table, setTable] = useState("");
  const [nameColumn, setNameColumn] = useState("");
  const [descriptionColumn, setDescriptionColumn] = useState("");
  const [expressionColumn, setExpressionColumn] = useState("");

  const load = useCallback(async () => {
    const [active, waiting] = await Promise.all([
      api.definitions(orgId, dataSourceId),
      api.definitionProposals(orgId, dataSourceId),
    ]);
    setDefinitions(active);
    setProposals(waiting);
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
        const [current, waiting] = await Promise.all([
          api.definitions(orgId, dataSourceId),
          api.definitionProposals(orgId, dataSourceId),
        ]);
        if (active) {
          setDefinitions(current);
          setProposals(waiting);
        }
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "Could not load");
      }
    })();
    return () => {
      active = false;
    };
  }, [api, orgId, dataSourceId, isAdmin]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
      await load();
    } catch (cause) {
      // The API's own message, which names the column a filter got wrong. A
      // generic "that did not work" would send an Admin back to guess.
      setError(cause instanceof Error ? cause.message : "That did not work");
    } finally {
      setBusy(false);
    }
  };

  const importFromTable = async () => {
    await run(async () => {
      const created = await api.importDefinitions(orgId, dataSourceId, {
        table: table.trim(),
        name_column: nameColumn.trim(),
        description_column: descriptionColumn.trim(),
        ...(expressionColumn.trim() ? { expression_column: expressionColumn.trim() } : {}),
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
    await run(async () => {
      await api.acceptProposal(orgId, dataSourceId, proposal.id, filters);
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
    });
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
      {/* One error region for the whole screen. Every action here shares one
          `error`, so rendering it per card would put the same sentence on the
          page twice and read as two problems. */}
      {error ? <p className={styles.error}>{error}</p> : null}

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
          <Button
            onClick={() => void importFromTable()}
            disabled={busy || !table.trim() || !nameColumn.trim() || !descriptionColumn.trim()}
          >
            {busy ? "Working…" : "Import"}
          </Button>
        </div>
        {notice ? <p className={styles.notice}>{notice}</p> : null}
      </Card>

      <Card
        title="Waiting for review"
        subtitle="What the database says these metrics mean. None of it constrains anything yet."
      >
        {proposals === null ? (
          <p className={styles.empty}>Loading…</p>
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

                  <Row>
                    <Button onClick={() => void accept(proposal)} disabled={busy}>
                      {filters.length > 0 ? "Accept and enforce" : "Accept as prose"}
                    </Button>
                    <Button
                      onClick={() =>
                        void run(async () => {
                          await api.rejectProposal(orgId, dataSourceId, proposal.id);
                        })
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

      <Card
        title="In force"
        subtitle="What the agent is told these metrics mean, and which of them the critic enforces."
      >
        {definitions === null ? (
          <p className={styles.empty}>Loading…</p>
        ) : definitions.length === 0 ? (
          <p className={styles.empty}>No definitions yet.</p>
        ) : (
          <ul className={styles.list}>
            {definitions.map((definition) => (
              <li key={definition.id} className={styles.item}>
                <div className={styles.itemHead}>
                  <div>
                    <h3 className={styles.title}>{definition.name}</h3>
                    {definition.synonyms.length > 0 ? (
                      <p className={styles.meta}>Also called: {definition.synonyms.join(", ")}</p>
                    ) : null}
                  </div>
                  <Badge tone={definition.binds ? "mint" : "neutral"}>
                    {definition.binds ? "enforced" : "prose only"}
                  </Badge>
                </div>
                <p className={styles.description}>{definition.description}</p>
                {definition.expression ? (
                  <p className={styles.expression}>
                    <code>{definition.expression}</code>
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
              </li>
            ))}
          </ul>
        )}
      </Card>
    </Stack>
  );
}
