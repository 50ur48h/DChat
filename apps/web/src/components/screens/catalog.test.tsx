import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CatalogBrowser } from "./catalog";

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

const EMAIL_COLUMN = {
  id: "c1",
  name: "email",
  ordinal: 2,
  data_type: "text",
  nullable: false,
  is_pk: false,
  description: "Contact email address (personal data).",
  null_frac: 0,
  distinct_est: 300,
  min_val: "a***@e***.com",
  max_val: "z***@e***.com",
  top_values: null,
  semantic_role: "other",
  sensitivity: "suspected",
  sample_rows: 300,
  policy: "mask",
  policy_decided: false,
};

const CITY_COLUMN = {
  ...EMAIL_COLUMN,
  id: "c2",
  name: "city",
  ordinal: 3,
  description: null,
  min_val: null,
  max_val: null,
  semantic_role: "dimension",
  sensitivity: "none",
  policy: "allow",
};

const CATALOG = {
  snapshot: {
    id: "s1",
    version: 2,
    status: "active",
    captured_at: "2026-08-13T09:00:00Z",
    completed_at: "2026-08-13T09:00:01Z",
    object_count: 1,
    error: null,
  },
  tables: [
    {
      schema_name: "public",
      table_name: "customers",
      kind: "table",
      description: "Loyalty-programme members. Contains personal data.",
      row_estimate: 300,
      card_text: "public.customers is a table with about 300 rows.",
      columns: [EMAIL_COLUMN, CITY_COLUMN],
    },
  ],
  relationships: [],
};

function json(body: unknown, status = 200): Response {
  // 204 must have no body at all — the Response constructor refuses one.
  if (status === 204) return new Response(null, { status });
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stubFetch(...responses: Response[]) {
  const calls: { url: string; init: RequestInit }[] = [];
  let index = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init: RequestInit = {}) => {
      calls.push({ url, init });
      const response = responses[Math.min(index, responses.length - 1)];
      index += 1;
      return Promise.resolve(response?.clone() ?? json(CATALOG));
    }),
  );
  return calls;
}

describe("<CatalogBrowser />", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
  });

  it("lists columns with their role, their sample and the policy in force", async () => {
    stubFetch(json(CATALOG));

    render(<CatalogBrowser orgId="o1" dataSourceId="d1" role="admin" />);

    expect(await screen.findByText("public.customers")).toBeInTheDocument();
    expect(screen.getByText("email")).toBeInTheDocument();
    expect(screen.getByText("suspected")).toBeInTheDocument();
    expect(screen.getAllByText("mask").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/from 300 sampled rows/).length).toBe(2);
    // A masked column is listed like any other: somebody asking "can I group by
    // this" needs an answer even when they may not see the values.
    expect(screen.getAllByText(/a\*\*\*@e\*\*\*\.com/).length).toBeGreaterThan(0);
  });

  it("says when nobody has reviewed a masking decision", async () => {
    stubFetch(json(CATALOG));

    render(<CatalogBrowser orgId="o1" dataSourceId="d1" role="admin" />);

    expect((await screen.findAllByText("nobody has reviewed this")).length).toBe(2);
  });

  it("distinguishes a decision from a default", async () => {
    stubFetch(
      json({
        ...CATALOG,
        tables: [
          {
            ...CATALOG.tables[0],
            columns: [{ ...EMAIL_COLUMN, policy: "allow", policy_decided: true }],
          },
        ],
      }),
    );

    render(<CatalogBrowser orgId="o1" dataSourceId="d1" role="admin" />);

    expect(await screen.findByText("decided by an admin")).toBeInTheDocument();
  });

  it("lets an Admin change a column policy", async () => {
    const calls = stubFetch(json(CATALOG), json(null, 204), json(CATALOG));

    render(<CatalogBrowser orgId="o1" dataSourceId="d1" role="admin" />);
    const select = (await screen.findAllByLabelText("Change policy"))[0] as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "allow" } });

    await waitFor(() => expect(calls.some((call) => call.init.method === "PATCH")).toBe(true));
    const patch = calls.find((call) => call.init.method === "PATCH");
    expect(patch?.url).toContain("/columns/c1/policy");
    expect(String(patch?.init.body)).toContain("allow");
  });

  it("offers a Reader no way to change what may be seen", async () => {
    stubFetch(json(CATALOG));

    render(<CatalogBrowser orgId="o1" dataSourceId="d1" role="reader" />);

    expect(await screen.findByText("public.customers")).toBeInTheDocument();
    expect(screen.queryByLabelText("Change policy")).not.toBeInTheDocument();
    expect(screen.getByText("Only an Admin can change what may be seen.")).toBeInTheDocument();
  });

  it("searches cards and shows what matched", async () => {
    stubFetch(
      json(CATALOG),
      json([
        {
          data_source_id: "d1",
          schema_name: "public",
          table_name: "orders",
          card_text: "public.orders is a table. Revenue excludes cancelled orders.",
          rank: 0.9,
        },
      ]),
    );

    render(<CatalogBrowser orgId="o1" dataSourceId="d1" role="admin" />);
    fireEvent.change(await screen.findByLabelText("Search"), { target: { value: "revenue" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText("public.orders")).toBeInTheDocument();
    expect(screen.getByText(/Revenue excludes cancelled orders/)).toBeInTheDocument();
  });

  it("says so when a search matches nothing", async () => {
    stubFetch(json(CATALOG), json([]));

    render(<CatalogBrowser orgId="o1" dataSourceId="d1" role="admin" />);
    fireEvent.change(await screen.findByLabelText("Search"), { target: { value: "nothing" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText(/Nothing matched/)).toBeInTheDocument();
  });

  it("shows the card an agent would be given, verbatim", async () => {
    stubFetch(json(CATALOG));

    render(<CatalogBrowser orgId="o1" dataSourceId="d1" role="admin" />);
    fireEvent.click(await screen.findByRole("button", { name: "Show card" }));

    expect(
      await screen.findByText("public.customers is a table with about 300 rows."),
    ).toBeInTheDocument();
  });

  it("says what went wrong rather than showing an empty screen", async () => {
    stubFetch(json({ detail: "This data source has no catalog yet. Refresh it." }, 404));

    render(<CatalogBrowser orgId="o1" dataSourceId="d1" role="admin" />);

    expect(
      await screen.findByText("This data source has no catalog yet. Refresh it."),
    ).toBeInTheDocument();
  });
});
