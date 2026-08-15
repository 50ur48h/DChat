"use client";

/**
 * The query behind an answer, as a person may check it (plan WP7.3, B-034).
 *
 * This is the screen that makes the product's central claim checkable. An answer
 * says "128 orders"; a finding points at the execution that produced it; this
 * opens that execution and shows the statement, the rows it returned and how
 * long it took. Without it a citation is a reference to evidence nobody can
 * read, which looks like proof while being none.
 *
 * Three things it will not blur together, each for a specific reason.
 *
 * **A refusal is not an empty result.** A statement the DAL declined to send
 * reached no engine, so it has no rows — and rendering that as an empty table
 * would read as "your data has no answer" when what happened is "this service
 * would not run that". A refused execution shows its violation code and the
 * statement that earned it instead.
 *
 * **A preview is not the result.** `row_count` is what the query returned and
 * the table shows at most the 50 rows the artifact keeps inline, so "50 shown of
 * 71,798" is said in those words rather than left for someone to infer from a
 * scrollbar.
 *
 * **A masked column is labelled where it is read.** The values arrive already
 * masked — there is no unmasked copy in the platform database (D-013, WP5.2b) —
 * so the header carries the badge, and nobody mistakes `k***@e***.com` for a
 * value the database actually holds.
 *
 * The SQL is shown verbatim, for the same reason the catalog screen shows a card
 * verbatim: somebody asking why the answer looks odd should be able to read
 * exactly what ran. It is this service's own canonical statement, not the
 * model's text.
 */

import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Row } from "@/components/ui/page";
import { ApiError, createApi, type Execution } from "@/lib/api-client";
import { useSession } from "@/lib/auth/session";

import styles from "./evidence.module.css";

/** What a cell shows when the database returned no value at all. */
const NOTHING = "—";

function cell(value: unknown): string {
  if (value === null || value === undefined) return NOTHING;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function duration(ms: number | null): string | null {
  if (ms === null) return null;
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/** "50 shown of 71,798" rather than a table the reader has to measure. */
function rowSummary(execution: Execution): string {
  const total = execution.row_count;
  const shown = execution.sample_rows.length;
  if (total === null) return `${shown} row${shown === 1 ? "" : "s"}`;
  const counted = total.toLocaleString();
  if (shown >= total) return `${counted} row${total === 1 ? "" : "s"}`;
  return `${shown} shown of ${counted}`;
}

function Refused({ execution }: { execution: Execution }) {
  return (
    <div className={styles.refused}>
      <Row>
        <Badge tone="rose">refused</Badge>
        {execution.violation_code && <Badge tone="neutral">{execution.violation_code}</Badge>}
      </Row>
      <p className={styles.refusedText}>
        {execution.error ?? "This statement was refused before it reached the database."}
      </p>
      <p className={styles.note}>
        Nothing was sent to the database, so there are no rows to show.
      </p>
    </div>
  );
}

function Failed({ execution }: { execution: Execution }) {
  return (
    <div className={styles.refused}>
      <Row>
        <Badge tone="peach">error</Badge>
      </Row>
      <p className={styles.refusedText}>
        {execution.error ?? "The database did not answer this statement."}
      </p>
    </div>
  );
}

function Results({ execution }: { execution: Execution }) {
  const masked = new Set(execution.masked_columns);
  if (execution.columns.length === 0) {
    return <p className={styles.note}>This query returned no columns.</p>;
  }
  return (
    <>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              {execution.columns.map((column) => (
                <th key={column} scope="col">
                  <span className={styles.columnName}>{column}</span>
                  {masked.has(column) && <Badge tone="peach">masked</Badge>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {execution.sample_rows.map((row, index) => (
              // The index is the key because a result row has no identity of its
              // own — two identical rows are a legitimate result, not a bug.
              <tr key={index}>
                {execution.columns.map((column, position) => (
                  <td key={column}>{cell(row[position])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {execution.sample_rows.length === 0 && (
        <p className={styles.note}>This query matched no rows.</p>
      )}
      {execution.masked_columns.length > 0 && (
        <p className={styles.note}>
          Values in masked columns were obscured by an organization policy before they were
          stored. The unmasked values were never kept.
        </p>
      )}
    </>
  );
}

export function EvidencePanel({
  orgId,
  runId,
  executionId,
}: {
  orgId: string;
  runId: string;
  executionId: string;
}) {
  const session = useSession();
  const [execution, setExecution] = useState<Execution | null>(null);
  const [error, setError] = useState<string | null>(null);
  const api = useMemo(() => createApi(session.getToken), [session.getToken]);

  useEffect(() => {
    // Guarded so a slow response cannot write into an unmounted panel — a
    // citation can be collapsed again before the query comes back.
    let active = true;
    void (async () => {
      try {
        const found = await api.execution(orgId, runId, executionId);
        if (active) setExecution(found);
      } catch (cause) {
        // A citation that will not open is worth saying out loud rather than
        // rendering as an empty panel: it is the one failure that would
        // otherwise look like the evidence simply being thin.
        if (active) {
          setError(
            cause instanceof ApiError ? cause.message : "This citation could not be opened.",
          );
        }
      }
    })();
    return () => {
      active = false;
    };
  }, [api, orgId, runId, executionId]);

  if (error) return <p className={styles.error}>{error}</p>;
  if (!execution) return <p className={styles.note}>Opening the query…</p>;

  const took = duration(execution.duration_ms);
  return (
    <Card tone="sunken">
      <div className={styles.facts}>
        {execution.status === "ok" && <span>{rowSummary(execution)}</span>}
        {took && <span>{took}</span>}
        {execution.tables.length > 0 && <span>{execution.tables.join(", ")}</span>}
        {execution.truncated && <Badge tone="peach">truncated</Badge>}
        {execution.sensitive_accessed && <Badge tone="lilac">touched sensitive columns</Badge>}
      </div>

      <pre className={styles.sql}>
        <code>{execution.sql}</code>
      </pre>

      {execution.status === "refused" && <Refused execution={execution} />}
      {execution.status === "error" && <Failed execution={execution} />}
      {execution.status === "ok" && <Results execution={execution} />}
    </Card>
  );
}
