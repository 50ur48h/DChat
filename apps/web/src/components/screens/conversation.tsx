"use client";

/**
 * One thread of questions, and the answers with their evidence (plan WP7.3).
 *
 * The screen the whole build has been pointing at: a person types a question and
 * gets back an answer they can check. Everything below the composer already
 * existed as rows in the platform database — this renders them, and adds no
 * behaviour of its own.
 *
 * **A run is watched, not waited for.** `POST …/messages` answers 202 with a run
 * id, because a run takes thirty seconds to four minutes. So the thread polls
 * `GET …/runs/{id}` until the run reaches a terminal status, and shows what the
 * run is doing meanwhile from its own trace events. The poll is stopped on
 * unmount and on the first terminal status — a page left open must not keep
 * asking forever.
 *
 * **A refusal is an answer.** A run that could not answer *completes*, with a
 * readable reply and no findings (WP7.2b). It is rendered as a reply and not as
 * an error, because dressing an honest refusal up as a failure would send people
 * hunting for a bug in their question. `failed` is the different thing, and says
 * so.
 *
 * **A citation opens.** Each finding lists the executions behind it, and each one
 * expands into `EvidencePanel` — the SQL that ran and the rows it returned. That
 * is the product's central claim made checkable rather than asserted, and it is
 * why the answer card is built around findings instead of around prose.
 *
 * **The send button cannot bill twice.** Every send carries a fresh idempotency
 * key, and the key is held for the lifetime of that draft: a retry after a
 * timeout replays the same key and gets the run that already exists, rather than
 * a second run and a second bill (D-019).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EvidencePanel } from "@/components/screens/evidence";
import { Trace } from "@/components/screens/trace";
import { Badge, type Tone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Row, Stack } from "@/components/ui/page";
import {
  ApiError,
  createApi,
  type Conversation,
  type ConversationMessage,
  type Finding,
  type Run,
} from "@/lib/api-client";
import { useSession } from "@/lib/auth/session";

import styles from "./conversation.module.css";

/** How often a live run is asked how it is getting on. */
const POLL_MS = 1500;

/** Statuses a run never leaves, so polling stops (`runs/service.py`). */
const TERMINAL = new Set([
  "completed",
  "interrupted",
  "failed",
  "budget_exhausted",
]);

const STATUS_TONES: Record<string, Tone> = {
  completed: "mint",
  failed: "rose",
  interrupted: "peach",
  budget_exhausted: "peach",
  queued: "neutral",
  running: "sky",
  validating: "sky",
};

/** Plain words for a status, because a badge's colour never carries meaning alone. */
const STATUS_WORDS: Record<string, string> = {
  queued: "queued",
  running: "working",
  validating: "checking",
  completed: "answered",
  interrupted: "interrupted",
  failed: "failed",
  budget_exhausted: "stopped at its budget",
};

const CONFIDENCE_TONES: Record<string, Tone> = {
  high: "mint",
  medium: "sky",
  low: "peach",
};

function when(timestamp: string): string {
  const parsed = new Date(timestamp);
  return Number.isNaN(parsed.getTime()) ? "" : parsed.toLocaleString();
}

function Citation({
  orgId,
  runId,
  executionId,
  label,
}: {
  orgId: string;
  runId: string;
  executionId: string;
  label: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className={styles.citation}>
      <Button
        variant="ghost"
        aria-expanded={open}
        onClick={() => setOpen((was) => !was)}
        className={styles.citationToggle}
      >
        {open ? "Hide" : "Show"} {label}
      </Button>
      {open && <EvidencePanel orgId={orgId} runId={runId} executionId={executionId} />}
    </div>
  );
}

/**
 * What the answer does not establish.
 *
 * Beside the answer, never instead of it, and never styled as an error — a
 * limitation is part of a good answer, not a failure of one. The list is
 * assembled by the platform from what the run knows (`agent/composer.py`), so a
 * model cannot hedge it away or invent it; an empty list means there was nothing
 * to say, which is the common case and a good one.
 */
