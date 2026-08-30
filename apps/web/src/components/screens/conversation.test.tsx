import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConversationThread, isNumeric, nearlyOut, progressLine } from "./conversation";

const session = {
  mode: "dev" as const,
  who: "alice",
  getToken: async () => "token",
  signIn: async () => undefined,
  signOut: async () => undefined,
  problems: [],
  busy: false,
  error: null,
};

vi.mock("@/lib/auth/session", () => ({
  useSession: () => session,
}));

const CONVERSATION = {
  id: "c1",
  title: "How many orders were placed in July 2026?",
  created_at: "2026-08-15T09:00:00Z",
  message_count: 2,
  last_run_id: "r1",
  data_source_id: "d1",
  data_source_name: "Pizza (PostgreSQL)",
};

const MESSAGES = [
  {
    id: "m1",
    role: "user",
    content: "How many orders were placed in July 2026?",
    run_id: "r1",
    created_at: "2026-08-15T09:00:00Z",
  },
  {
    id: "m2",
    role: "assistant",
    content: "6,214 orders were placed in July 2026.",
    run_id: "r1",
    created_at: "2026-08-15T09:00:20Z",
  },
];

const ANSWERED = {
  id: "r1",
  conversation_id: "c1",
  status: "completed",
  question: "How many orders were placed in July 2026?",
  answer: "6,214 orders were placed in July 2026.",
  findings: [
    {
      id: "f1",
      statement: "6,214 orders were placed in July 2026.",
      support: ["x1"],
      confidence: "high",
    },
  ],
  started_at: "2026-08-15T09:00:01Z",
  finished_at: "2026-08-15T09:00:20Z",
  failure_reason: null,
};

const EXECUTION = {
  id: "x1",
  run_id: "r1",
  status: "ok",
  sql: 'SELECT count(*) AS "order_count" FROM "public"."orders"',
  tables: ["public.orders"],
  columns: ["order_count"],
  row_count: 1,
  duration_ms: 31,
  violation_code: null,
  error: null,
  sensitive_accessed: false,
  masked_columns: [],
  sample_rows: [[6214]],
  truncated: false,
  created_at: "2026-08-15T09:00:10Z",
};

/** Comfortably longer than the screen's 1500ms poll, so a stray tick would land. */
const POLL_QUIET_MS = 2200;

/**
 * A response that arrives on a later task, the way a real one does.
 *
 * Not decoration. The bug this file's regression test covers is a race between
 * a `setState` and the `await` after it: React commits the update — and runs the
 * effect cleanup that cancels the in-flight tick — on a macrotask. A stub that
 * resolves in a microtask beats the commit and hides the bug entirely, which is
 * exactly what happened the first time this test was written: it passed against
 * the broken code. Real latency is what makes the ordering deterministic.
 */
function delayed(body: unknown, ms = 5): Promise<Response> {
  return new Promise((resolve) => setTimeout(() => resolve(json(body)), ms));
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/**
 * Routes on the URL rather than on call order.
 *
 * This screen polls, so the number of calls is not fixed and a sequence stub
 * would make every assertion depend on timing.
 */
function routeFetch(overrides: Partial<Record<string, unknown>> = {}) {
  const calls: { url: string; init: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init: RequestInit = {}) => {
      calls.push({ url, init });
      if (url.includes("/executions/")) return Promise.resolve(json(overrides.execution ?? EXECUTION));
      if (url.includes("/events")) {
        return Promise.resolve(json({ run_id: "r1", events: [], last_seq: 0 }));
      }
      if (url.includes("/runs/")) return Promise.resolve(json(overrides.run ?? ANSWERED));
      // The thread's runs (**B-106**). Defaults to the same run the messages
      // name, so a test that says nothing about it gets a thread whose answer
      // renders as its card — which is what the screen does now.
      if (url.endsWith("/runs")) {
        if ("runs" in overrides) return Promise.resolve(json(overrides.runs));
        return Promise.resolve(json([overrides.run ?? ANSWERED]));
      }
      if (url.includes("/messages")) {
        if (init.method === "POST") {
          return Promise.resolve(
            json({ run_id: "r1", message_id: "m1", status: "queued", created: true }),
          );
        }
        return Promise.resolve(json(overrides.messages ?? MESSAGES));
      }
      return Promise.resolve(json(overrides.conversation ?? CONVERSATION));
    }),
  );
  return calls;
}

