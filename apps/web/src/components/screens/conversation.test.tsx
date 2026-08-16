import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConversationThread } from "./conversation";

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

  it("opens the query behind a citation", async () => {
    routeFetch();

    render(<ConversationThread orgId="o1" conversationId="c1" />);

    // The product's central claim, made checkable rather than asserted: the
    // answer names a finding, the finding names an execution, and the execution
    // opens into the statement that produced the number.
    const toggle = await screen.findByRole("button", { name: /Show the query behind this/ });
    fireEvent.click(toggle);

    expect(await screen.findByText(/SELECT count\(\*\) AS "order_count"/)).toBeInTheDocument();
    expect(screen.getByText("6214")).toBeInTheDocument();
  });

  it("renders an honest refusal as an answer, not as a failure", async () => {
    routeFetch({
      run: { ...ANSWERED, findings: [] },
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

    // A run that could not answer *completes* (WP7.2b). Dressing that up as an
    // error would send people hunting for a bug in their question.
    expect(await screen.findByText(/more than one data source/)).toBeInTheDocument();
    expect(screen.getByText("answered")).toBeInTheDocument();
    expect(screen.getByText("no supporting query")).toBeInTheDocument();
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
});
