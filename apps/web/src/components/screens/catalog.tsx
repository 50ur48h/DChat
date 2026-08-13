"use client";

/**
 * What we know about one database, and who may see it (plan WP4.3).
 *
 * Three things this screen refuses to blur together, because the product's
 * honesty depends on the distinction:
 *
 *   1. **Suspected is not decided.** The classifier's opinion and an Admin's
 *      decision are shown as separate facts. A column reading "masked" with
 *      "nobody has reviewed this" beside it is the truth; a single green tick
 *      would be a claim nobody made.
 *   2. **Masked is not absent.** A masked column is listed like any other, with
 *      its type and its role, because somebody asking "can I group by this"
 *      needs an answer even when they may not see the values.
 *   3. **A sample is a sample.** Counts say how many rows they came from, so
 *      "12% empty" is never mistaken for a statement about the whole table.
 *
 * The card is shown verbatim. It is the text the agent will be given, and a
 * person who wants to know why the agent answered oddly should be able to read
 * exactly what it read.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge, type Tone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { Row, Stack } from "@/components/ui/page";
import { createApi, type CardHit, type Catalog, type CatalogColumn } from "@/lib/api-client";
import { useSession } from "@/lib/auth/session";

import styles from "./catalog.module.css";

const POLICIES = ["allow", "mask", "deny"] as const;

const SENSITIVITY_TONES: Record<string, Tone> = {
  suspected: "peach",
  confirmed: "rose",
};

const POLICY_TONES: Record<string, Tone> = {
  allow: "mint",
  mask: "peach",
  deny: "rose",
};

function percentage(value: number | null): string | null {
  if (value === null) return null;
  return `${Math.round(value * 100)}% empty`;
}

function facts(column: CatalogColumn): string[] {
  const found: string[] = [];
  const empty = percentage(column.null_frac);
  if (empty && column.null_frac !== null && column.null_frac > 0) found.push(empty);
  if (column.distinct_est !== null) found.push(`${column.distinct_est} distinct`);
  if (column.min_val !== null && column.max_val !== null) {
    found.push(`${column.min_val} … ${column.max_val}`);
  }
  if (column.sample_rows !== null) found.push(`from ${column.sample_rows} sampled rows`);
  return found;
}

function ColumnRow({
  column,
  canDecide,
  busy,
  onDecide,
}: {
  column: CatalogColumn;
  canDecide: boolean;
  busy: boolean;
  onDecide: (columnId: string, policy: string) => void;
}) {
  return (
    <tr>
      <td>
        <div className={styles.columnName}>{column.name}</div>
        <div className={styles.columnFacts}>
          {column.data_type}
          {column.is_pk ? " · primary key" : ""}
          {column.nullable ? "" : " · required"}
        </div>
        {column.description && <div className={styles.columnFacts}>{column.description}</div>}
      </td>
      <td>
        <Row>
          {column.semantic_role && <Badge tone="sky">{column.semantic_role}</Badge>}
          {column.sensitivity !== "none" && (
            <Badge tone={SENSITIVITY_TONES[column.sensitivity] ?? "neutral"}>
              {column.sensitivity}
            </Badge>
          )}
        </Row>
      </td>
      <td className={styles.columnFacts}>{facts(column).join(" · ") || "not profiled"}</td>
      <td>
        <Row>
          <Badge tone={POLICY_TONES[column.policy] ?? "neutral"}>{column.policy}</Badge>
        </Row>
        {/* Whether a person decided is a different fact from what was decided,
            and the screen says both rather than implying review that never
            happened. */}
        <div className={styles.columnFacts}>
          {column.policy_decided ? "decided by an admin" : "nobody has reviewed this"}
        </div>
        {canDecide && (
          <div className={styles.policy}>
            <Select
              label="Change policy"
              options={POLICIES}
              value={column.policy}
              disabled={busy}
              onChange={(event) => onDecide(column.id, event.target.value)}
            />
          </div>
        )}
      </td>
    </tr>
  );
}

export function CatalogBrowser({
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

  const isAdmin = role === "admin";

  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<CardHit[] | null>(null);
  const [shown, setShown] = useState<string | null>(null);

  const load = useCallback(async () => {
    setCatalog(await api.catalog(orgId, dataSourceId));
  }, [api, orgId, dataSourceId]);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const next = await api.catalog(orgId, dataSourceId);
        if (active) setCatalog(next);
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "Could not load");
      }
    })();
    return () => {
      active = false;
    };
  }, [api, orgId, dataSourceId]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "That did not work");
    } finally {
      setBusy(false);
    }
  };

  const search = (event: React.FormEvent) => {
    event.preventDefault();
    void run(async () => {
      setHits(await api.searchCatalog(orgId, query, dataSourceId));
    });
  };

  const decide = (columnId: string, policy: string) =>
    void run(async () => {
      await api.setColumnPolicy(orgId, dataSourceId, columnId, { policy });
      await load();
    });

  return (
    <Stack>
      <Card
        title="Find a table"
        subtitle="Describe what you are looking for. Cards are searched, not just names."
      >
        <form onSubmit={search}>
          <div className={styles.search}>
            <Input
              label="Search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="revenue"
            />
            <Button variant="primary" type="submit" disabled={busy || !query.trim()}>
              Search
            </Button>
          </div>
        </form>

        {hits !== null && hits.length === 0 && (
          <p className={styles.muted}>Nothing matched. Try fewer words.</p>
        )}
        {hits !== null && hits.length > 0 && (
          <ul className={styles.hits}>
            {hits.map((hit) => (
              <li className={styles.hit} key={`${hit.schema_name}.${hit.table_name}`}>
                <div className={styles.hitName}>
                  {hit.schema_name}.{hit.table_name}
                </div>
                <div className={styles.hitCard}>{hit.card_text}</div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card
        title="Tables"
        subtitle={
          catalog
            ? `Catalog version ${catalog.snapshot.version}, ${catalog.snapshot.object_count} table(s).`
            : undefined
        }
      >
        {catalog === null && !error && <p className={styles.muted}>Loading…</p>}
        {catalog && (
          <ul className={styles.tables}>
            {catalog.tables.map((table) => {
              const name = `${table.schema_name}.${table.table_name}`;
              return (
                <li className={styles.table} key={name}>
                  <div className={styles.tableHead}>
                    <div>
                      <h3 className={styles.tableName}>{name}</h3>
                      {table.description && (
                        <p className={styles.tableNote}>{table.description}</p>
                      )}
                    </div>
                    <Row>
                      <Badge tone="lilac">{table.kind}</Badge>
                      {table.card_text && (
                        <Button onClick={() => setShown(shown === name ? null : name)}>
                          {shown === name ? "Hide card" : "Show card"}
                        </Button>
                      )}
                    </Row>
                  </div>

                  <table className={styles.columns}>
                    <thead>
                      <tr>
                        <th>Column</th>
                        <th>Role</th>
                        <th>Sample</th>
                        <th>Policy</th>
                      </tr>
                    </thead>
                    <tbody>
                      {table.columns.map((column) => (
                        <ColumnRow
                          key={column.id}
                          column={column}
                          canDecide={isAdmin}
                          busy={busy}
                          onDecide={decide}
                        />
                      ))}
                    </tbody>
                  </table>

                  {shown === name && table.card_text && (
                    <pre className={styles.card}>{table.card_text}</pre>
                  )}
                </li>
              );
            })}
          </ul>
        )}
        {error && <p className={styles.error}>{error}</p>}
        {!isAdmin && catalog && (
          <p className={styles.muted}>Only an Admin can change what may be seen.</p>
        )}
      </Card>
    </Stack>
  );
}