describe("<ConversationThread />", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
  });

  it("shows the question, the answer and which database answered it", async () => {
    routeFetch();

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(await screen.findByText("6,214 orders were placed in July 2026.")).toBeInTheDocument();
    expect(screen.getByText("Pizza (PostgreSQL)")).toBeInTheDocument();
    expect(screen.getAllByText(/How many orders were placed in July 2026\?/).length).toBeGreaterThan(
      0,
    );
  });

  it("shows the query behind a lone citation without a second click", async () => {
    /**
     * The product's central claim, made checkable rather than asserted: the
     * answer names a finding, the finding names an execution, and the execution
     * opens into the statement that produced the number.
     *
     * **A finding with one citation shows it outright** (D-047). Having opened
     * *Evidence*, being asked to open the evidence again is a click that buys
     * nothing — so the toggle is still there, reading *Hide*, and the query is
     * already on screen.
     */
    routeFetch();

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(
      await screen.findByRole("button", { name: /Hide the query behind this/ }),
    ).toBeInTheDocument();
    expect(await screen.findByText(/SELECT count\(\*\) AS "order_count"/)).toBeInTheDocument();
    expect(screen.getByText("6214")).toBeInTheDocument();
  });

  it("renders an honest refusal as a refusal, and not as a failure", async () => {
    routeFetch({
      run: { ...ANSWERED, findings: [], state: "refused" },
      messages: [
        MESSAGES[0],
        {
          id: "m2",
          role: "assistant",
          content:
            "This organization has more than one data source and this conversation is not tied to one of them: Pizza (PostgreSQL), Pizza (SQL Server).",
          run_id: "r1",
          created_at: "2026-08-15T09:00:05Z",
        },
      ],
    });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    // A run that could not answer *completes* (WP7.2b), so dressing it up as an
    // error would send people hunting for a bug in their question — and calling it
    // **answered** was the opposite mistake (**B-133**). This assertion used to
    // read `getByText("answered")`, which is the defect written down as an
    // expectation: the card labelled every honest refusal with the one claim a
    // refusal exists to deny.
    expect(await screen.findByText(/more than one data source/)).toBeInTheDocument();
    expect(screen.getByText("could not answer")).toBeInTheDocument();
    expect(screen.queryByText("answered")).not.toBeInTheDocument();
    expect(screen.getByText("no supporting query")).toBeInTheDocument();
  });

  it("still says answered when the run answered", async () => {
    // **The control.** Without it the assertion above is satisfied by a card that
    // says "could not answer" on every run, which would be a worse defect than the
    // one being fixed — it would make the product look incapable.
    routeFetch({ run: { ...ANSWERED, state: "answered" } });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(await screen.findByText("answered")).toBeInTheDocument();
    expect(screen.queryByText("could not answer")).not.toBeInTheDocument();
  });

  it("names the missing half when a run answered only part of the question", async () => {
    // **The last hop of B-134's chain.** `unanswered` is written by the model
    // into `FinalizeIn` and crosses the composer, the ending, a column, RunView
    // and RunOut before it gets here. The API side of that is asserted in
    // `tests/agent/test_outcome_state.py`; this is the end a person actually
    // reads, and the reason the badge is not just a different word: "could not
    // answer the cost" is useful, "partly answered" alone is not.
    routeFetch({ run: { ...ANSWERED, state: "partly", unanswered: "the cost" } });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(await screen.findByText("could not answer the cost")).toBeInTheDocument();
    expect(screen.queryByText("answered")).not.toBeInTheDocument();
  });

  it("keeps the status word for a run that never recorded whether it answered", async () => {
    // Every run that ended before revision 0029 is this one. Guessing from the
    // absence of findings would put a word in the mouth of a run that never said
    // it, so `null` falls back to exactly what these runs showed before.
    routeFetch({ run: { ...ANSWERED, findings: [], state: null } });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(await screen.findByText("answered")).toBeInTheDocument();
  });

  it("says the platform failed when the platform failed", async () => {
    routeFetch({
      run: {
        ...ANSWERED,
        status: "failed",
        findings: [],
        failure_reason: "The run could not be completed.",
      },
    });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    // `failed` is reserved for the platform breaking, and is the one case where
    // the screen should say something went wrong on our side.
    expect(await screen.findByText("failed")).toBeInTheDocument();
    expect(screen.getByText(/The run could not be completed/)).toBeInTheDocument();
  });

  it("shows what a live run is doing instead of a bare spinner", async () => {
    routeFetch({ run: { ...ANSWERED, status: "running", answer: null, findings: [] } });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(await screen.findByText("working")).toBeInTheDocument();
    expect(screen.getByText(/The answer arrives here on its own/)).toBeInTheDocument();
  });

  it("sends a question with an idempotency key, so a double tap cannot bill twice", async () => {
    const calls = routeFetch();

    render(<ConversationThread orgId="o1" conversationId="c1" />);
    await screen.findByText("6,214 orders were placed in July 2026.");

    fireEvent.change(screen.getByLabelText("Ask a question"), {
      target: { value: "Which store sold most?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(calls.some((call) => call.init.method === "POST")).toBe(true));
    const post = calls.find((call) => call.init.method === "POST");
    const body = JSON.parse(String(post?.init.body)) as Record<string, string>;
    expect(body.content).toBe("Which store sold most?");
    // Required by the API, and the reason a retried send returns the run that
    // already exists rather than a second one under D-019's spend ceiling.
    expect(body.idempotency_key).toBeTruthy();
  });

  it("shows the answer as soon as the run finishes, with no further interaction", async () => {
    /**
     * The bug the Phase 7 gate found, and the reason it did not pass.
     *
     * `POST …/messages` answers 202 with a run id and **no answer** — the reply
     * becomes a message only when the run finishes. So the screen must re-fetch
     * the thread at that moment. It did not: the poll wrote the finished run
     * into state, that write cancelled the very tick that was meant to reload
     * the messages, and the effect then saw a terminal run and stopped. The
     * answer text arrived only on the *next* fetch something else triggered —
     * so every reply rendered one message behind, and the user saw a card with
     * a citation and no words.
     *
     * Modelled the way the API really behaves, which is what makes this fail
     * without the fix: the assistant message does not exist while the run is
     * running, and `send()`'s own reload therefore cannot pick it up.
     */
    let runCalls = 0;
    let answered = false;
    const calls: { url: string; init: RequestInit }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init: RequestInit = {}) => {
        calls.push({ url, init });
        if (url.includes("/events")) return delayed({ run_id: "r1", events: [], last_seq: 0 });
        if (url.includes("/runs/")) {
          runCalls += 1;
          // Running first, finished afterwards — the transition is the thing.
          if (runCalls === 1) {
            return delayed({ ...ANSWERED, status: "running", answer: null, findings: [] });
          }
          answered = true;
          return delayed(ANSWERED);
        }
        if (url.includes("/messages")) {
          if (init.method === "POST") {
            return delayed({ run_id: "r1", message_id: "m1", status: "queued", created: true });
          }
          // The reply is written by the run, so it does not exist until the run
          // has finished. This is what stops `send()`'s own reload from picking
          // it up, and therefore what makes the test bite.
          return delayed(answered ? MESSAGES : [MESSAGES[0]]);
        }
        return delayed({ ...CONVERSATION, last_run_id: null, message_count: 0 });
      }),
    );

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    fireEvent.change(await screen.findByLabelText("Ask a question"), {
      target: { value: "How many orders were placed in July 2026?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    // No second question, no click, no refresh: the answer must simply appear.
    expect(
      await screen.findByText("6,214 orders were placed in July 2026.", undefined, {
        timeout: 4000,
      }),
    ).toBeInTheDocument();
  });

  it("is never a wordless answer, even if the thread has not caught up", async () => {
    /**
     * Belt to the fix above's braces. The run carries its own answer, so if the
     * message list is empty — a slow reload, a failed one — the card says what
     * the answer was rather than showing a confidence badge and a citation with
     * no words, which is exactly what the gate saw.
     */
    routeFetch({ messages: [] });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(await screen.findByText("6,214 orders were placed in July 2026.")).toBeInTheDocument();
  });

  it("does not print the answer twice once the reply is in the thread", async () => {
    /** The other half: with the reply present, the card must not restate it. */
    routeFetch();

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(
      await screen.findAllByText("6,214 orders were placed in July 2026."),
    ).toHaveLength(1);
  });

  it("stops polling once the run has finished", async () => {
    /** A page left open must not ask forever — and the fix moved the guard that
     * stops it, so it is worth holding down. */
    const calls = routeFetch();

    render(<ConversationThread orgId="o1" conversationId="c1" />);
    await screen.findByText("6,214 orders were placed in July 2026.");

    // Let the opening fetches land before counting. Since **B-106** the answer
    // renders from the thread's own runs, which can resolve before the live
    // run's fetch does — so the text appearing no longer means every opening
    // request is in. The property is that nothing *keeps* asking, and this is
    // the honest way to snapshot it.
    await new Promise((resolve) => setTimeout(resolve, POLL_QUIET_MS));
    const settled = calls.filter((call) => call.url.includes("/runs/")).length;
    await new Promise((resolve) => setTimeout(resolve, POLL_QUIET_MS));

    expect(calls.filter((call) => call.url.includes("/runs/")).length).toBe(settled);
  });

  it("will not send an empty question", async () => {
    routeFetch();

    render(<ConversationThread orgId="o1" conversationId="c1" />);
    await screen.findByText("6,214 orders were placed in July 2026.");

    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });
});

describe("what the answer does not establish", () => {
  it("shows the limitations beside the answer", async () => {
    routeFetch({
      run: {
        ...ANSWERED,
        limitations: [
          "The investigation stopped before it was finished: I reached the maximum number of research steps for one question.",
          "9,999.00 appears in no result this run returned.",
        ],
      },
    });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(await screen.findByText(/2 things to know/i)).toBeInTheDocument();
    expect(screen.getByText(/maximum number of research steps/i)).toBeInTheDocument();
    expect(screen.getByText(/appears in no result/i)).toBeInTheDocument();
  });

  it("says nothing at all when there is nothing to say", async () => {
    // A clean run should not be made to sound uncertain. An empty list is a
    // result, not a gap, so the section is absent rather than empty.
    routeFetch({ run: { ...ANSWERED, limitations: [] } });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    await screen.findByText(/answered/i);
    expect(screen.queryByText(/things to know/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/one thing to know/i)).not.toBeInTheDocument();
  });

  it("counts one limitation in the singular", async () => {
    routeFetch({ run: { ...ANSWERED, limitations: ["Only one query returned rows."] } });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(await screen.findByText(/one thing to know/i)).toBeInTheDocument();
  });
});

describe("how the answer was reached", () => {
  // **B-100.** Architecture 4.2's fourth part of an answer. The API built this
  // sentence on every run from Phase 9 and no screen ever showed it, so the one
  // line meant for a reader who will not open the SQL was the one nobody saw.
  it("shows the method line with the answer", async () => {
    routeFetch({ run: { ...ANSWERED, method: "2 queries over 2 steps, against orders." } });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(await screen.findByText(/2 queries over 2 steps, against orders/i)).toBeInTheDocument();
  });

  it("says nothing when the run recorded no method", async () => {
    // A run answered before the column existed, or one that never composed.
    // Absent rather than an empty label, which would be a heading over nothing.
    routeFetch({ run: { ...ANSWERED, method: "" } });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    await screen.findByText(/answered/i);
    expect(screen.queryByText(/^How:/)).not.toBeInTheDocument();
  });
});

// jsdom has no canvas, so the real vega would try to draw, fail asynchronously
// and resolve after the assertions — which made this file flake once in a full
// run. What these tests claim is that the card offers the spec and renders the
// refusal, not that vega draws; the drawing is what the browser smoke is for.
vi.mock("vega-embed", () => ({ default: vi.fn(async () => ({})) }));

describe("the chart, and the reason there is none", () => {
  const SPEC = {
    $schema: "https://vega.github.io/schema/vega-lite/v5.json",
    mark: "bar",
    encoding: {
      x: { field: "month", type: "temporal" },
      y: { field: "revenue", type: "quantitative" },
    },
    data: { values: [{ month: "2026-07-01", revenue: 6214 }] },
  };

  it("gives vega a categorical range, so a split is not eight of one colour", async () => {
    /**
     * **B-109.** Every chart was `--primary` and nothing else, and the model
     * apologised in the answer for a colour setting it had no field to ask for.
     * The range is read from the tokens like the rest of the theme, because a
     * palette written into a spec is the same bug design.md names for a colour
     * written into a component.
     *
     * Asserted through the embed call rather than on the CSS, since what matters
     * is that vega is *given* the range — jsdom resolves no custom properties,
     * so the fallback is what renders here and the shape is the property.
     */
    const embed = (await import("vega-embed")).default as unknown as ReturnType<typeof vi.fn>;
    embed.mockClear();
    routeFetch({ run: { ...ANSWERED, limitations: [], chart: { spec: SPEC } } });

    render(<ConversationThread orgId="o1" conversationId="c1" />);
    await screen.findByTestId("chart");

    const config = embed.mock.calls[0]?.[2] as { config?: { range?: { category?: string[] } } };
    const category = config?.config?.range?.category ?? [];
    expect(category).toHaveLength(8);
    // Eight distinct hues, in a fixed order. A palette that had quietly become
    // eight copies of the primary would pass a length check and nothing else.
    expect(new Set(category).size).toBe(8);
  });

  it("says why there is no chart, where the chart would have been", async () => {
    // **The case the whole design turned on.** A picture that silently fails to
    // appear is indistinguishable from a broken page, so the reason renders in
    // the chart's own place — not in the limitations list, which is about
    // whether the answer is true.
    routeFetch({
      run: {
        ...ANSWERED,
        limitations: [],
        chart: {
          declined:
            "No chart was drawn: 'ingredient' has 4,812 distinct values and a chart here shows at most 50. The table has all of them.",
          code: "too_many_categories",
        },
      },
    });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(await screen.findByText(/4,812 distinct values/)).toBeInTheDocument();
    // And it did not become a limitation: that region is about the truth of the
    // answer, and a missing picture says nothing about that.
    expect(screen.queryByText(/things to know/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/one thing to know/i)).not.toBeInTheDocument();
  });

  it("offers the spec, the way the SQL is offered", async () => {
    // **B-048.** A chart nobody can trace back to the query behind it is
    // decoration that looks like evidence, so the document the browser rendered
    // is openable — the same claim Phase 7 made for answers, extended to
    // pictures.
    routeFetch({ run: { ...ANSWERED, limitations: [], chart: { spec: SPEC } } });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Chart spec" }));

    expect(await screen.findByText(/"mark": "bar"/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hide chart spec" })).toBeInTheDocument();
  });

  it("shows nothing at all when no chart was asked for", async () => {
    // Most answers. No empty frame and no placeholder: those are what make an
    // absent chart look like a broken one.
    routeFetch({ run: { ...ANSWERED, limitations: [], chart: null } });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    await screen.findByText(/answered/i);
    expect(screen.queryByRole("button", { name: "Chart spec" })).not.toBeInTheDocument();
    expect(screen.queryByText(/No chart was drawn/)).not.toBeInTheDocument();
  });

  it("renders a run recorded before charts existed", async () => {
    // `chart` is absent rather than null on an older run, and an answer card
    // that threw on one would lose the whole answer over a missing picture.
    routeFetch({ run: { ...ANSWERED, limitations: [] } });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(await screen.findByText(/6,214 orders/)).toBeInTheDocument();
  });
});

describe("which findings are evidence", () => {
  it("shows only the findings the answer rests on", async () => {
    routeFetch({
      run: {
        ...ANSWERED,
        answer: "Revenue fell because of delivery at one store.",
        findings: [
          { id: "f1", statement: "Revenue fell 12%.", support: ["x1"], confidence: "high", cited: true },
          { id: "f2", statement: "A step along the way.", support: ["x2"], confidence: "low", cited: false },
        ],
      },
    });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(await screen.findByText("Revenue fell 12%.")).toBeInTheDocument();
    // The uncited one is the investigation's working and lives in the trace.
    expect(screen.queryByText("A step along the way.")).not.toBeInTheDocument();
  });

  it("falls back to showing every finding when none is marked", async () => {
    // Runs written before WP9.2 have no `cited` on any finding. Filtering them
    // all away would empty the evidence panel of every older answer.
    routeFetch({
      run: {
        ...ANSWERED,
        answer: "Two things happened.",
        findings: [
          { id: "f1", statement: "The first thing.", support: ["x1"], confidence: "high" },
          { id: "f2", statement: "The second thing.", support: ["x1"], confidence: "high" },
        ],
      },
    });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(await screen.findByText("The first thing.")).toBeInTheDocument();
    expect(screen.getByText("The second thing.")).toBeInTheDocument();
  });

  it("says which definitions governed the answer (B-087)", async () => {
    routeFetch({
      run: { ...ANSWERED, definitions_applied: ["prep_quantity"], definitions_available: 18 },
    });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(await screen.findByText(/governed by prep_quantity/)).toBeInTheDocument();
  });

  it("says when a question matched none of the definitions that exist (B-087)", async () => {
    // The sentence three gate walks needed and never got. Without it a question
    // that named no metric answers exactly as it would with no semantic layer at
    // all, and reads as the feature being broken rather than as the metric not
    // being found.
    routeFetch({ run: { ...ANSWERED, definitions_applied: [], definitions_available: 18 } });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(await screen.findByText(/no definition matched this question/)).toBeInTheDocument();
    expect(screen.getByText(/18 defined here/)).toBeInTheDocument();
  });

  it("stays silent when there was nothing to match (B-087)", async () => {
    // The case that keeps the line worth reading. An organization that has
    // defined no metrics does not need telling that none applied, and a caveat
    // on every answer is how people learn to stop reading caveats.
    routeFetch({ run: { ...ANSWERED, definitions_applied: [], definitions_available: 0 } });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    await screen.findByText(/6,214 orders/);
    expect(screen.queryByText(/no definition matched/)).not.toBeInTheDocument();
    expect(screen.queryByText(/governed by/)).not.toBeInTheDocument();
  });

  it("renders a run recorded before these fields existed (B-087)", async () => {
    // Old rows carry neither field. The page whose whole job is explaining what
    // happened must not be the page that breaks.
    routeFetch({ run: ANSWERED });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(await screen.findByText(/6,214 orders/)).toBeInTheDocument();
    expect(screen.queryByText(/no definition matched/)).not.toBeInTheDocument();
  });
});


// ---------------------------------------------------------------------------
// An answer keeps its evidence when the next question is asked (B-106)
// ---------------------------------------------------------------------------

/** The chart the first answer carries. Its own copy, because the one in the
 *  chart suite is scoped to that describe and this is a different property. */
const FIRST_CHART = {
  $schema: "https://vega.github.io/schema/vega-lite/v5.json",
  mark: "bar",
  encoding: {
    x: { field: "month", type: "temporal", timeUnit: "yearmonth" },
    y: { field: "revenue", type: "quantitative" },
  },
  data: { values: [{ month: "2026-07-01", revenue: 6214 }] },
};

/** A second turn in the same thread, so there is an older answer to lose. */
const TWO_TURNS = [
  MESSAGES[0],
  MESSAGES[1],
  {
    id: "m3",
    role: "user",
    content: "and in August?",
    run_id: "r2",
    created_at: "2026-08-15T09:01:00Z",
  },
  {
    id: "m4",
    role: "assistant",
    content: "5,004 orders were placed in August 2026.",
    run_id: "r2",
    created_at: "2026-08-15T09:01:20Z",
  },
];

/** After a second question, the conversation's last run is the second one — so
 *  the live run the screen watches is r2 and r1 comes from the thread's list.
 *  Getting this wrong made the first version of these tests assert against a
 *  fixture that contradicted itself. */
const AFTER_TWO = { ...CONVERSATION, last_run_id: "r2", message_count: 4 };

const SECOND_RUN = {
  ...ANSWERED,
  id: "r2",
  question: "and in August?",
  answer: "5,004 orders were placed in August 2026.",
  findings: [
    { id: "f2", statement: "5,004 orders were placed in August 2026.", support: ["x1"], confidence: "high" },
  ],
};

describe("an answer keeps its evidence when the next question is asked (B-106)", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
  });

  it("draws the first answer's chart after a second question has been answered", async () => {
    /**
     * **The gate walk's defect.** The screen held one run and rendered one card,
     * so asking again took the previous answer's chart off the page — along with
     * its method line, its limitations, its findings, its evidence controls and
     * its trace. Every one of those is a durable row; none of them had a route.
     *
     * A chart that survives only until the next message does not meet "the
     * trend question renders a chart", which is why this blocked the phase gate
     * rather than waiting for Phase 12.
     */
    routeFetch({
      conversation: AFTER_TWO,
      run: SECOND_RUN,
      messages: TWO_TURNS,
      runs: [{ ...ANSWERED, chart: { spec: FIRST_CHART } }, SECOND_RUN],
    });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    // Both answers are on the screen...
    expect(await screen.findByText("6,214 orders were placed in July 2026.")).toBeInTheDocument();
    expect(screen.getByText("5,004 orders were placed in August 2026.")).toBeInTheDocument();
    // ...and the older one still has its picture.
    expect(await screen.findByTestId("chart")).toBeInTheDocument();
  });

  it("keeps the evidence control on every answer, not just the newest", async () => {
    routeFetch({ conversation: AFTER_TWO, run: SECOND_RUN, messages: TWO_TURNS, runs: [ANSWERED, SECOND_RUN] });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    // One per answer. Anchored on the count rather than on presence, because
    // presence passed against the broken screen — the newest answer always had
    // one.
    // `the query behind this`, not `Show …`: a lone citation now starts open and
    // its control reads *Hide* (D-047). Matching only "Show" would count zero
    // and report the product as broken when it is not.
    expect(
      await screen.findAllByRole("button", { name: /the query behind this/ }),
    ).toHaveLength(2);
  });

  it("asks for the thread's runs once, not once per answer", async () => {
    /** A screen whose cost grows with how much somebody has used it is one that
     *  gets slower the more they like it. */
    const calls = routeFetch({ conversation: AFTER_TWO, run: SECOND_RUN, messages: TWO_TURNS, runs: [ANSWERED, SECOND_RUN] });

    render(<ConversationThread orgId="o1" conversationId="c1" />);
    expect(await screen.findByText("5,004 orders were placed in August 2026.")).toBeInTheDocument();

    expect(calls.filter((call) => call.url.endsWith("/runs")).length).toBe(1);
  });

  it("keeps the whole thread when the runs request fails outright", async () => {
    /**
     * **What CI caught and the unit tests did not.** The runs began life in the
     * same `Promise.all` and the same `catch` as the conversation and its
     * messages, so a failure there emptied the screen — questions, answers and
     * all. The test above covers a run *missing from the list*; this covers the
     * request not answering, which is the case that took the thread down.
     *
     * The runs enrich the answers. The messages **are** the conversation.
     */
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init: RequestInit = {}) => {
        if (url.endsWith("/runs")) return Promise.resolve(json({ nope: true }, 500));
        if (url.includes("/executions/")) return Promise.resolve(json(EXECUTION));
        if (url.includes("/events")) {
          return Promise.resolve(json({ run_id: "r1", events: [], last_seq: 0 }));
        }
        if (url.includes("/runs/")) return Promise.resolve(json(ANSWERED));
        if (url.includes("/messages")) {
          if (init.method === "POST") {
            return Promise.resolve(
              json({ run_id: "r1", message_id: "m1", status: "queued", created: true }),
            );
          }
          return Promise.resolve(json(TWO_TURNS));
        }
        return Promise.resolve(json(AFTER_TWO));
      }),
    );

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(await screen.findByText("6,214 orders were placed in July 2026.")).toBeInTheDocument();
    expect(screen.getByText("5,004 orders were placed in August 2026.")).toBeInTheDocument();
    expect(screen.getByText("How many orders were placed in July 2026?")).toBeInTheDocument();
  });

  it("falls back to the words when a run could not be fetched", async () => {
    /** The answer is what the reader came for. Losing it because a second
     *  request failed would be a worse trade than losing the picture. */
    routeFetch({ conversation: AFTER_TWO, run: SECOND_RUN, messages: TWO_TURNS, runs: [SECOND_RUN] });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(await screen.findByText("6,214 orders were placed in July 2026.")).toBeInTheDocument();
  });

  it("shows an answer once, not once in a bubble and once in a card", async () => {
    /** The Phase 7 rule, and the reason `replied` used to exist. With the card
     *  as the turn there is one rendering of an answer and nothing to keep in
     *  step. */
    routeFetch({ conversation: AFTER_TWO, run: SECOND_RUN, messages: TWO_TURNS, runs: [ANSWERED, SECOND_RUN] });

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(await screen.findByText("6,214 orders were placed in July 2026.")).toBeInTheDocument();
    expect(screen.getAllByText("6,214 orders were placed in July 2026.")).toHaveLength(1);
  });
});

