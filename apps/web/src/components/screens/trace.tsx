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

/**
 * Which events are worth colouring, and why — never colour for interest.
 *
 * Text colour rather than a badge: a row of badges made the trace read as a list
 * of labels, and design.md's rule 4 puts the step word above its decoration. The
 * word is always there, so colour is a second cue and never the only one.
 */
const TONE_CLASS: Record<string, string | undefined> = {
  sql_rejected: styles.wordWarn,
  budget_warning: styles.wordWarn,
  budget_exhausted: styles.wordWarn,
  finding_added: styles.wordOk,
  query_executed: styles.wordOk,
  run_finished: styles.wordOk,
  error: styles.wordError,
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


/**
 * How long the run took, in words, from something real.
 *
 * The run's own clock first; the first and last event otherwise. **If neither is
 * knowable it says `Thought` with no number** rather than inventing one — a
 * duration is a claim, and this component's whole premise is that it only
 * repeats claims the platform already made.
 */
function tookFor(
  events: RunEvent[],
  startedAt: string | null | undefined,
  finishedAt: string | null | undefined,
): string {
  const from = startedAt ?? events[0]?.ts;
  const to = finishedAt ?? events.at(-1)?.ts;
  if (!from || !to) return "Thought";

  const ms = new Date(to).getTime() - new Date(from).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "Thought";

  const seconds = Math.max(1, Math.round(ms / 1000));
  if (seconds < 60) return `Thought for ${seconds} second${seconds === 1 ? "" : "s"}`;
  const minutes = Math.round(seconds / 60);
  return `Thought for ${minutes} minute${minutes === 1 ? "" : "s"}`;
}

/**
 * The working state (docs/design.md, *The working state*).
 *
 * **Every row here is one durable event, in the order it was written.** There is
 * no scripted sequence, no minimum display time and no step that appears because
 * somebody expected it to: if the stream stops, this stops. A progress display
 * that runs ahead of the work is the most convincing lie an interface can tell,
 * and this product's claim is that its account of itself is checkable.
 */
export function Trace({
  orgId,
  runId,
  live,
  defaultOpen = false,
  startedAt,
  finishedAt,
}: {
  orgId: string;
  runId: string | null;
  live: boolean;
  defaultOpen?: boolean;
  /** The run's own clock, for the settled word. Absent falls back to the events. */
  startedAt?: string | null | undefined;
  finishedAt?: string | null | undefined;
}) {
  const events = useTrace(orgId, runId);
  // Open while the run is going and collapsed once it has finished — watching is
  // the point during, the answer is the point after — but an explicit toggle
  // wins from then on. Derived rather than synced in an effect: the default is a
  // function of `live`, not a thing that has to be kept in step with it.
  const [toggled, setToggled] = useState<boolean | null>(null);
  const open = toggled ?? (live || defaultOpen);

  if (!runId || events.length === 0) return null;

  return (
    <div className={styles.trace}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setToggled(!open)}
        className={styles.toggle}
      >
        <svg
          className={live ? styles.markLive : styles.mark}
          width="16"
          height="16"
          viewBox="0 0 24 24"
          aria-hidden="true"
        >
          <path d="M12 2l2.4 7.2L22 12l-7.6 2.8L12 22l-2.4-7.2L2 12l7.6-2.8z" />
        </svg>
        {/*
         * **The label is a plain child of the button, and that is deliberate.**
         *
         * It was first wrapped in a `role="status"` region with
         * `display: contents`, and the result was a button with **no accessible
         * name at all** — the text was trapped inside the live region and never
         * reached the name computation, so a screen reader announced "button"
         * and nothing else. Caught by an e2e locator that could not find it by
         * name, which is the only reason it was found before shipping.
         *
         * `aria-live` here does the announcing without taking the text out of
         * the name: the role is unchanged, so name-from-content still sees it.
         */}
        <span aria-live="polite" className={live ? styles.working : styles.settled}>
          {live ? "Thinking" : tookFor(events, startedAt, finishedAt)}
        </span>
        <svg
          className={open ? styles.chevOpen : styles.chev}
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>

      {/* A grid whose single row goes 0fr → 1fr, which animates a height the
          component does not have to measure. */}
      <div className={open ? styles.expanderOpen : styles.expander}>
        <div className={styles.expanderClip}>
          <ol className={styles.steps}>
            {events.map((event, index) => {
              const detail = line(event);
              // The last row carries the spinner only while the run is still
              // going. Once it has settled every step finished, so every one
              // of them gets a check.
              const running = live && index === events.length - 1;
              return (
                <li
                  key={event.seq}
                  className={styles.step}
                  style={{ animationDelay: `${Math.min(index, 6) * 60}ms` }}
                >
                  {running ? (
                    <span className={styles.spinner} aria-hidden="true" />
                  ) : (
                    <svg
                      className={styles.tick}
                      width="14"
                      height="14"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2.5"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      aria-hidden="true"
                    >
                      <path d="M20 6L9 17l-5-5" />
                    </svg>
                  )}
                  <span className={TONE_CLASS[event.type] ?? styles.word}>
                    {STEP_WORDS[event.type] ?? event.type}
                  </span>
                  {detail && <span className={styles.detail}>{detail}</span>}
                </li>
              );
            })}
          </ol>
        </div>
      </div>
    </div>
  );
}
