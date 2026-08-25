/**
 * A stand-in for the data-agent API, for the browser tests.
 *
 * Not a mock of the client — a real HTTP server the real app talks to over a
 * real socket, so what is exercised is the built page in a real browser with
 * real network timing. That distinction is the whole point: the jsdom test for
 * **B-044** passed against the broken code, because a stubbed `fetch` that
 * resolves in a microtask beats React's commit and hides the race entirely.
 *
 * Two things it models faithfully, because the bug lives in both:
 *
 *   1. **`POST …/messages` returns 202 with a run id and no answer.** The reply
 *      becomes a `messages` row only when the run finishes, so a screen that
 *      does not re-read the thread at that moment shows a wordless answer.
 *   2. **A run is `running` before it is `completed`.** The transition is what
 *      the poll has to notice, and answering `completed` immediately would skip
 *      the very state machine under test.
 *
 * Every response is delayed a little, the way a real one is.
 */

import { createServer, type Server } from "node:http";

const ORG = "11111111-1111-1111-1111-111111111111";
const CONVERSATION = "22222222-2222-2222-2222-222222222222";
const RUN = "33333333-3333-3333-3333-333333333333";
const EXECUTION = "44444444-4444-4444-4444-444444444444";

/**
 * The chart the answer carries, exactly as `agent/charts.py` assembles one.
 *
 * Inline values and no URL, because that is the property the server guarantees
 * by construction — and a stub that served a URL would be testing a document
 * the product cannot produce.
 */
export const CHART_SPEC = {
  $schema: "https://vega.github.io/schema/vega-lite/v5.json",
  mark: "line",
  encoding: {
    x: { field: "order_month", type: "temporal" },
    y: { field: "order_count", type: "quantitative" },
  },
  data: {
    values: [
      { order_month: "2026-05-01", order_count: 3624 },
      { order_month: "2026-06-01", order_count: 4125 },
      { order_month: "2026-07-01", order_count: 3857 },
    ],
  },
};

export const ANSWER = "3,718 orders were placed in July 2026.";
export const QUESTION = "How many orders were placed in July 2026?";

export const ids = { org: ORG, conversation: CONVERSATION, run: RUN, execution: EXECUTION };

/** How many polls a run stays `running` before it finishes. */
const RUNNING_POLLS = 2;

/** Latency on every response. Small, but not zero — zero is what hid B-044. */
const LATENCY_MS = 25;

export interface StubApi {
  url: string;
  close: () => Promise<void>;
  /**
   * Back to "nothing has been asked yet", without rebinding the port.
   *
   * One server for the whole file, reset between tests: the browser holds
   * keep-alive connections, so `close()` does not free a fixed port promptly and
   * the next test's `listen` loses it — which shows up as a page that never
   * signs in, several tests later and nowhere near the cause.
   */
  reset: () => void;
  /** Requests seen, for asserting what the screen did and did not ask for. */
  readonly calls: string[];
}

/**
 * Fixed, because `NEXT_PUBLIC_API_URL` is inlined when the dev server starts and
 * therefore has to be known before any test runs. Chosen well away from the
 * compose stack's 3000/8000 so a developer's running containers are untouched.
 */
export const STUB_PORT = 4111;

/**
 * One server per worker process, shared by every spec file in it.
 *
 * **Two files each starting their own on a fixed port is the failure this
 * module's header already warned about**, and WP13.1b walked straight into it:
 * adding `shell.spec.ts` beside `conversation.spec.ts` meant the first file's
 * `close()` raced the second file's `listen()`, the browser's keep-alive
 * connections kept the port from freeing promptly, and the loser showed up as a
 * page that never signed in — **in a different test on every run**, which is
 * what made it read as flakiness rather than as a bug.
 *
 * So `startStubApi` hands back the running instance if there is one, and
 * `close()` only really closes when the last holder has let go. Every caller
 * keeps its existing `beforeAll`/`afterAll` and none of them has to know.
 */