/**
 * What the question cost (B-153).
 *
 * **The columns behind this existed since revision 0012 and nothing wrote
 * them** — `model_usage`'s own comment called it "a rollup for the trace UI"
 * and the rollup was never built, so the API returned `null` and `{}` and no
 * screen could show what a run cost. These assert the half a person reads, and
 * particularly the case where an honest absence beats a tidy number.
 */
describe("how a table cell is aligned", () => {
  it("right-aligns numbers and money, and nothing else", () => {
    // **B-179.** The first version right-aligned every column but the first, on
    // the assumption that "every number here is money or a count". A `Period`
    // column and a `Why it matters` column arrived ragged against the right
    // edge, which is how the owner met it.
    for (const numeric of ["310817.09", "RM 310,817", "1,973 kg".replace(" kg", ""), "42", "-12.75", "87%", "RM 20,469.38"]) {
      expect(isNumeric(numeric)).toBe(true);
    }
  });

  it("leaves anything it cannot be sure about on the left", () => {
    // A wrongly right-aligned word is the fault this replaces, so doubt goes
    // left. "2025-12" is a period, not a number.
    for (const words of [
      "Ayam Penyet+Nasi+Sup",
      "2025-12",
      "Annual undated view",
      "This is the gap between Outlet A and Outlet D",
      "",
      "  ",
      "1,973 kg",
    ]) {
      expect(isNumeric(words)).toBe(false);
    }
  });

  it("reads a number out of nested markup", () => {
    // Money is bolded by the answer rule, so the cell holds an element rather
    // than a string and a naive check would call every bold figure prose.
    expect(isNumeric(["RM 310,817"])).toBe(true);
  });
});

