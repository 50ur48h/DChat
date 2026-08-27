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
import { Pending } from "@/components/ui/pending";
import { Row } from "@/components/ui/page";
import {
  ApiError,
  createApi,
  type Conversation,
  type ConversationMessage,
  type Finding,
  type Run,
  type RunChart,
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

/**
 * The badge for an ending, which is **not** the status word alone (B-133).
 *
 * WP7.2b's rule: *"A refusal is an ending, not a failure. A run that could not
 * answer completes with `answered=false` and a reason."* So `completed` covers an
 * answer and an honest refusal alike, and rendering `STATUS_WORDS.completed` for
 * both labelled every refusal **answered** — the one claim a refusal exists to
 * deny. Seen on the deployed app on 2026-08-25: a card badged *answered*, marked
 * *no supporting query*, saying *"The data does not establish which outlet wastes
 * the most."*
 *
 * `answered == null` keeps the status word: runs that ended before revision 0029
 * never recorded it, and guessing from the absence of findings would put a word in
 * the mouth of a run that never said it.
 *
 * Mint is deliberately not kept. A refusal is a correct, useful outcome and not an
 * error, so it is neither green nor red — the colour says "this is a different
 * kind of ending", and the word says which.
 */
function outcome(run: Run): string | null {
  return run.status === "completed" && run.state ? run.state : null;
}

/**
 * **The badge names the missing half, never just the state** (D-044).
 *
 * "Partly answered" on its own tells a reader less than the wrong badge did: they
 * now know something is missing and not what. `unanswered` is non-empty exactly
 * when the state is `partly` — a database CHECK, not a convention — so there is
 * always something to name, and the fallback below is unreachable rather than
 * merely unlikely.
 */
function endingWord(run: Run): string {
  const state = outcome(run);
  if (state === "refused") return "could not answer";
  if (state === "partly") {
    return run.unanswered ? `could not answer ${run.unanswered}` : "partly answered";
  }
  return STATUS_WORDS[run.status] ?? run.status;
}

/**
 * Which colour the ending word is set in — not which badge it wears.
 *
 * **Neither green nor red for the two endings that are neither.** A refusal and
 * a partial answer are correct outcomes rather than errors, so they are simply
 * muted and the word carries the whole meaning (D-044). Only a `failed` run is
 * red, because only that one is something going wrong.
 */
function endingClass(run: Run): string | undefined {
  const state = outcome(run);
  if (run.status === "failed") return styles.stateFailed;
  if (state === "refused" || state === "partly") return styles.stateNeutral;
  return styles.stateOk;
}

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
  startOpen = false,
}: {
  orgId: string;
  runId: string;
  executionId: string;
  label: string;
  /**
   * Whether the query is already on show when the evidence is opened.
   *
   * True for a finding with a single citation, which is the common case: having
   * opened *Evidence*, being asked to open the evidence again is a click that
   * buys nothing. With several citations the buttons stay closed, because a
   * finding that rests on four queries should not unfold into four SQL blocks
   * at once.
   */
  startOpen?: boolean;
}) {
  const [open, setOpen] = useState(startOpen);
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
/**
 * The chart, or the reason there is none (WP11.1, **B-048**).
 *
 * **Inside the answer card**, which is B-048's requirement: a chart beside the
 * answer is a picture next to some prose, while a chart inside it is part of the
 * claim. And its spec opens the way the SQL does, because a picture nobody can
 * check is decoration that looks like evidence.
 *
 * **A refusal renders here too, in the chart's own place.** It is deliberately
 * not a limitation: that list is about whether the answer is *true*, and a
 * missing picture says nothing about that — putting it there would teach readers
 * to skim the region that carries an unresolved critic block. What a reader
 * needs is an answer to "where is the chart", where they were looking for one.
 *
 * The renderer is loaded on demand. `vega-embed` is large, most answers carry no
 * chart, and a bundle every reader pays for to serve a minority of answers is a
 * cost with no case.
 */
/**
 * Vega's config, in the page's own colours.
 *
 * Read from the computed style rather than written twice: the app's tokens are
 * the single place a colour is decided, and a chart with its own palette is a
 * chart that drifts from the product the first time somebody changes a token.
 */
/**
 * The light-mode categorical steps, for the one case `getComputedStyle` returns
 * nothing: jsdom, where the unit tests run. Not a second source of truth — the
 * tokens are — but a chart that silently fell back to eight copies of one colour
 * would be the flat purple this was built to fix, wearing a passing test.
 */
const CHART_CATEGORY_FALLBACK = [
  "#5b5bd6",
  "#eb6834",
  "#1baf7a",
  "#eda100",
  "#e87ba4",
  "#008300",
  "#2a78d6",
  "#e34948",
];

function chartTheme(element: HTMLElement): Record<string, unknown> {
  const style = getComputedStyle(element);
  const token = (name: string, fallback: string) =>
    style.getPropertyValue(name).trim() || fallback;

  const ink = token("--fg", "#101828");
  const muted = token("--fg-muted", "#667085");
  const line = token("--border", "#eaecf0");
  const primary = token("--primary", "#5b5bd6");

  const axis = {
    labelColor: muted,
    titleColor: muted,
    domainColor: line,
    tickColor: line,
    gridColor: line,
    labelFontSize: 11,
    titleFontSize: 11,
    titleFontWeight: 500 as const,
  };

  // **The categorical range, in fixed order** (**B-109**). Read from the tokens
  // like everything else here, because a palette written into a spec is the same
  // bug design.md names for a colour written into a component. Never cycled: a
  // ninth series would repeat slot 1 and two series would look like one, which
  // is why `charts.MAX_SERIES` refuses before it can happen.
  const category = Array.from({ length: 8 }, (_, index) =>
    token(`--chart-cat-${index + 1}`, CHART_CATEGORY_FALLBACK[index] ?? primary),
  );

  return {
    // Transparent, so the card's surface shows through and the chart is part of
    // the answer rather than a picture pasted onto it.
    background: "transparent",
    range: { category },
    font: style.fontFamily,
    axis,
    axisX: { ...axis, labelAngle: 0 },
    view: { stroke: "transparent" },
    line: { color: primary, strokeWidth: 2 },
    bar: { fill: primary },
    point: { fill: primary },
    area: { fill: primary, fillOpacity: 0.2, line: { color: primary } },
    title: { color: ink, fontSize: 13, fontWeight: 600 as const },
    legend: { labelColor: muted, titleColor: muted },
  };
}

function AnswerChart({ chart }: { chart: RunChart | null | undefined }) {
  const host = useRef<HTMLDivElement | null>(null);
  const [failed, setFailed] = useState(false);
  const [showSpec, setShowSpec] = useState(false);
  const spec = chart?.spec ?? null;

  useEffect(() => {
    if (!spec || !host.current) return;
    let live = true;
    const target = host.current;
    void (async () => {
      try {
        const { default: embed } = await import("vega-embed");
        if (!live) return;
        // `actions: false` removes vega's own export menu: this product's answer
        // to "let me check that" is the spec below and the SQL behind it, not a
        // menu that saves a PNG.
        //
        // The theme is read from the page's own tokens rather than hard-coded,
        // so a chart belongs to the card it sits in — vega's default is a white
        // box, which on this surface reads as an image that failed to load
        // rather than as part of the answer.
        // `"container"` is vega-lite's own value for "as wide as the parent",
        // and it belongs on the spec: the embed option of the same name is
        // typed as a number, because it means something else there.
        const sized = { ...spec, width: "container", autosize: { type: "fit-x", contains: "padding" } };
        await embed(target, sized as Parameters<typeof embed>[1], {
          actions: false,
          renderer: "svg",
          config: chartTheme(target),
        });
      } catch {
        // Defence in depth (the plan's phrase): the server already validated
        // this spec, so a failure here is a renderer problem rather than a bad
        // document — and the table under the citation still has the numbers.
        if (live) setFailed(true);
      }
    })();
    return () => {
      live = false;
      target.replaceChildren();
    };
  }, [spec]);

  if (!chart) return null;

  if (!spec) {
    return (
      <p className={styles.chartNote}>{chart.declined ?? "No chart was drawn."}</p>
    );
  }

  return (
    <figure className={styles.chart}>
      {failed ? (
        <p className={styles.chartNote}>
          The chart could not be drawn in this browser. The numbers are in the result below.
        </p>
      ) : (
        <div ref={host} data-testid="chart" />
      )}
      <figcaption>
        <button
          type="button"
          className={styles.specToggle}
          onClick={() => setShowSpec((open) => !open)}
        >
          {showSpec ? "Hide chart spec" : "Chart spec"}
        </button>
      </figcaption>
      {showSpec ? <pre className={styles.spec}>{JSON.stringify(spec, null, 2)}</pre> : null}
    </figure>
  );
}

/**
 * How the answer was reached, in one line (**B-100**).
 *
 * Architecture 4.2 makes an answer four things — the words, the evidence, the
 * method, the limitations — and this was the part built on every run since
 * Phase 9 and shown on none. It is for the reader who wants to know *how*
 * without opening the SQL, which is a different question from the one the
 * evidence panel answers and a much more common one.
 *
 * Above the limitations and styled quieter, deliberately. "How did you get
 * this" is not a doubt about the answer, and a method line that read like a
 * caveat would make every answer look qualified.
 *
 * Absent rather than blank when there is nothing to say: a run answered before
 * the column existed, or one that never composed.
 */
function Method({ method }: { method?: string | undefined }) {
  if (!method) return null;
  return (
    <p className={styles.method}>
      <span className={styles.methodLabel}>How: </span>
      {method}
    </p>
  );
}

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
                startOpen={finding.support.length === 1}
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
        The answer arrives here on its own — you can leave the page and come back to it.
      </p>
      {/* The working state (D-047). It streams, so a step appears when it
          happens, and it replays from the durable rows, so a refresh mid-run
          loses nothing. */}
      <Trace
        orgId={orgId}
        runId={run.id}
        live
        defaultOpen
        startedAt={run.started_at}
        finishedAt={run.finished_at}
      />
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
/**
 * What this organization's own definitions did to this answer (**B-087**).
 *
 * The absence of this line cost three gate walks. A definition is matched to a
 * question by name and synonym, so a question that never says "prep quantity"
 * reaches nothing — and the run then answers exactly as it would have with no
 * semantic layer at all. Indistinguishable from the feature being broken, which
 * is what it was taken for, three times.
 *
 * **Silent when there was nothing to match.** An organization that has defined
 * no metrics does not need telling that none applied, and a caveat on every
 * answer is how people learn to stop reading caveats — the failure B-079 was
 * filed about, arriving from the other direction. The line appears only when
 * definitions existed and the question reached none of them, which is precisely
 * the state nobody could see.
 */
function Grounding({ run }: { run: Run }) {
  const applied = run.definitions_applied ?? [];
  const available = run.definitions_available ?? 0;

  if (applied.length > 0) {
    return <span className={styles.step}>governed by {applied.join(", ")}</span>;
  }
  if (available > 0) {
    return (
      <span className={styles.step}>
        no definition matched this question ({available} defined here)
      </span>
    );
  }
  return null;
}

function AnswerCard({
  orgId,
  run,
  answer,
}: {
  orgId: string;
  run: Run;
  /**
   * The words this turn said, when the card is rendering a message.
   *
   * The two are the same string by construction — the run's `answer` is *read
   * from* the assistant message — so this is not a second source of truth. It is
   * which of the two the card should quote: rendering a message means rendering
   * that message's own content, and the run's copy is the fallback for the card
   * that appears before the message exists (**B-044**).
   */
  answer?: string | undefined;
}) {
  const words = answer ?? run.answer;
  const failed = run.status === "failed";
  const queries = run.findings.reduce((total, finding) => total + finding.support.length, 0);
  return (
    <div className={styles.turnAgent} data-testid="answer">
      {/* **A caption, not a header** (docs/design.md, rule 4). No pill, no fill,
          nothing at badge weight: an `answered` badge above a 19px sentence
          draws the eye to the label rather than to the answer. The state is a
          word in colour with a dot beside it, so it still reads with colour
          ignored. */}
      <div className={styles.attribution}>
        <span className={styles.agentMark} aria-hidden="true">
          da
        </span>
        <span className={endingClass(run)}>
          <span className={styles.stateDot} aria-hidden="true" />
          {endingWord(run)}
        </span>
        {run.findings.length === 0 && !failed && (
          <>
            <span className={styles.sep} aria-hidden="true">
              ·
            </span>
            <span className={styles.meta}>no supporting query</span>
          </>
        )}
        {!failed && <Grounding run={run} />}
      </div>
      {failed ? (
        <p className={styles.failure}>
          {run.failure_reason ??
            "Something went wrong on our side while answering this. Nothing was wrong with the question."}
        </p>
      ) : (
        <>
          {/* **The card says the words** (**B-106**). It used to suppress them
              once the same sentence arrived in the thread as a message, which is
              why `replied` existed — and having the answer in two places, only
              one of which carried the chart and the evidence, is what the Phase 7
              gate caught from the other direction when the card rendered a
              citation and no words at all. The card is the assistant turn now, so
              there is one rendering of an answer and nothing to keep in step. */}
          {words && (
            <p className={styles.answerText} data-testid="answer-text">
              {words}
            </p>
          )}
          <AnswerChart chart={run.chart} />
          {/* **A caveat is never folded away.** It changes what the answer
              means, so a reader who opens nothing must still see it; hiding the
              qualification while showing the claim is the defect this product
              exists to avoid (B-133, D-044). */}
          <Limitations notes={run.limitations ?? []} />

          {/* The working: quiet until the response is hovered, and reachable
              four other ways — see conversation.module.css. */}
          <div className={styles.working}>
            {run.findings.length > 0 && (
              <details className={styles.disclosure}>
                <summary className={styles.summary}>
                  <span className={styles.chev} aria-hidden="true">
                    ▶
                  </span>
                  {/* Its own element rather than a bare text node: the summary's
                      full text is the chevron, the label and the count together,
                      so nothing can address the label without one. */}
                  <span className={styles.summaryLabel}>Evidence</span>
                  <span className={styles.summaryMeta}>
                    {queries} quer{queries === 1 ? "y" : "ies"}
                  </span>
                </summary>
                <div className={styles.disclosureBody}>
                  <Method method={run.method} />
                  <Findings orgId={orgId} runId={run.id} findings={run.findings} answer={words} />
                </div>
              </details>
            )}
            {/* Its own toggle rather than a `<details>`, because it has to open
                itself while a run is live. Styled to match the summary beside
                it in `trace.module.css`. */}
            <Trace
              orgId={orgId}
              runId={run.id}
              live={false}
              startedAt={run.started_at}
              finishedAt={run.finished_at}
            />
            <Cost run={run} />
          </div>
        </>
      )}
    </div>
  );
}

/**
 * What the question cost, beside the working that produced it.
 *
 * **The columns behind this existed since revision 0012 and nothing wrote them**
 * — `model_usage`'s own comment called it "a rollup for the trace UI", and the
 * rollup was never built, so the API returned `null` and `{}` and no screen
 * could show what a run cost. The rollup now runs when a run ends; this is the
 * half a person reads.
 *
 * **Null is shown as "not priced", never as a number.** A run holding a call the
 * deployment cannot price reports no total rather than one that quietly omits
 * it, and the breakdown says how many calls that was — so the absence is
 * informative instead of merely blank.
 */
function Cost({ run }: { run: Run }) {
  const usage = run.model_usage ?? {};
  const calls = usage.calls ?? 0;
  if (calls === 0 && run.cost_estimate == null) return null;

  const unpriced = usage.unpriced_calls ?? 0;
  const total = run.cost_estimate == null ? null : Number(run.cost_estimate);
  // `agent_runs.cost_estimate` is stored at four decimal places while the ledger
  // prices each call at six, so a very cheap run rounds to 0.0000. Showing
  // "$0.0000" would read as free, which is the one thing this column promises
  // never to say.
  const money =
    total === null
      ? null
      : total === 0
        ? "less than $0.0001"
        : `$${total.toFixed(total < 0.01 ? 4 : 2)}`;

  const detail = [
    `${calls} model call${calls === 1 ? "" : "s"}`,
    usage.input_tokens || usage.output_tokens
      ? `${((usage.input_tokens ?? 0) + (usage.output_tokens ?? 0)).toLocaleString()} tokens`
      : null,
    unpriced > 0 ? `${unpriced} not priced` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <span className={styles.cost} data-testid="run-cost">
      <span className={styles.costAmount}>{money ?? "not priced"}</span>
      <span className={styles.costMeta}>{detail}</span>
    </span>
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
  /**
   * Every answered run in the thread, by id (**B-106**).
   *
   * The screen used to hold exactly one run, so a second question took the first
   * answer's chart, method line, limitations, findings, evidence controls and
   * trace off the screen — durable rows with no route to them. A demo that loses
   * its chart on the next question is the gate criterion failing one message
   * late.
   */
  const [runs, setRuns] = useState<Map<string, Run>>(new Map());
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
      const [details, thread, answered] = await Promise.all([
        api.conversation(orgId, conversationId),
        api.messages(orgId, conversationId),
        // **One request for every run in the thread** (**B-106**), not one per
        // assistant message: the cost of opening a conversation should not grow
        // with how much somebody has used it.
        //
        // **And it cannot take the thread down with it.** The runs enrich the
        // answers; the messages *are* the conversation. Sharing one `Promise.all`
        // and one `catch` meant a failure here emptied the screen — words,
        // questions and all — which CI caught against a stub that did not serve
        // this route yet. Failing to null keeps whatever runs were already held,
        // so a later refresh that fails leaves the cards it had rather than
        // dropping every answer to a bare line of text.
        api.conversationRuns(orgId, conversationId).catch(() => null),
      ]);
      setConversation(details);
      setMessages(thread);
      if (answered !== null) setRuns(new Map(answered.map((found) => [found.id, found])));
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

  /**
   * The run to render for an assistant message.
   *
   * The live one wins where they overlap: it is polled to completion, so it is
   * the fresher of the two for the run being watched, and `loadThread` fills the
   * rest of the thread in around it.
   */
  const runFor = (messageRunId: string | null): Run | null => {
    if (messageRunId === null) return null;
    const found = run !== null && run.id === messageRunId ? run : (runs.get(messageRunId) ?? null);
    // Only a finished run has an answer to be the card of. A message should
    // never name an unfinished one — the reply is written when the run ends —
    // but a card that rendered "working" inside the thread would put a second
    // live badge on the screen beside the real one, so the guard is here rather
    // than assumed.
    return found !== null && TERMINAL.has(found.status) ? found : null;
  };

  /**
   * Whether the live run already has its message in the thread.
   *
   * Until it does, the card below the thread is the only place its answer can
   * appear — `POST …/messages` returns before the reply is written, and this
   * screen must not go quiet in between (**B-044**).
   */
  const liveRunIsInThread =
    run !== null && messages.some((message) => message.run_id === run.id && message.role === "assistant");

  return (
    /* **One column.** Every child below is a direct child of this element, so
       the context strip, the question, the answer, the evidence and the
       composer are all exactly the same width. A left gutter for the agent's
       mark is what broke that before: it inset the prose while the composer
       stayed full width, and the mismatch was visible. */
    <div className={styles.page}>
      {conversation && (
        <div className={styles.contextStrip}>
          {conversation.data_source_name ? (
            <span className={styles.chip}>
              <span className={styles.chipDot} aria-hidden="true" />
              {conversation.data_source_name}
            </span>
          ) : (
            <span className={styles.chipWarn}>no database chosen</span>
          )}
          <span>started {when(conversation.created_at)}</span>
        </div>
      )}
      {conversation && !conversation.data_source_id && (
        <p className={styles.note}>
          This conversation is not tied to a database. If this organization has exactly one,
          questions here are answered against it; if it has several, they will be refused
          until you start a conversation that names one.
        </p>
      )}

      <ol className={styles.thread}>
        {messages.map((message) => {
          const answering = message.role === "assistant" ? runFor(message.run_id) : null;
          return (
            <li
              key={message.id}
              className={message.role === "user" ? styles.turnYou : styles.turnAgentRow}
            >
              {/* **An answer is its card, not a bubble beside one** (B-106).
                  Where the run is in hand the whole answer renders here — words,
                  chart, method, limitations, evidence and trace — so it stays
                  put when the next question is asked. The plain bubble is the
                  fallback for a run that could not be fetched: the words are
                  still the answer, and losing them because a second request
                  failed would be worse than losing the picture. */}
              {answering ? (
                <AnswerCard orgId={orgId} run={answering} answer={message.content} />
              ) : (
                <p className={message.role === "user" ? styles.bubble : styles.messageText}>
                  {message.content}
                </p>
              )}
            </li>
          );
        })}
      </ol>

      {live && run && <RunProgress orgId={orgId} run={run} />}
      {/* The live run's card, until its message joins the thread and the card
          above becomes the one that carries it. Without this a finished run is
          wordless for as long as the reload takes, which is the Phase 7 gate's
          own defect (**B-044**). */}
      {answered && run && !liveRunIsInThread && <AnswerCard orgId={orgId} run={run} />}

      <div className={styles.composerWrap}>
        <div className={styles.composerBox}>
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
          {sending && <Pending spinner={false}>Sending your question…</Pending>}
          {live && !sending && <span className={styles.step}>a question is already running</span>}
        </Row>
        {error && <p className={styles.failure}>{error}</p>}
        </div>
      </div>
    </div>
  );
}
