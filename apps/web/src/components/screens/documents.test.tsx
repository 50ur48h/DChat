import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Documents, indexingSummary } from "./documents";

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

const INDEXED = {
  id: "d1",
  title: "Revenue policy",
  mime: "text/markdown",
  status: "indexed",
  chunk_count: 4,
  embedded_count: 4,
  failure_reason: null,
  created_at: "2026-08-17T09:00:00Z",
  indexed_at: "2026-08-17T09:00:05Z",
};

const PART_EMBEDDED = {
  ...INDEXED,
  id: "d2",
  title: "Operations handbook",
  embedded_count: 1,
};

const SCANNED = {
  ...INDEXED,
  id: "d3",
  title: "Scanned contract",
  status: "failed",
  chunk_count: 0,
  embedded_count: 0,
  failure_reason:
    "No readable text was found in this file. If it is a scanned PDF, the pages are images and need OCR before they can be indexed.",
  indexed_at: null,
};

function json(body: unknown, status = 200): Response {
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
      return Promise.resolve(response?.clone() ?? json([]));
    }),
  );
  return calls;
}

describe("indexingSummary", () => {
  it("says a document is searchable both ways when every chunk has a vector", () => {
    expect(indexingSummary(INDEXED)).toContain("wording and meaning");
  });

  it("says how far embedding got rather than rounding it up to indexed", () => {
    // The state a large upload spends longest in. Reporting it as "indexed"
    // would hide the one thing somebody waiting on it wants to know.
    const summary = indexingSummary(PART_EMBEDDED);

    expect(summary).toContain("1 searchable by meaning so far");
    expect(summary).not.toContain("wording and meaning");
  });

  it("says a failed document with chunks is still searchable by wording", () => {
    // An embedding failure keeps the text (WP10.1a), so the screen must not
    // imply the upload was lost.
    expect(indexingSummary({ ...SCANNED, chunk_count: 3 })).toContain("wording only");
  });

  it("says nothing was indexed when a failure produced no chunks", () => {
    expect(indexingSummary(SCANNED)).toBe("Nothing was indexed");
  });
});

describe("<Documents />", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
  });

  it("lists what the organization has written down", async () => {
    stubFetch(json([INDEXED, SCANNED]));

    render(<Documents orgId="org-1" role="admin" />);

    expect(await screen.findByText("Revenue policy")).toBeInTheDocument();
    expect(screen.getByText("Scanned contract")).toBeInTheDocument();
  });

  it("shows the failure reason the API wrote for the person who uploaded it", async () => {
    // Not replaced with a generic message: "needs OCR" is actionable and
    // "indexing failed" is not.
    stubFetch(json([SCANNED]));

    render(<Documents orgId="org-1" role="admin" />);

    expect(await screen.findByText(/need OCR/)).toBeInTheDocument();
  });

  it("offers a Reader no control the API would refuse", async () => {
    // B-008: the API refuses and audits these anyway; offering the button
    // teaches people the product is broken rather than that they lack
    // permission.
    stubFetch(json([INDEXED]));

    render(<Documents orgId="org-1" role="reader" />);

    expect(await screen.findByText("Revenue policy")).toBeInTheDocument();
    expect(screen.queryByText("Upload")).not.toBeInTheDocument();
    expect(screen.queryByText("Re-index")).not.toBeInTheDocument();
    expect(screen.queryByText("Remove")).not.toBeInTheDocument();
  });

  it("fails closed while the role is still unknown", async () => {
    // Costs a Contributor one page load; the alternative shows buttons to
    // people the API will refuse.
    stubFetch(json([INDEXED]));

    render(<Documents orgId="org-1" role={null} />);

    expect(await screen.findByText("Revenue policy")).toBeInTheDocument();
    expect(screen.queryByText("Upload")).not.toBeInTheDocument();
  });

  it("lets a Contributor upload and re-index", async () => {
    stubFetch(json([INDEXED]));

    render(<Documents orgId="org-1" role="contributor" />);

    expect(await screen.findByText("Revenue policy")).toBeInTheDocument();
    expect(screen.getByText("Upload")).toBeInTheDocument();
    expect(screen.getByText("Re-index")).toBeInTheDocument();
  });

  it("sends the file as multipart, with no JSON content type", async () => {
    // The browser has to write its own boundary; setting the header by hand
    // produces a request the server cannot parse.
    const calls = stubFetch(json([]), json(INDEXED, 201), json([INDEXED]));

    render(<Documents orgId="org-1" role="admin" />);
    await screen.findByText(/No documents yet/);

    const picker = document.querySelector('input[type="file"]');
    expect(picker).not.toBeNull();
    const file = new File(["# Revenue policy\n\nNet revenue excludes cancellations."], "policy.md", {
      type: "text/markdown",
    });
    fireEvent.change(picker as HTMLInputElement, { target: { files: [file] } });
    fireEvent.click(screen.getByText("Upload"));

    await waitFor(() => {
      expect(calls.some((call) => call.init.method === "POST")).toBe(true);
    });
    const post = calls.find((call) => call.init.method === "POST");
    expect(post?.url).toBe("http://api.test/v1/orgs/org-1/documents");
    expect(post?.init.body).toBeInstanceOf(FormData);
    const headers = post?.init.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBeUndefined();
  });

  it("says something useful when there is nothing yet, and says it differently to a Reader", async () => {
    stubFetch(json([]));
    const { unmount } = render(<Documents orgId="org-1" role="admin" />);
    expect(await screen.findByText(/Upload a policy or a definition/)).toBeInTheDocument();
    unmount();

    stubFetch(json([]));
    render(<Documents orgId="org-1" role="reader" />);
    expect(await screen.findByText(/A Contributor or Admin can add one/)).toBeInTheDocument();
  });
});

describe("the indexing meter", () => {
  it("draws a proportion only where two real counts exist (D-049)", async () => {
    /**
     * **A meter needs a denominator that is not invented.** A part-embedded
     * document has one — passages stored against passages embedded — and the
     * bar is a proportion of those two numbers and nothing else.
     */
    stubFetch(json([PART_EMBEDDED]));

    render(<Documents orgId="o1" role="admin" />);

    const meter = await screen.findByRole("progressbar", { name: /Operations handbook/ });
    expect(meter).toHaveAttribute("aria-valuemax", "4");
    expect(meter).toHaveAttribute("aria-valuenow", "1");
  });

  it("draws nothing at all when there is no denominator", async () => {
    /**
     * Before any passage is stored the total does not exist, so a bar at 0%
     * would be claiming a total nobody knows. A failed document gets none
     * either: it is not part-way anywhere, it has stopped. The words carry
     * both cases instead.
     */
    stubFetch(json([SCANNED]));

    render(<Documents orgId="o1" role="admin" />);

    expect(await screen.findByText("Scanned contract")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });
});