let shared: Promise<StubApi> | null = null;

export async function startStubApi(port: number = STUB_PORT): Promise<StubApi> {
  shared ??= bindStubApi(port).then((api) => ({
    ...api,
    calls: api.calls,
    /**
     * Deliberately does not close.
     *
     * Reference counting was tried and does not help: spec files run one after
     * another, so the first file's `afterAll` fires *before* the second file's
     * `beforeAll` and the count reaches zero between them — which is the same
     * unbind/rebind race by a longer route. The server instead lives as long as
     * the worker process, and the operating system reclaims the port when that
     * exits. Nothing leaks beyond the run.
     */
    close: async () => {},
  }));
  return shared;
}

/**
 * Bind, retrying briefly on a port that has not been released yet.
 *
 * **Playwright restarts a worker after a test fails**, and the replacement can
 * reach `listen` while the previous process's socket is still in `TIME_WAIT`.
 * Without this, one genuine failure turns into a cascade of unrelated ones in
 * the next file — which is precisely the "nowhere near the cause" symptom this
 * module's header warns about.
 */
async function bindWithRetry(server: Server, port: number): Promise<void> {
  const deadline = Date.now() + 10_000;
  for (;;) {
    try {
      await new Promise<void>((resolve, reject) => {
        const onError = (error: NodeJS.ErrnoException) => reject(error);
        server.once("error", onError);
        server.listen(port, "127.0.0.1", () => {
          server.removeListener("error", onError);
          resolve();
        });
      });
      return;
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (code !== "EADDRINUSE" || Date.now() > deadline) throw error;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
}

async function bindStubApi(port: number): Promise<StubApi> {
  let asked = false;
  let runPolls = 0;
  const calls: string[] = [];

  const finished = () => asked && runPolls > RUNNING_POLLS;

  const server: Server = createServer((request, response) => {
    const url = request.url ?? "";
    const method = request.method ?? "GET";
    calls.push(`${method} ${url.split("?")[0]}`);

    const send = (status: number, body: unknown) => {
      const payload = JSON.stringify(body);
      setTimeout(() => {
        // A page closed mid-flight leaves a destroyed socket; writing to it
        // would throw inside a timer, where nothing is there to catch it.
        if (response.writableEnded || response.destroyed) return;
        response.writeHead(status, {
          "content-type": "application/json",
          // The browser talks to a different origin than the page (arch 3.1).
          "access-control-allow-origin": "*",
          "access-control-allow-headers": "authorization,content-type,accept",
          "access-control-allow-methods": "GET,POST,PATCH,DELETE,OPTIONS",
          // No keep-alive. Each test opens fresh pages against this one
          // long-lived server, and sockets held open by pages that have since
          // closed accumulate until the browser's per-host connection limit is
          // reached — after which a later test simply never signs in, failing
          // several tests away from the cause.
          connection: "close",
        });
        response.end(payload);
      }, LATENCY_MS);
    };

    if (method === "OPTIONS") {
      response.writeHead(204, {
        "access-control-allow-origin": "*",
        "access-control-allow-headers": "authorization,content-type,accept",
        "access-control-allow-methods": "GET,POST,PATCH,DELETE,OPTIONS",
      });
      response.end();
      return;
    }

    // Development sign-in, so a real browser can hold a real session without
    // Entra (`lib/auth/dev-tokens.ts`).
    if (url.startsWith("/dev/token")) {
      send(200, { access_token: "stub-token", token_type: "Bearer", expires_in: 3600 });
      return;
    }

    if (url.startsWith("/v1/me")) {
      send(200, {
        subject: "tester",
        user_id: "55555555-5555-5555-5555-555555555555",
        email: "tester@example.com",
        name: "Tester",
        memberships: [{ org_id: ORG, org_name: "Acme", role: "admin" }],
      });
      return;
    }

    if (url.includes("/executions/")) {
      send(200, {
        id: EXECUTION,
        run_id: RUN,
        status: "ok",
        sql: 'SELECT COUNT(*) AS "order_count" FROM "public"."orders"',
        tables: ["public.orders"],
        columns: ["order_count"],
        row_count: 1,
        duration_ms: 31,
        violation_code: null,
        error: null,
        sensitive_accessed: false,
        masked_columns: [],
        sample_rows: [[3718]],
        truncated: false,
        created_at: "2026-08-15T09:00:10Z",
      });
      return;
    }

    if (url.includes("/events")) {
      // The trace's own steps, revealed as the run progresses so a browser test
      // can watch them arrive rather than being handed the finished list.
      const steps = [
        { seq: 1, type: "run_started", payload: { question: QUESTION } },
        { seq: 2, type: "context_selected", payload: { tables: ["public.orders"] } },
        { seq: 3, type: "plan_created", payload: { purpose: "count July orders" } },
        { seq: 4, type: "query_executed", payload: { row_count: 1, duration_ms: 31 } },
        { seq: 5, type: "run_finished", payload: { status: "completed" } },
      ];
      const visible = steps.slice(0, Math.min(steps.length, runPolls + 1));
      const after = Number(new URL(url, "http://stub").searchParams.get("after") ?? 0);
      const fresh = visible.filter((step) => step.seq > after);

      if ((request.headers.accept ?? "").includes("text/event-stream")) {
        // Replay, then close — the real server holds the connection open until
        // the run ends, and the client resumes from `Last-Event-ID` either way,
        // so closing early exercises exactly that recovery path.
        setTimeout(() => {
          if (response.writableEnded || response.destroyed) return;
          response.writeHead(200, {
            "content-type": "text/event-stream",
            "cache-control": "no-cache",
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "authorization,content-type,accept,last-event-id",
            connection: "close",
          });
          for (const step of fresh) {
            const body = JSON.stringify({ ...step, ts: "2026-08-15T09:00:05Z" });
            response.write(`id: ${step.seq}
event: ${step.type}
data: ${body}

`);
          }
          response.end();
        }, LATENCY_MS);
        return;
      }

      send(200, {
        run_id: RUN,
        events: fresh.map((step) => ({ ...step, ts: "2026-08-15T09:00:05Z" })),
        last_seq: fresh.length > 0 ? fresh[fresh.length - 1]!.seq : after,
      });
      return;
    }

    /**
     * One run, as both routes state it.
     *
     * Shared, because the API shares it: `_run_out` builds the single route's
     * payload and the thread's list alike, and a stub that let the two drift
     * would be testing a difference the product cannot produce.
     */
    const runPayload = () => {
      const done = finished();
      return {
        id: RUN,
        conversation_id: CONVERSATION,
        status: done ? "completed" : "running",
        question: QUESTION,
        answer: done ? ANSWER : null,
        findings: done
          ? [{ id: "f1", statement: ANSWER, support: [EXECUTION], confidence: "high" }]
          : [],
        // A real spec, of the shape the server builds: values inline, no URL.
        // Only once the run is done, because a chart is part of an answer.
        chart: done ? { spec: CHART_SPEC } : null,
        started_at: "2026-08-15T09:00:01Z",
        finished_at: done ? "2026-08-15T09:00:20Z" : null,
        failure_reason: null,
      };
    };

    // The thread's runs (**B-106**), which the screen reads so every answer is a
    // card rather than only the newest one. Before the trailing-slash route
    // below, because `/conversations/{id}/runs` has no slash after `runs` and
    // would otherwise fall through to the messages branch.
    if (url.endsWith("/runs")) {
      send(200, finished() ? [runPayload()] : []);
      return;
    }

    if (url.includes("/runs/")) {
      runPolls += 1;
      send(200, runPayload());
      return;
    }

    if (url.includes("/messages")) {
      if (method === "POST") {
        asked = true;
        send(202, { run_id: RUN, message_id: "m1", status: "queued", created: true });
        return;
      }
      const thread: unknown[] = [];
      if (asked) {
        thread.push({
          id: "m1",
          role: "user",
          content: QUESTION,
          run_id: RUN,
          created_at: "2026-08-15T09:00:00Z",
        });
      }
      // The reply exists only once the run has finished — which is exactly what
      // stops `send()`'s own reload from picking it up.
      if (finished()) {
        thread.push({
          id: "m2",
          role: "assistant",
          content: ANSWER,
          run_id: RUN,
          created_at: "2026-08-15T09:00:20Z",
        });
      }
      send(200, thread);
      return;
    }

    if (url.includes("/data-sources")) {
      send(200, [
        {
          id: "66666666-6666-6666-6666-666666666666",
          name: "Demo",
          engine: "pg",
          host: "seed-pizza-pg",
          port: 5432,
          database: "pizza",
          host_display: "seed-pizza-pg:5432/pizza",
          status: "verified",
          secret_ref: "ds/x",
          username_last4: "only",
          tls_mode: "prefer",
          readonly_verified: true,
          last_verified_at: null,
          created_by: null,
          created_at: "2026-08-12T10:00:00Z",
        },
      ]);
      return;
    }

    // The database this organization answers from (D-045). The chat home and
    // Settings both read it; without it they would report a failure instead of
    // a composer.
    if (url.includes("/active-data-source")) {
      send(200, {
        data_source_id: "66666666-6666-6666-6666-666666666666",
        data_source_name: "Demo",
      });
      return;
    }

    /**
     * The **list**, which the sidebar reads — an array, not the single object
     * the catch-all below returns.
     *
     * Matched before it and anchored on the end of the path, because
     * `/conversations` is a prefix of `/conversations/{id}` and the looser test
     * would answer both with the wrong shape. `api.conversations()` validates
     * `every(isConversation)`, so the wrong shape is an error banner in the rail
     * rather than a silent oddity.
     */
    if (/\/conversations(\?|$)/.test(url)) {
      // Starting a chat posts to the same path the list is read from, and answers
      // with the one conversation it made — not with a list. Without this the
      // new-chat flow parses an array as a conversation and fails its guard.
      if (method === "POST") {
        send(201, {
          id: CONVERSATION,
          title: null,
          created_at: "2026-08-15T09:00:00Z",
          message_count: 0,
          last_run_id: null,
          data_source_id: "66666666-6666-6666-6666-666666666666",
          data_source_name: "Demo",
          archived_at: null,
        });
        return;
      }
      send(200, [
        {
          id: CONVERSATION,
          title: QUESTION,
          created_at: "2026-08-15T09:00:00Z",
          message_count: asked ? 1 : 0,
          last_run_id: asked ? RUN : null,
          data_source_id: "66666666-6666-6666-6666-666666666666",
          data_source_name: "Demo",
          archived_at: null,
        },
      ]);
      return;
    }

    if (url.includes("/conversations")) {
      send(200, {
        id: CONVERSATION,
        title: QUESTION,
        created_at: "2026-08-15T09:00:00Z",
        message_count: asked ? 1 : 0,
        // Null until a question has been asked, then the run — exactly as the
        // real API behaves. This is what a reloaded page adopts, and getting it
        // wrong made the refresh-replay test pass vacuously: the answer was
        // visible from the messages while no run, and therefore no trace, was
        // ever picked up.
        last_run_id: asked ? RUN : null,
        data_source_id: "66666666-6666-6666-6666-666666666666",
        data_source_name: "Demo",
      });
      return;
    }

    send(404, { detail: `stub has no route for ${url}` });
  });

  await bindWithRetry(server, port);
  const address = server.address();
  if (address === null || typeof address === "string") {
    throw new Error("the stub API did not bind a port");
  }

  return {
    url: `http://127.0.0.1:${address.port}`,
    calls,
    reset: () => {
      asked = false;
      runPolls = 0;
      calls.length = 0;
    },
    close: () =>
      new Promise<void>((resolve, reject) =>
        server.close((error) => (error ? reject(error) : resolve())),
      ),
  };
}