describe("how far through its allowance a run is", () => {
  const withProgress = (used: object, limits: object) =>
    ({ ...ANSWERED, progress: { used, limits } }) as unknown as Parameters<typeof progressLine>[0];

  it("counts steps and time, and predicts nothing", () => {
    // **B-177.** A bar claims to know when a run finishes. What finishes a run
    // is a model deciding it has enough, and nothing here can know that.
    const run = withProgress(
      { iterations: 6, wall_seconds: 210 },
      { iterations: 12, wall_seconds: 330 },
    );

    expect(progressLine(run)).toBe("step 6 of 12 · 3:30 of 5:30");
  });

  it("says nothing at all before the run has done anything", () => {
    // "step 0 of 12" while the catalog is still being searched measures the
    // wrong thing, and an empty strip is honest.
    expect(progressLine(ANSWERED as unknown as Parameters<typeof progressLine>[0])).toBeNull();
  });

  it("warns when a ceiling is close, before the answer arrives", () => {
    // Saying it early is the feature: a reader who knows the search was cut
    // short reads the caveats differently.
    const close = withProgress(
      { iterations: 11, wall_seconds: 200 },
      { iterations: 12, wall_seconds: 330 },
    );
    const early = withProgress(
      { iterations: 2, wall_seconds: 40 },
      { iterations: 12, wall_seconds: 330 },
    );

    expect(nearlyOut(close)).toBe(true);
    expect(nearlyOut(early)).toBe(false);
  });

  it("is not confused by a run that reports one dimension and not the other", () => {
    const partial = withProgress({ iterations: 3 }, { iterations: 12 });

    expect(progressLine(partial)).toBe("step 3 of 12");
    expect(nearlyOut(partial)).toBe(false);
  });
});

