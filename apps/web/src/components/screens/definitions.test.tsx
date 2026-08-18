import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  acceptanceSummary,
  describeFilter,
  describeProvenance,
  Definitions,
} from "./definitions";

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

const PROPOSAL = {
  id: "p1",
  name: "anchor_order",
  description: "A completed order over 40 pounds placed on a weekday.",
  expression: "count(orders.id)",
  synonyms: ["anchor orders"],
  provenance: { kind: "import", table: "public.meta_metric", snapshot_id: "s1" },
};

const BINDING = {
  id: "d1",
  name: "net_revenue",
  kind: "metric",
  description: "Revenue excluding cancelled and refunded orders.",
  expression: "sum(orders.total_amount)",
  required_filters: [
    { table: "orders", column: "status", op: "not_in", values: ["cancelled", "refunded"] },
  ],
  synonyms: ["net sales"],
  binds: true,
};

const PROSE = {
  ...BINDING,
  id: "d2",
  name: "basket_size",
  description: "What an average order is worth.",
  required_filters: [],
  synonyms: [],
  binds: false,
};

function json(body: unknown, status = 200): Response {
  if (status === 204) return new Response(null, { status });
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/**
 * The screen loads definitions and proposals together, so a stub keyed by URL
 * is the only honest shape — a positional list would depend on which half of
 * `Promise.all` the runtime happened to start first.
 */
function stubFetch(routes: { definitions?: Response; proposals?: Response } = {}) {
  const calls: { url: string; init: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init: RequestInit = {}) => {
      calls.push({ url, init });
      if (url.endsWith("/definitions/proposals")) {
        return Promise.resolve((routes.proposals ?? json([])).clone());
      }
      if (url.endsWith("/definitions")) {
        return Promise.resolve((routes.definitions ?? json([])).clone());
      }
      return Promise.resolve(json({}, 200));
    }),
  );
  return calls;
}

describe("describeFilter", () => {
  it("reads a filter the way the prompt words it", () => {
    // The same phrasing the API's `describe()` uses, so an Admin reading the
    // screen and a model reading the prompt are told the same thing.
    expect(describeFilter(BINDING.required_filters[0]!)).toBe(
      "orders.status none of cancelled, refunded",
    );
  });
});

describe("describeProvenance", () => {
  it("names the table an import read", () => {
    expect(describeProvenance(PROPOSAL.provenance)).toBe("Imported from public.meta_metric");
  });

  it("says nothing when there is nothing to say", () => {
    // A definition typed by hand has no provenance, and "Imported from
    // undefined" would be worse than an absent line.
    expect(describeProvenance({})).toBeNull();
  });
});

describe("acceptanceSummary", () => {
  it("warns that accepting with no filters binds nothing", () => {
    // D-033's disclosure, at the moment somebody can still change their mind.
    const summary = acceptanceSummary([]);

    expect(summary).toContain("binds nothing");
    expect(summary).toContain("an answer resting on it will say so");
  });

  it("says a query ignoring the filters will be blocked", () => {
    expect(acceptanceSummary(BINDING.required_filters)).toContain("blocked");
  });
});

