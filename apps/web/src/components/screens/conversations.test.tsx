import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversations } from "./conversations";

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

const PIZZA_PG = {
  id: "d1",
  name: "Pizza (PostgreSQL)",
  engine: "pg",
  host: "seed-pizza-pg",
  port: 5432,
  database: "pizza",
  host_display: "seed-pizza-pg:5432/pizza",
  status: "verified",
  secret_ref: "ds/o1/d1/credentials",
  username_last4: "only",
  tls_mode: "prefer",
  readonly_verified: true,
  last_verified_at: "2026-08-12T10:00:00Z",
  created_by: "u1",
  created_at: "2026-08-12T10:00:00Z",
};

const PIZZA_MSSQL = { ...PIZZA_PG, id: "d2", name: "Pizza (SQL Server)", engine: "mssql" };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** Nothing chosen — the state every organization is in until an Admin picks. */
const NOTHING_CHOSEN = { data_source_id: null, data_source_name: null };

/** Routes on the URL, because this screen fetches three things at once. */
function routeFetch(
  sources: unknown[],
  conversations: unknown[] = [],
  chosen: unknown = NOTHING_CHOSEN,
) {
  const calls: { url: string; init: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init: RequestInit = {}) => {
      calls.push({ url, init });
      // Checked before `/data-sources`, though the two do not actually collide:
      // the active route is `active-data-source`, singular and hyphenated.
      if (url.includes("/active-data-source")) return Promise.resolve(json(chosen));
      if (url.includes("/data-sources")) return Promise.resolve(json(sources));
      if (init.method === "POST") return Promise.resolve(json({ ...CREATED }));
      return Promise.resolve(json(conversations));
    }),
  );
  return calls;
}

const CREATED = {
  id: "c-new",
  title: null,
  created_at: "2026-08-15T12:00:00Z",
  message_count: 0,
  last_run_id: null,
  data_source_id: "d1",
  data_source_name: "Pizza (PostgreSQL)",
};

describe("<Conversations />", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
  });

  it("preselects the only database, because there is nothing to choose", async () => {
    routeFetch([PIZZA_PG]);

    render(<Conversations orgId="o1" />);

    const picker = (await screen.findByLabelText("Database")) as HTMLSelectElement;
    expect(picker.value).toBe("d1");
    expect(screen.getByRole("button", { name: "Start" })).toBeEnabled();
  });

  it("chooses nothing when there is more than one, and will not start until you do", async () => {
    routeFetch([PIZZA_PG, PIZZA_MSSQL]);

    render(<Conversations orgId="o1" />);

    const picker = (await screen.findByLabelText("Database")) as HTMLSelectElement;
    // Selecting the first would be exactly the guess the scheduler refuses to
    // make (WP7.2c) — a confident answer drawn from the wrong database is the
    // one mistake that does not look like a mistake.
    expect(picker.value).toBe("");
    expect(screen.getByRole("button", { name: "Start" })).toBeDisabled();
    expect(screen.getByText(/nothing will guess for you/)).toBeInTheDocument();
  });

  it("points at the screen that fixes it when no database is registered", async () => {
    routeFetch([]);

    render(<Conversations orgId="o1" />);

    // A picker with nothing in it and a button that can only fail would be
    // worse than a sentence saying what to do.
    expect(await screen.findByText(/No database is registered/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Database")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "data sources" })).toHaveAttribute(
      "href",
      "/orgs/o1/data-sources",
    );
  });

  it("lists your conversations with the database each is about", async () => {
    routeFetch(
      [PIZZA_PG],
      [
        {
          id: "c1",
          title: "How many orders were placed in July 2026?",
          created_at: "2026-08-15T09:00:00Z",
          message_count: 2,
          last_run_id: "r1",
          data_source_id: "d1",
          data_source_name: "Pizza (PostgreSQL)",
        },
      ],
    );

    render(<Conversations orgId="o1" />);

    expect(
      await screen.findByRole("link", { name: "How many orders were placed in July 2026?" }),
    ).toHaveAttribute("href", "/orgs/o1/conversations/c1");
    expect(screen.getAllByText("Pizza (PostgreSQL)").length).toBeGreaterThan(0);
    expect(screen.getByText(/2 messages/)).toBeInTheDocument();
  });

  it("marks a conversation that named no database", async () => {
    routeFetch(
      [PIZZA_PG],
      [
        {
          id: "c2",
          title: "An older thread",
          created_at: "2026-08-14T09:00:00Z",
          message_count: 1,
          last_run_id: null,
          data_source_id: null,
          data_source_name: null,
        },
      ],
    );

    render(<Conversations orgId="o1" />);

    // Legal, not broken: every conversation written before revision 0014 has
    // this shape. It is labelled rather than hidden.
    expect(await screen.findByText("no database chosen")).toBeInTheDocument();
  });
});