describe("how an answer is rendered", () => {
  it("shows headings, lists and tables as structure rather than as characters", async () => {
    // **B-172.** This rendered into a bare `<p>` with `white-space: pre-wrap`,
    // so a heading arrived as a literal `##` and a ranked list as a column of
    // asterisks. The only sane instruction against that renderer was "plain
    // words", which is why nine ranked products came back as one paragraph.
    const markdown = [
      "## Products with high sales",
      "",
      "* **Sup Buntut** - RM737.68",
      "* **Peha** - RM368.97",
      "",
      "| Product | Waste RM |",
      "| --- | --- |",
      "| Peha | 64.59 |",
    ].join("\n");
    // The card renders the *message* where one exists — the run's own answer is
    // the fallback for the window before the message lands (B-044). Overriding
    // only the run would have tested the fallback and left the live path alone.
    routeFetch({
      run: { ...ANSWERED, answer: markdown },
      messages: [MESSAGES[0], { ...MESSAGES[1], content: markdown }],
    });
    render(<ConversationThread orgId="o1" conversationId="c1" />);

    const answer = await screen.findByTestId("answer-text");
    expect(answer.querySelector("h2")?.textContent).toBe("Products with high sales");
    expect(answer.querySelectorAll("li")).toHaveLength(2);
    expect(answer.querySelector("strong")?.textContent).toBe("Sup Buntut");
    // The table needs remark-gfm; without it this is four lines of pipes.
    expect(answer.querySelector("table")).not.toBeNull();
    expect(answer.querySelector("th")?.textContent).toBe("Product");
    // And the markup must not leak through as text.
    expect(answer.textContent).not.toContain("##");
    expect(answer.textContent).not.toContain("| --- |");
  });

  it("escapes HTML in an answer rather than rendering it", async () => {
    // The answer is written by a model that has just read customer rows. If raw
    // HTML reached the DOM the answer would be a delivery mechanism, so
    // `rehype-raw` is deliberately not installed — this is the test that says
    // so out loud, and it goes red the day somebody adds it for convenience.
    const hostile = 'Sales rose. <img src=x onerror="alert(1)"> <script>alert(2)</script> Done.';
    routeFetch({
      run: { ...ANSWERED, answer: hostile },
      messages: [MESSAGES[0], { ...MESSAGES[1], content: hostile }],
    });
    render(<ConversationThread orgId="o1" conversationId="c1" />);

    const answer = await screen.findByTestId("answer-text");
    expect(answer.querySelector("img")).toBeNull();
    expect(answer.querySelector("script")).toBeNull();
    expect(answer.textContent).toContain("Sales rose.");
  });

  it("still renders an answer that carries no markdown at all", async () => {
    // Most answers are a sentence. Turning the renderer on must not require
    // every answer to be a document.
    routeFetch({ run: ANSWERED });
    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(await screen.findByText(/6,214 orders were placed in July 2026/)).toBeInTheDocument();
  });
});

