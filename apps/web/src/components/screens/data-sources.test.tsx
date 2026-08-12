import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DataSources } from "./data-sources";

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

/** The password a person types. It must not appear anywhere but one POST body. */
const TYPED_SECRET = "not-a-real-password-9f3a";

const SOURCE = {
  id: "ds1",
  name: "Pizza demo",
  engine: "pg",
  host: "localhost",
  port: 6543,
  database: "pizza",
  host_display: "localhost:6543/pizza",
  status: "verified",
  secret_ref: "ds/org1/ds1/credentials",
  username_last4: "only",
  tls_mode: "prefer",
  readonly_verified: true,
  last_verified_at: "2026-08-12T09:00:00Z",
  created_by: "u1",
  created_at: "2026-08-12T08:00:00Z",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** A fetch stub that answers each call from a queue, remembering every request. */
function stubFetch(...responses: Response[]) {
  const calls: { url: string; init: RequestInit }[] = [];
  let index = 0;
  const fetchMock = vi.fn((url: string, init: RequestInit = {}) => {
    calls.push({ url, init });
    const response = responses[Math.min(index, responses.length - 1)];
    index += 1;
    return Promise.resolve(response?.clone() ?? json([]));
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

function bodies(calls: { init: RequestInit }[]): string {
  return calls.map((call) => String(call.init.body ?? "")).join(" ");
}

describe("<DataSources />", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
  });

  it("says what is registered, and what has been proven about it", async () => {
    stubFetch(json([SOURCE]));

    render(<DataSources orgId="org1" role="admin" />);

    expect(await screen.findByText("Pizza demo")).toBeInTheDocument();
    expect(screen.getByText("localhost:6543/pizza")).toBeInTheDocument();
    expect(screen.getByText("read-only verified")).toBeInTheDocument();
    expect(screen.getByText("PostgreSQL")).toBeInTheDocument();
    // The mode itself, not a green tick: `prefer` may not be encrypted at all.
    expect(screen.getByText("TLS: prefer")).toBeInTheDocument();
    // The account is identified without being handed back.
    expect(screen.getByText("only")).toBeInTheDocument();
  });

  it("invites the first registration when there is nothing yet", async () => {
    stubFetch(json([]));

    render(<DataSources orgId="org1" role="admin" />);

    expect(await screen.findByText(/None yet/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Register" })).toBeInTheDocument();
  });

  it("sends the password once, in a POST body, and never shows it again", async () => {
    const calls = stubFetch(json([]), json(SOURCE, 201), json([SOURCE]));

    render(<DataSources orgId="org1" role="admin" />);
    await screen.findByText(/None yet/);

    for (const [label, value] of [
      ["Name", "Pizza demo"],
      ["Host", "localhost"],
      ["Database", "pizza"],
      ["Username", "pizza_readonly"],
      ["Password", TYPED_SECRET],
    ] as const) {
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    }
    fireEvent.click(screen.getByRole("button", { name: "Register" }));

    await waitFor(() => expect(screen.getByLabelText("Password")).toHaveValue(""));

    const posts = calls.filter((call) => call.init.method === "POST");
    expect(posts).toHaveLength(1);
    expect(String(posts[0]?.init.body)).toContain(TYPED_SECRET);
    // Not in a URL — a query string would put it in history and in every log
    // between the browser and the API.
    expect(calls.every((call) => !call.url.includes(TYPED_SECRET))).toBe(true);
    // And not left rendered anywhere, including as a value attribute.
    expect(document.body.innerHTML).not.toContain(TYPED_SECRET);
  });

  it("clears the password even when the registration is refused", async () => {
    const calls = stubFetch(
      json([]),
      json({ detail: "A data source named 'Pizza demo' already exists here" }, 409),
      json([]),
    );

    render(<DataSources orgId="org1" role="admin" />);
    await screen.findByText(/None yet/);

    for (const [label, value] of [
      ["Name", "Pizza demo"],
      ["Host", "localhost"],
      ["Database", "pizza"],
      ["Username", "pizza_readonly"],
      ["Password", TYPED_SECRET],
    ] as const) {
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    }
    fireEvent.click(screen.getByRole("button", { name: "Register" }));

    expect(await screen.findByText(/already exists here/)).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(document.body.innerHTML).not.toContain(TYPED_SECRET);
    expect(bodies(calls)).toContain(TYPED_SECRET); // it did reach the API, once
  });

  it("renders a failed test as an outcome with a reason, not an alarm", async () => {
    stubFetch(
      json([{ ...SOURCE, status: "error", readonly_verified: false, last_verified_at: null }]),
      json({
        reachable: true,
        readonly_verified: false,
        status: "error",
        detail: "Connected, but these credentials are not read-only: the role is a superuser",
        checked_at: "2026-08-12T10:00:00Z",
        server_version: "PostgreSQL 16.14",
        tls_mode: "prefer",
        tls_encrypted: false,
        tls_detail: "prefer — this connection is NOT encrypted",
        evidence: ["the role is a superuser", "CREATE TABLE succeeded and was rolled back"],
      }),
    );

    render(<DataSources orgId="org1" role="admin" />);
    fireEvent.click(await screen.findByRole("button", { name: "Test connection" }));

    expect(await screen.findByText(/not read-only: the role is a superuser/)).toBeInTheDocument();
    expect(screen.getByText("not proven")).toBeInTheDocument();
    // Encrypted and verified are separate claims and both are shown.
    expect(screen.getByText("prefer — this connection is NOT encrypted")).toBeInTheDocument();
    expect(screen.getByText("CREATE TABLE succeeded and was rolled back")).toBeInTheDocument();
  });

  it("shows the API's own words when a TLS downgrade is refused", async () => {
    stubFetch(
      json([]),
      json(
        {
          detail:
            "TLS mode 'prefer' allows an unencrypted connection, which is only permitted for a database on this machine — 'db.example.com' is not one. Use one of: require, verify-ca, verify-full.",
        },
        422,
      ),
      json([]),
    );

    render(<DataSources orgId="org1" role="admin" />);
    await screen.findByText(/None yet/);

    for (const [label, value] of [
      ["Name", "Cloud"],
      ["Host", "db.example.com"],
      ["Database", "sales"],
      ["Username", "reader"],
      ["Password", TYPED_SECRET],
    ] as const) {
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    }
    fireEvent.click(screen.getByRole("button", { name: "Register" }));

    expect(await screen.findByText(/Use one of: require, verify-ca, verify-full/)).toBeInTheDocument();
  });

  it("offers a Reader nothing they cannot do", async () => {
    stubFetch(json([SOURCE]));

    render(<DataSources orgId="org1" role="reader" />);

    expect(await screen.findByText("Pizza demo")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Register" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Test connection" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
    // …and says why, rather than looking like a screen that failed to load.
    expect(screen.getByText(/Only an Admin can add or change them/)).toBeInTheDocument();
  });

  it("shows no admin controls while the role is still unknown", async () => {
    stubFetch(json([SOURCE]));

    render(<DataSources orgId="org1" role={null} />);

    expect(await screen.findByText("Pizza demo")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Test connection" })).not.toBeInTheDocument();
  });

  it("says what went wrong when the list cannot be loaded", async () => {
    stubFetch(json({ detail: "Your role does not permit this action" }, 403));

    render(<DataSources orgId="org1" role="admin" />);

    expect(await screen.findByText("Your role does not permit this action")).toBeInTheDocument();
  });
});