const THREAD = {
  id: "c1",
  title: "Revenue by month",
  created_at: "2026-08-20T09:00:00Z",
  message_count: 4,
  last_run_id: "r1",
  data_source_id: "d1",
  data_source_name: "Pizza (PostgreSQL)",
  archived_at: null,
};

describe("putting a thread away", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
  });

  it("says Archive, never Delete", async () => {
    // **D-039, and the owner's condition on it.** Nothing under this thread is
    // removed — the runs, their events and their query executions all stay — so
    // a control labelled "Delete" would be telling the person something the
    // product does not do. This asserts the word, because the word is the promise.
    routeFetch([PIZZA_PG], [THREAD]);

    render(<Conversations orgId="o1" />);

    expect(await screen.findByRole("button", { name: "Archive" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete/i })).not.toBeInTheDocument();
  });

  it("archives through the API and asks for the list again", async () => {
    const calls = routeFetch([PIZZA_PG], [THREAD]);

    render(<Conversations orgId="o1" />);
    (await screen.findByRole("button", { name: "Archive" })).click();

    await vi.waitFor(() =>
      expect(calls.some((call) => call.url.includes("/archive"))).toBe(true),
    );
    const archived = calls.find((call) => call.url.includes("/archive"));
    expect(archived?.init.method).toBe("POST");
    expect(JSON.parse(String(archived?.init.body))).toEqual({ archived: true });
  });

  it("does not report an empty list before it has loaded one", async () => {
    // The list used to start at `[]`, so every visitor was told "Nothing yet"
    // for as long as the fetch took — an empty state standing in for a loading
    // one, which reads as "you have no conversations" rather than "wait".
    let release: ((value: Response) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (url.includes("/active-data-source")) {
          return Promise.resolve(json(NOTHING_CHOSEN));
        }
        if (url.includes("/data-sources")) {
          return Promise.resolve(json([PIZZA_PG]));
        }
        // Only the conversation list is left hanging, so `release` below
        // resolves the one call this test is actually about.
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      }),
    );

    render(<Conversations orgId="o1" />);

    // Both cards are waiting on the same `Promise.all`, so both say so.
    expect(await screen.findAllByText("Loading…")).not.toHaveLength(0);
    expect(screen.queryByText(/Nothing yet/)).not.toBeInTheDocument();
    release?.(json([]));
    expect(await screen.findByText(/Nothing yet/)).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // The organization has chosen, so a member does not (D-045)
  // -------------------------------------------------------------------------

  const CHOSEN = { data_source_id: "d1", data_source_name: "Pizza (PostgreSQL)" };

  it("offers no picker once the organization has chosen, and still names the database", async () => {
    routeFetch([PIZZA_PG, PIZZA_MSSQL], [], CHOSEN);

    render(<Conversations orgId="o1" />);

    expect(await screen.findByText("Pizza (PostgreSQL)")).toBeInTheDocument();
    // Two sources registered — today's screen would force a choice. The Admin
    // has already made it, so there is nothing here to decide.
    expect(screen.queryByLabelText("Database")).not.toBeInTheDocument();
    expect(screen.queryByText(/more than one database/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start" })).toBeEnabled();
  });

  it("sends no data source at all, so the API stamps the thread", async () => {
    /**
     * **The browser-path reachability proof for D-045.** The API test proves the
     * stamp works when a caller sends nothing; this proves the screen a person
     * actually uses *is* that caller. Sending the same id from here would reach
     * the same database and leave the stamp exercised only by tests — built,
     * tested and never reached, which is the defect this project produces most.
     */
    const calls = routeFetch([PIZZA_PG], [], CHOSEN);

    render(<Conversations orgId="o1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Start" }));

    await vi.waitFor(() =>
      expect(calls.some((call) => call.init.method === "POST")).toBe(true),
    );
    const created = calls.find((call) => call.init.method === "POST");
    expect(JSON.parse(String(created?.init.body ?? "{}"))).toEqual({});
  });

  it("still makes a member choose when the organization has not", async () => {
    // D-045 fills a blank; it does not remove the refusal. With two sources and
    // no organization choice, nothing guesses — exactly as before.
    routeFetch([PIZZA_PG, PIZZA_MSSQL], []);

    render(<Conversations orgId="o1" />);

    expect(await screen.findByLabelText("Database")).toBeInTheDocument();
    expect(screen.getByText(/more than one database/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start" })).toBeDisabled();
  });
});
