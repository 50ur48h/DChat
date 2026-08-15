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

  it("will not send an empty question", async () => {
    routeFetch();

    render(<ConversationThread orgId="o1" conversationId="c1" />);
    await screen.findByText("6,214 orders were placed in July 2026.");

    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });
});
