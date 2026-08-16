"use client";

/**
 * How the answer was reached, as it is reached (plan WP8.3, architecture 10.3).
 *
 * The product's honesty claim, rendered. `agent_events` is append-only by grant
 * precisely so this can be shown as a record rather than as a story — what you
 * see here was written once, by the code that did the thing, and cannot have
 * been tidied up afterwards.
 *
 * **It subscribes rather than polls**, over the same events route asked for as
 * `text/event-stream`. Two consequences are the whole reason for it: a step
 * appears when it happens rather than up to a poll later, and **a refresh
 * replays the entire trace** — the reader resumes from `Last-Event-ID` and the
 * server answers from the durable rows. Nothing is held in memory that a reload
 * could lose.
 *
 * The stream is read with `fetch`, not `EventSource`, so the bearer token can go
 * in a header: `EventSource` cannot set headers, and the query string is where
 * this codebase has already refused to put a credential once.
 *
 * **Every event is shown, including the ones that are not progress.** A trace
 * that only listed successes would be advertising. A refused query, a duplicate
 * blocked, a budget warning and a capability gap are exactly what somebody
 * checking the agent's work needs to see, and they are the events most likely to
 * be quietly dropped by a UI written to look good.
 *
 * **Names are ours, not the model's.** 10.3's payloads are built for eyes, but
 * its *type names* are machine names; a person watching should read "Running the
 * query", not `query_executed`. Anything unrecognised falls back to the raw type
 * rather than being hidden — a trace that silently omits an event it does not
 * know about is worse than one that shows an ugly word.
 */

import { useEffect, useMemo, useState } from "react";

import { Badge, type Tone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { createApi, type RunEvent } from "@/lib/api-client";
import { useSession } from "@/lib/auth/session";

import styles from "./trace.module.css";

/** Plain words for 10.3's vocabulary. */
const STEP_WORDS: Record<string, string> = {
  run_started: "Started",
  intent_classified: "Read the question",
  context_selected: "Read the catalog",
  capability_checked: "Checked what this database can answer",
  plan_created: "Wrote a query",
  step_started: "Next step",
  tool_called: "Ran the query",
  sql_validated: "Query checked",
  sql_rejected: "Query refused",
  query_executed: "Got results",
  result_summarized: "Read the results",
  finding_added: "Noted a finding",
  hypothesis_updated: "Updated a hypothesis",
  reflection: "Decided what to do next",
  critic_verdict: "Checked the answer",
  budget_warning: "Approaching a limit",
  budget_exhausted: "Stopped at a limit",
  answer_composed: "Wrote the answer",
  run_finished: "Finished",
  error: "Something went wrong",
};

/** Which events are worth colouring, and why — never colour for interest. */
const TONES: Record<string, Tone> = {
  sql_rejected: "peach",
  budget_warning: "peach",
  budget_exhausted: "peach",
  capability_checked: "lilac",
  finding_added: "mint",
  query_executed: "mint",
  run_finished: "mint",
  error: "rose",
};

function line(event: RunEvent): string {
  /**
   * One line of detail, taken only from fields 10.3 promises. Anything absent
   * renders as nothing rather than as "undefined" — a payload shape that changed
   * should make the trace quieter, not wrong.
   */
  const payload = event.payload;
  const text = (key: string): string | null => {
    const value = payload[key];
    return typeof value === "string" && value.length > 0 ? value : null;
  };
  const count = (key: string): string | null => {
    const value = payload[key];
    return typeof value === "number" ? String(value) : null;
  };

  switch (event.type) {
    case "context_selected": {
      const tables = payload.tables;
      return Array.isArray(tables) && tables.length > 0
        ? `${tables.length} table${tables.length === 1 ? "" : "s"}: ${tables.join(", ")}`
        : "";
    }
    case "capability_checked": {
      const unreachable = payload.unreachable;
      if (Array.isArray(unreachable) && unreachable.length > 0) {
        return `cannot be joined — ${unreachable.join("; ")}`;
      }
      return "every table needed can be joined";
    }
    case "plan_created":
      return text("purpose") ?? "";
    case "step_started":
      return count("iteration") ? `step ${count("iteration")}` : "";
    case "sql_rejected":
      return text("rule") ? `refused: ${text("rule")}` : "refused";
    case "query_executed": {
      const rows = count("row_count");
      const ms = count("duration_ms");
      return [rows ? `${rows} row${rows === "1" ? "" : "s"}` : null, ms ? `${ms} ms` : null]
        .filter(Boolean)
        .join(" · ");
    }
    case "result_summarized":
      return text("one_liner") ?? "";
    case "finding_added":
      return text("statement") ?? "";
    case "reflection":
      return text("public_rationale") ?? "";
    case "budget_warning":
      return text("dimension") ? `${text("dimension")} is running low` : "";
    case "budget_exhausted":
      return text("reason") ?? text("dimension") ?? "";
    case "error":
      return text("safe_message") ?? "";
    case "run_finished":
      return text("status") ?? "";
    default:
      return "";
  }
}

/**
 * Subscribe to a run's trace, replaying whatever came before.
 *
 * Returns the events in order. The `EventSource` is closed when the run ends or
 * the component unmounts, because a page left open must not hold a stream on a
 * run that finished.
 */
function useTrace(orgId: string, runId: string | null): RunEvent[] {
  const session = useSession();
  // Keyed by run, so switching runs cannot show the previous one's trace and
  // nothing has to be cleared in an effect — the stale buffer is simply not the
  // one this render reads.
  const [buffer, setBuffer] = useState<{ runId: string | null; events: RunEvent[] }>({
    runId: null,
    events: [],
  });
  const api = useMemo(() => createApi(session.getToken), [session.getToken]);

  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController();
    const seen = new Set<number>();

    void api
      .streamRunEvents(orgId, runId, {
        onEvent: (event) => {
          if (seen.has(event.seq)) return;
          seen.add(event.seq);
          setBuffer((was) =>
            was.runId === runId
              ? { runId, events: [...was.events, event].sort((a, b) => a.seq - b.seq) }
              : { runId, events: [event] },
          );
        },
        signal: controller.signal,
      })
      .catch(() => {
        // A trace that will not load is not worth breaking the answer over: the
        // run is unaffected by whether anyone is watching it, and the answer
        // arrives through its own path.
      });

    return () => controller.abort();
  }, [api, orgId, runId]);

  return buffer.runId === runId ? buffer.events : [];
}