describe("<Definitions />", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
  });

  it("shows a proposal, what it says, and where it came from", async () => {
    stubFetch({ proposals: json([PROPOSAL]) });

    render(<Definitions orgId="org-1" dataSourceId="ds-1" role="admin" />);

    expect(await screen.findByText("anchor_order")).toBeInTheDocument();
    expect(screen.getByText(/completed order over 40 pounds/)).toBeInTheDocument();
    expect(screen.getByText("Imported from public.meta_metric")).toBeInTheDocument();
  });

  it("separates what is enforced from what is only prose", async () => {
    // The whole of D-033 on one screen. A definition with filters is a
    // constraint; one without is guidance, and reading them as the same thing
    // is the mistake this labelling exists to prevent.
    stubFetch({ definitions: json([BINDING, PROSE]) });

    render(<Definitions orgId="org-1" dataSourceId="ds-1" role="admin" />);

    expect(await screen.findByText("net_revenue")).toBeInTheDocument();
    expect(screen.getByText("enforced")).toBeInTheDocument();
    expect(screen.getByText("prose only")).toBeInTheDocument();
    expect(screen.getByText(/Nothing checks this one/)).toBeInTheDocument();
  });

  it("offers a Reader no control the API would refuse (B-008)", async () => {
    stubFetch({ definitions: json([BINDING]), proposals: json([PROPOSAL]) });

    render(<Definitions orgId="org-1" dataSourceId="ds-1" role="reader" />);

    expect(await screen.findByText(/Only an Admin can review/)).toBeInTheDocument();
    expect(screen.queryByText("Import")).not.toBeInTheDocument();
    expect(screen.queryByText("Reject")).not.toBeInTheDocument();
    expect(screen.queryByText("anchor_order")).not.toBeInTheDocument();
  });

  it("fails closed while the role is still unknown", async () => {
    stubFetch({ proposals: json([PROPOSAL]) });

    render(<Definitions orgId="org-1" dataSourceId="ds-1" role={null} />);

    expect(await screen.findByText(/Only an Admin can review/)).toBeInTheDocument();
  });

  it("asks the API for nothing when the role would be refused", async () => {
    // A 403 for a screen that never offered the action is noise, and the audit
    // row it writes is a denial nobody attempted.
    const calls = stubFetch({ proposals: json([PROPOSAL]) });

    render(<Definitions orgId="org-1" dataSourceId="ds-1" role="reader" />);
    await screen.findByText(/Only an Admin can review/);

    expect(calls.filter((call) => call.url.includes("/definitions"))).toHaveLength(0);
  });

  it("says accepting without filters will bind nothing, before the click", async () => {
    stubFetch({ proposals: json([PROPOSAL]) });

    render(<Definitions orgId="org-1" dataSourceId="ds-1" role="admin" />);

    expect(await screen.findByText("anchor_order")).toBeInTheDocument();
    expect(screen.getByText(/binds nothing/)).toBeInTheDocument();
    expect(screen.getByText("Accept as prose")).toBeInTheDocument();
  });

  it("changes what the button promises once a filter is staged", async () => {
    // The button names the act it is about to perform. Accepting as prose and
    // accepting as a constraint are different decisions, and one label for both
    // is how somebody enforces a rule they did not mean to.
    stubFetch({ proposals: json([PROPOSAL]) });

    render(<Definitions orgId="org-1" dataSourceId="ds-1" role="admin" />);
    await screen.findByText("anchor_order");

    fireEvent.change(screen.getByLabelText("Table"), { target: { value: "orders" } });
    fireEvent.change(screen.getByLabelText("Column"), { target: { value: "status" } });
    fireEvent.change(screen.getByLabelText("Must be"), { target: { value: "eq" } });
    fireEvent.change(screen.getByLabelText("Values"), { target: { value: "completed" } });
    fireEvent.click(screen.getByText("Add filter"));

    expect(await screen.findByText("orders.status equal to completed")).toBeInTheDocument();
    expect(screen.getByText("Accept and enforce")).toBeInTheDocument();
    expect(screen.getByText(/is blocked before the answer is written/)).toBeInTheDocument();
  });

  it("sends the staged filters when it accepts", async () => {
    const calls = stubFetch({ proposals: json([PROPOSAL]) });

    render(<Definitions orgId="org-1" dataSourceId="ds-1" role="admin" />);
    await screen.findByText("anchor_order");

    fireEvent.change(screen.getByLabelText("Table"), { target: { value: "orders" } });
    fireEvent.change(screen.getByLabelText("Column"), { target: { value: "status" } });
    fireEvent.change(screen.getByLabelText("Values"), { target: { value: "completed" } });
    fireEvent.click(screen.getByText("Add filter"));
    fireEvent.click(await screen.findByText("Accept and enforce"));

    await waitFor(() => {
      const accept = calls.find((call) => call.url.endsWith("/accept"));
      expect(accept).toBeDefined();
      expect(JSON.parse(String(accept?.init.body))).toEqual({
        required_filters: [
          { table: "orders", column: "status", op: "in", values: ["completed"] },
        ],
      });
    });
  });

  it("keeps a rejected filter on screen to correct rather than retype", async () => {
    // The API's message names the column that was wrong. Clearing the staged
    // work on failure would make the correction a retype.
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (url.endsWith("/definitions/proposals")) {
          return Promise.resolve(json([PROPOSAL]));
        }
        if (url.endsWith("/accept")) {
          return Promise.resolve(
            json({ detail: "'anchor_order' requires a filter on orders.stats, and 'orders' has no column called 'stats'." }, 400),
          );
        }
        return Promise.resolve(json([]));
      }),
    );

    render(<Definitions orgId="org-1" dataSourceId="ds-1" role="admin" />);
    await screen.findByText("anchor_order");

    fireEvent.change(screen.getByLabelText("Table"), { target: { value: "orders" } });
    fireEvent.change(screen.getByLabelText("Column"), { target: { value: "stats" } });
    fireEvent.change(screen.getByLabelText("Values"), { target: { value: "completed" } });
    fireEvent.click(screen.getByText("Add filter"));
    fireEvent.click(await screen.findByText("Accept and enforce"));

    expect(await screen.findByText(/no column called 'stats'/)).toBeInTheDocument();
    expect(screen.getByText("orders.stats one of completed")).toBeInTheDocument();
  });

  it("says an import that proposed nothing succeeded", async () => {
    // An empty list alone answers neither "done" nor "did my mapping fail".
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (url.endsWith("/definitions/import")) return Promise.resolve(json([], 201));
        return Promise.resolve(json([]));
      }),
    );

    render(<Definitions orgId="org-1" dataSourceId="ds-1" role="admin" />);
    await screen.findByText(/Nothing waiting/);

    fireEvent.change(screen.getByLabelText("Metric table"), {
      target: { value: "meta_metric" },
    });
    fireEvent.change(screen.getByLabelText("Name column"), { target: { value: "metric_key" } });
    fireEvent.change(screen.getByLabelText("Definition column"), {
      target: { value: "definition_text" },
    });
    fireEvent.click(screen.getByText("Import"));

    expect(await screen.findByText(/already known here/)).toBeInTheDocument();
  });

  it("carries the customer's own names into the import (B-085, B-087)", async () => {
    // The field the form did not have, found when a gate walk imported 18
    // metrics and every question sailed past all of them. A definition is
    // matched by name and synonym; imported without synonyms it answers only to
    // its key, which nobody types. The import binds nothing and looks like the
    // feature not working.
    const calls = stubFetch();

    render(<Definitions orgId="org-1" dataSourceId="ds-1" role="admin" />);
    await screen.findByText(/Nothing waiting/);

    fireEvent.change(screen.getByLabelText("Metric table"), {
      target: { value: "meta_metric" },
    });
    fireEvent.change(screen.getByLabelText("Name column"), { target: { value: "metric_key" } });
    fireEvent.change(screen.getByLabelText("Definition column"), {
      target: { value: "definition" },
    });
    fireEvent.change(screen.getByLabelText("Names column (optional)"), {
      target: { value: "metric_name" },
    });
    fireEvent.click(screen.getByText("Import"));

    await waitFor(() => {
      const call = calls.find((entry) => entry.url.endsWith("/definitions/import"));
      expect(call).toBeDefined();
      expect(JSON.parse(String(call?.init.body))).toMatchObject({
        table: "meta_metric",
        name_column: "metric_key",
        description_column: "definition",
        synonyms_column: "metric_name",
      });
    });
  });

  it("omits the optional columns rather than sending them empty", async () => {
    // An empty string is a column name the API would look for and never find.
    const calls = stubFetch();

    render(<Definitions orgId="org-1" dataSourceId="ds-1" role="admin" />);
    await screen.findByText(/Nothing waiting/);

    fireEvent.change(screen.getByLabelText("Metric table"), { target: { value: "m" } });
    fireEvent.change(screen.getByLabelText("Name column"), { target: { value: "k" } });
    fireEvent.change(screen.getByLabelText("Definition column"), { target: { value: "d" } });
    fireEvent.click(screen.getByText("Import"));

    await waitFor(() => {
      const call = calls.find((entry) => entry.url.endsWith("/definitions/import"));
      const body = JSON.parse(String(call?.init.body)) as Record<string, unknown>;
      expect(body).not.toHaveProperty("synonyms_column");
      expect(body).not.toHaveProperty("expression_column");
    });
  });
});
