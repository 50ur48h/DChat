import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EvidencePanel } from "./evidence";

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

const RAN = {
  id: "x1",
  run_id: "r1",
  status: "ok",
  sql: 'SELECT count(*) AS "order_count" FROM "public"."orders" WHERE placed_at >= \'2026-07-01\'',
  tables: ["public.orders"],
  columns: ["order_count", "email"],
  row_count: 71798,
  duration_ms: 142,
  violation_code: null,
  error: null,
  sensitive_accessed: true,
  masked_columns: ["email"],
  sample_rows: [[128, "k***@e***.com"]],
  truncated: false,
  created_at: "2026-08-15T09:00:00Z",
};

const REFUSED = {
  ...RAN,
  id: "x2",
  status: "refused",
  sql: "SELECT customer_name FROM orders",
  violation_code: "unknown_column",
  error: "Unknown column 'customer_name' on public.orders",
  row_count: null,
  duration_ms: null,
  sensitive_accessed: false,
  masked_columns: [],
  sample_rows: [],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stubFetch(response: Response) {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response.clone())));
}

describe("<EvidencePanel />", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
  });

  it("shows the statement that ran, verbatim", async () => {
    stubFetch(json(RAN));

    render(<EvidencePanel orgId="o1" runId="r1" executionId="x1" />);

    // Verbatim, for the same reason the catalog shows a card verbatim: somebody
    // asking why the answer looks odd must be able to read exactly what ran.
    expect(await screen.findByText(/SELECT count\(\*\) AS "order_count"/)).toBeInTheDocument();
    expect(screen.getByText("public.orders")).toBeInTheDocument();
    expect(screen.getByText("142 ms")).toBeInTheDocument();
  });

  it("says how many rows were returned against how many are shown", async () => {
    stubFetch(json(RAN));

    render(<EvidencePanel orgId="o1" runId="r1" executionId="x1" />);

    // "1 shown of 71,798" rather than a table the reader has to measure — a
    // preview mistaken for the result is a wrong conclusion, not a display nit.
    expect(await screen.findByText("1 shown of 71,798")).toBeInTheDocument();
  });

  it("labels a masked column where its values are read", async () => {
    stubFetch(json(RAN));

    render(<EvidencePanel orgId="o1" runId="r1" executionId="x1" />);

    expect(await screen.findByText("k***@e***.com")).toBeInTheDocument();
    expect(screen.getByText("masked")).toBeInTheDocument();
    // The values arrived masked (D-013): there is no unmasked copy in the
    // platform database, and this panel has no path that could ask for one.
    expect(screen.getByText(/unmasked values were never kept/)).toBeInTheDocument();
  });

  it("shows a refusal as a refusal, never as an empty result", async () => {
    stubFetch(json(REFUSED));

    render(<EvidencePanel orgId="o1" runId="r1" executionId="x2" />);

    expect(await screen.findByText("refused")).toBeInTheDocument();
    expect(screen.getByText("unknown_column")).toBeInTheDocument();
    expect(screen.getByText(/Unknown column 'customer_name'/)).toBeInTheDocument();
    // An empty table here would read as "your data has no answer", when what
    // happened is "this service would not run that".
    expect(screen.getByText(/Nothing was sent to the database/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("says so when a citation will not open", async () => {
    stubFetch(json({ detail: "No such execution in this organization" }, 404));

    render(<EvidencePanel orgId="o1" runId="r1" executionId="gone" />);

    // Silence here would look like evidence that is merely thin, which is the
    // one failure this panel must not disguise.
    expect(await screen.findByText(/No such execution/)).toBeInTheDocument();
  });

  it("distinguishes a query that matched nothing from one that was refused", async () => {
    stubFetch(json({ ...RAN, row_count: 0, sample_rows: [] }));

    render(<EvidencePanel orgId="o1" runId="r1" executionId="x1" />);

    expect(await screen.findByText("This query matched no rows.")).toBeInTheDocument();
    expect(screen.queryByText("refused")).not.toBeInTheDocument();
  });
});