export function Trace({
  orgId,
  runId,
  live,
  defaultOpen = false,
}: {
  orgId: string;
  runId: string | null;
  live: boolean;
  defaultOpen?: boolean;
}) {
  const events = useTrace(orgId, runId);
  // Open while the run is going and collapsed once it has finished — watching is
  // the point during, the answer is the point after — but an explicit toggle
  // wins from then on. Derived rather than synced in an effect: the default is a
  // function of `live`, not a thing that has to be kept in step with it.
  const [toggled, setToggled] = useState<boolean | null>(null);
  const open = toggled ?? (live || defaultOpen);

  const latest = useMemo(() => events.at(-1), [events]);
  if (!runId || events.length === 0) return null;

  return (
    <div className={styles.trace}>
      <Button
        variant="ghost"
        aria-expanded={open}
        onClick={() => setToggled(!open)}
        className={styles.toggle}
      >
        {open ? "Hide" : "Show"} how this was worked out ({events.length} step
        {events.length === 1 ? "" : "s"})
      </Button>

      {!open && latest && (
        <span className={styles.latest}>{STEP_WORDS[latest.type] ?? latest.type}</span>
      )}

      {open && (
        <ol className={styles.steps}>
          {events.map((event) => {
            const detail = line(event);
            return (
              <li key={event.seq} className={styles.step}>
                <span className={styles.seq}>{event.seq}</span>
                <div className={styles.body}>
                  <Badge tone={TONES[event.type] ?? "neutral"}>
                    {STEP_WORDS[event.type] ?? event.type}
                  </Badge>
                  {detail && <p className={styles.detail}>{detail}</p>}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