describe("what the run cost", () => {
  const priced = { ...ANSWERED, cost_estimate: "0.0195", model_usage: { calls: 3, input_tokens: 1700, output_tokens: 170, unpriced_calls: 0, by_model: [] } };

  it("shows the total and where it went", async () => {
    routeFetch({ run: priced });
    render(<ConversationThread orgId="o1" conversationId="c1" />);

    const cost = await screen.findByTestId("run-cost");
    // Two decimals at or above a cent, four below it: $0.0195 reads as $0.02,
    // which rounds *up* and so cannot understate. Four decimals everywhere would
    // put trailing noise on every ordinary run.
    expect(cost).toHaveTextContent("$0.02");
    expect(cost).toHaveTextContent("3 model calls");
    expect(cost).toHaveTextContent("1,870 tokens");
  });

  it("says 'not priced' rather than a number that understates", async () => {
    // The rule both columns were born with: null means unpriced, never free. A
    // run with four fifths of its calls priced is exactly when a total is most
    // tempting and most misleading, because it looks complete.
    routeFetch({
      run: {
        ...ANSWERED,
        cost_estimate: null,
        model_usage: { calls: 5, input_tokens: 10, output_tokens: 2, unpriced_calls: 1, by_model: [] },
      },
    });
    render(<ConversationThread orgId="o1" conversationId="c1" />);

    const cost = await screen.findByTestId("run-cost");
    expect(cost).toHaveTextContent("not priced");
    // The absence stays informative: it says how many calls could not be priced
    // rather than leaving the reader to guess whether the run was free.
    expect(cost).toHaveTextContent("1 not priced");
    expect(cost.textContent).not.toMatch(/\$/);
  });

  it("never renders a sub-cent run as free", async () => {
    // `agent_runs.cost_estimate` stores four decimal places while the ledger
    // prices each call at six, so a very cheap run rounds to 0.0000. "$0.0000"
    // would read as free, which is the one thing this column promises never to
    // say.
    routeFetch({
      run: {
        ...ANSWERED,
        cost_estimate: "0.0000",
        model_usage: { calls: 1, input_tokens: 8, output_tokens: 1, unpriced_calls: 0, by_model: [] },
      },
    });
    render(<ConversationThread orgId="o1" conversationId="c1" />);

    const cost = await screen.findByTestId("run-cost");
    expect(cost).toHaveTextContent("less than $0.0001");
    expect(cost.textContent).not.toContain("$0.0000");
  });

  it("says how much of the input was cached, because the total does not discount it", async () => {
    // **Revision 0034.** `cost_estimate` prices the whole input at the full
    // rate while the provider bills the cached part at less, so a reader
    // checking this against an invoice needs to see the cached share rather
    // than having to guess why the two disagree.
    routeFetch({
      run: {
        ...priced,
        model_usage: { ...priced.model_usage, cached_input_tokens: 1200 },
      },
    });
    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(await screen.findByTestId("run-cost")).toHaveTextContent("1,200 cached");
  });

  it("says when the token counts are our arithmetic rather than the provider's", async () => {
    // Estimated tokens are priced as if measured. A total that mixes the two
    // and does not say which is the silent-mixing shape, so the line says it.
    routeFetch({
      run: { ...priced, model_usage: { ...priced.model_usage, estimated_calls: 2 } },
    });
    render(<ConversationThread orgId="o1" conversationId="c1" />);

    expect(await screen.findByTestId("run-cost")).toHaveTextContent("2 estimated");
  });

  it("renders nothing when the organization has spend switched off (D-066)", async () => {
    // The API withholds `cost_estimate` and `model_usage` rather than the
    // screen declining to draw them, so what arrives is indistinguishable from
    // a run that recorded no usage — which this component already handled. That
    // is the whole reason the switch needed no web change, and this is the
    // assertion that keeps it true.
    routeFetch({ run: { ...ANSWERED, cost_estimate: null, model_usage: {} } });
    render(<ConversationThread orgId="o1" conversationId="c1" />);

    await screen.findByText(/6,214 orders/);
    expect(screen.queryByTestId("run-cost")).toBeNull();
  });

  it("shows nothing at all for a run that recorded no usage", async () => {
    // Older runs, and runs that ended before a model was called. An empty strip
    // is honest; "$0.00" would not be.
    routeFetch({ run: { ...ANSWERED, cost_estimate: null, model_usage: {} } });
    render(<ConversationThread orgId="o1" conversationId="c1" />);

    await screen.findByText(/6,214 orders/);
    expect(screen.queryByTestId("run-cost")).toBeNull();
  });
});