function Limitations({ notes }: { notes: string[] }) {
  if (notes.length === 0) return null;
  return (
    <section className={styles.limitations} aria-label="What this answer does not establish">
      <p className={styles.limitationsTitle}>
        {notes.length === 1 ? "One thing to know" : `${notes.length} things to know`}
      </p>
      <ul className={styles.limitationList}>
        {notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </section>
  );
}

/**
 * The claims behind the reply, and the queries behind those.
 *
 * A single-shot run writes one finding whose statement *is* the answer
 * (`runner.py`), so restating it here would print the same sentence twice — once
 * as the reply and once as its own evidence. When they match, the card is the
 * evidence affordance and nothing else. Phase 8's loop produces several distinct
 * findings, and those are shown in full.
 */
function Findings({
  orgId,
  runId,
  findings,
  answer,
}: {
  orgId: string;
  runId: string;
  findings: Finding[];
  answer: string | null;
}) {
  // Cited findings are the evidence for *this* answer. The rest are steps the
  // investigation took on the way, and they are in the trace where they belong —
  // showing all of them under the answer buries the one a reader wants.
  // `cited` is absent on runs written before WP9.2, so an older run keeps
  // showing everything rather than suddenly showing nothing.
  const anyCited = findings.some((finding) => finding.cited);
  const shown = anyCited ? findings.filter((finding) => finding.cited) : findings;
  if (shown.length === 0) return null;
  return (
    <ul className={styles.findings}>
      {shown.map((finding) => (
        <li key={finding.id} className={styles.finding}>
          <Row>
            <Badge tone={CONFIDENCE_TONES[finding.confidence] ?? "neutral"}>
              {finding.confidence} confidence
            </Badge>
          </Row>
          {finding.statement.trim() !== (answer ?? "").trim() && (
            <p className={styles.findingStatement}>{finding.statement}</p>
          )}
          {finding.support.length === 0 ? (
            <p className={styles.unsupported}>This claim cites no query.</p>
          ) : (
            finding.support.map((executionId, position) => (
              <Citation
                key={executionId}
                orgId={orgId}
                runId={runId}
                executionId={executionId}
                // Numbered only when there is more than one, so the common case
                // reads as a sentence rather than as a list of one.
                label={
                  finding.support.length === 1
                    ? "the query behind this"
                    : `query ${position + 1} of ${finding.support.length}`
                }
              />
            ))
          )}
        </li>
      ))}
    </ul>
  );
}

function RunProgress({ orgId, run }: { orgId: string; run: Run }) {
  return (
    <Card tone="sunken">
      <Row>
        <Badge tone={STATUS_TONES[run.status] ?? "neutral"}>
          {STATUS_WORDS[run.status] ?? run.status}
        </Badge>
      </Row>
      <p className={styles.note}>
        This can take a minute. The answer arrives here on its own — you can leave the page and
        come back to it.
      </p>
      {/* The trace replaces the single summary line this card used to show. It
          streams, so a step appears when it happens; and it replays from the
          durable rows, so a refresh mid-run loses nothing. */}
      <Trace orgId={orgId} runId={run.id} live defaultOpen />
    </Card>
  );
}

/**
 * The reply, and what stands behind it.
 *
 * `answered=false` is not an error state and is not styled as one: the run
 * completed, and the reply explains why the data could not answer. What is
 * absent then is the findings list, because a refusal concluded nothing.
 */
function AnswerCard({
  orgId,
  run,
  replied,
}: {
  orgId: string;
  run: Run;
  /** Whether this run's reply is already in the thread above. */
  replied: boolean;
}) {
  const failed = run.status === "failed";
  return (
    <Card tone="sunken">
      <Row>
        <Badge tone={STATUS_TONES[run.status] ?? "neutral"}>
          {STATUS_WORDS[run.status] ?? run.status}
        </Badge>
        {run.findings.length === 0 && !failed && (
          <span className={styles.step}>no supporting query</span>
        )}
      </Row>
      {failed ? (
        <p className={styles.failure}>
          {run.failure_reason ??
            "Something went wrong on our side while answering this. Nothing was wrong with the question."}
        </p>
      ) : (
        <>
          {/* The run carries its own answer, so a card is never wordless even if
              the thread has not caught up or its fetch failed. Suppressed once
              the reply is in the list above, which is where it belongs — the
              gate found this card showing a citation and no words at all. */}
          {!replied && run.answer && <p className={styles.findingStatement}>{run.answer}</p>}
          <Limitations notes={run.limitations ?? []} />
          <Findings orgId={orgId} runId={run.id} findings={run.findings} answer={run.answer} />
          {/* Collapsed once the run is over — the answer is the point then — but
              still there, because "how did you get that" is the question this
              product exists to be able to answer. */}
          <Trace orgId={orgId} runId={run.id} live={false} />
        </>
      )}
    </Card>
  );
}

export function ConversationThread({
  orgId,
  conversationId,
}: {
  orgId: string;
  conversationId: string;
}) {
  const session = useSession();
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Held across retries of one draft, so a resend is the same question rather
  // than a second run and a second bill.
  const idempotencyKey = useRef<string | null>(null);

  // The run whose thread has already been re-read after it finished, so the
  // reload below happens exactly once per run.
  const reconciled = useRef<string | null>(null);

  const api = useMemo(() => createApi(session.getToken), [session.getToken]);

  const loadThread = useCallback(async () => {
    try {
      const [details, thread] = await Promise.all([
        api.conversation(orgId, conversationId),
        api.messages(orgId, conversationId),
      ]);
      setConversation(details);
      setMessages(thread);
      return details;
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "This conversation could not be loaded.");
      return null;
    }
  }, [api, orgId, conversationId]);

  useEffect(() => {
    void (async () => {
      const details = await loadThread();
      // Pick up a run that was still going when the page was last closed —
      // architecture 10.3's whole point is that the record outlives the tab.
      if (details?.last_run_id) {
        try {
          const existing = await api.run(orgId, details.last_run_id);
          // The thread was just read, so a run that had already finished needs
          // no reload — say so rather than paying for a second fetch below.
          if (TERMINAL.has(existing.status)) reconciled.current = existing.id;
          setRun(existing);
        } catch {
          // A run that will not load is not worth blocking the thread over; the
          // messages are the conversation, and they are already shown.
        }
      }
    })();
  }, [loadThread, api, orgId]);

  const runId = run?.id ?? null;
  const runStatus = run?.status ?? null;
  const live = runId !== null && runStatus !== null && !TERMINAL.has(runStatus);

  /**
   * Watch a live run until it stops.
   *
   * **Keyed on the run's id and whether it is live — never on the run object.**
   * Depending on `run` made this effect cancel itself: `setRun(latest)` inside
   * the tick triggered a re-render, whose cleanup set `cancelled`, and the
   * guard after the next `await` then returned before the thread could be
   * reloaded. The effect re-ran, saw a terminal status and stopped, so the
   * answer text never arrived — every reply rendered one message behind, which
   * is what the Phase 7 gate caught. Reloading is now a separate effect below,
   * which cannot be cancelled by the write that triggers it.
   *
   * The interval is cleared on unmount and as soon as `live` goes false, so a
   * page left open does not poll forever.
   */
  useEffect(() => {
    if (!live || runId === null) return;

    let cancelled = false;

    const tick = async () => {
      try {
        // Only the run's *status* is polled now. The steps arrive on the trace's
        // own stream (WP8.3), so this asks one small question — "has it
        // finished?" — rather than re-reading the whole trace every second.
        const latest = await api.run(orgId, runId);
        if (cancelled) return;
        setRun(latest);
      } catch {
        // A dropped poll is not worth an error banner: the next tick asks again,
        // and the run is unaffected by whether anyone is watching.
      }
    };

    const timer = setInterval(() => void tick(), POLL_MS);
    void tick();
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [api, orgId, runId, live]);

  /**
   * When a run finishes, re-read the thread — the reply is a message like any
   * other, and `POST …/messages` never returned it.
   *
   * Separate from the poll on purpose, and once per run: `reconciled` holds the
   * id already reloaded for, so a re-render cannot fetch the same thread twice
   * and a run whose page was opened after it finished is not re-fetched at all.
   */
  useEffect(() => {
    if (runId === null || runStatus === null || !TERMINAL.has(runStatus)) return;
    if (reconciled.current === runId) return;
    reconciled.current = runId;

    void loadThread();
  }, [runId, runStatus, loadThread]);

  const send = useCallback(async () => {
    const question = draft.trim();
    if (!question || sending) return;
    idempotencyKey.current ??= crypto.randomUUID();
    setSending(true);
    setError(null);
    try {
      const accepted = await api.ask(orgId, conversationId, question, idempotencyKey.current);
      setDraft("");
      idempotencyKey.current = null;
      await loadThread();
      setRun(await api.run(orgId, accepted.run_id));
    } catch (cause) {
      // The key is deliberately kept, so pressing Send again replays the same
      // question rather than starting a second run.
      setError(cause instanceof ApiError ? cause.message : "That question could not be sent.");
    } finally {
      setSending(false);
    }
  }, [api, orgId, conversationId, draft, sending, loadThread]);

  const answered = run !== null && TERMINAL.has(run.status);
  const replied =
    run !== null &&
    messages.some((message) => message.role === "assistant" && message.run_id === run.id);

  return (
    <Stack>
      {conversation && (
        <Card>
          <Row>
            {conversation.data_source_name ? (
              <Badge tone="lilac">{conversation.data_source_name}</Badge>
            ) : (
              <Badge tone="peach">no database chosen</Badge>
            )}
            <span className={styles.step}>
              started {when(conversation.created_at)}
            </span>
          </Row>
          {!conversation.data_source_id && (
            <p className={styles.note}>
              This conversation is not tied to a database. If this organization has exactly one,
              questions here are answered against it; if it has several, they will be refused
              until you start a conversation that names one.
            </p>
          )}
        </Card>
      )}

      <ol className={styles.thread}>
        {messages.map((message) => (
          <li
            key={message.id}
            className={message.role === "user" ? styles.fromUser : styles.fromAgent}
          >
            <p className={styles.messageText}>{message.content}</p>
            <span className={styles.timestamp}>{when(message.created_at)}</span>
          </li>
        ))}
      </ol>

      {live && run && <RunProgress orgId={orgId} run={run} />}
      {answered && run && <AnswerCard orgId={orgId} run={run} replied={replied} />}

      <Card>
        <label className={styles.composerLabel} htmlFor="question">
          Ask a question
        </label>
        <textarea
          id="question"
          className={styles.composer}
          rows={3}
          value={draft}
          disabled={sending}
          placeholder="How many orders were placed in July 2026?"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            // Enter sends, Shift+Enter is a newline — the convention every chat
            // input in the world uses, so people try it without being told.
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void send();
            }
          }}
        />
        <Row>
          <Button variant="primary" disabled={sending || draft.trim().length === 0} onClick={() => void send()}>
            {sending ? "Sending…" : "Send"}
          </Button>
          {live && <span className={styles.step}>a question is already running</span>}
        </Row>
        {error && <p className={styles.failure}>{error}</p>}
      </Card>
    </Stack>
  );
}
