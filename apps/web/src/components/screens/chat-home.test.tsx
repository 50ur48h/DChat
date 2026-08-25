import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ChatHome } from "./chat-home";

const pushed: string[] = [];

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: (href: string) => {
      pushed.push(href);
    },
    replace: (href: string) => {
      pushed.push(href);
    },
  }),
}));

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

const role = { value: "admin" as string | null };

vi.mock("@/lib/use-org-role", () => ({
  useOrgRole: () => ({ role: role.value, loading: false }),
}));

const CHOSEN = { data_source_id: "d1", data_source_name: "Pizza (PostgreSQL)" };
const NOTHING_CHOSEN = { data_source_id: null, data_source_name: null };

const CREATED = {
  id: "c-new",
  title: null,
  created_at: "2026-08-25T12:00:00Z",
  message_count: 0,
  last_run_id: null,
  data_source_id: "d1",
  data_source_name: "Pizza (PostgreSQL)",
  archived_at: null,
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** Routes on the URL, because this screen asks three different things. */
const SOURCE = {
  id: "d1",
  name: "Pizza (PostgreSQL)",
  engine: "pg",
  host: "localhost",
  port: 5432,
  database: "pizza",
  host_display: "localhost:5432/pizza",
  status: "verified",
  secret_ref: "ds/o1/d1/c",
  username_last4: "only",
  tls_mode: "prefer",
  readonly_verified: true,
  last_verified_at: null,
  created_by: null,
  created_at: "2026-08-25T09:00:00Z",
};

const SECOND_SOURCE = { ...SOURCE, id: "d2", name: "Warehouse (PostgreSQL)" };

function routeFetch(
  options: { chosen?: unknown; askFails?: boolean; sources?: unknown[] } = {},
) {
  const calls: { url: string; init: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init: RequestInit = {}) => {
      calls.push({ url, init });
      if (url.includes("/active-data-source")) {
        return Promise.resolve(json(options.chosen ?? CHOSEN));
      }
      if (url.includes("/data-sources")) {
        return Promise.resolve(json(options.sources ?? [SOURCE]));
      }
      if (url.includes("/messages")) {
        return options.askFails
          ? Promise.resolve(json({ detail: "the model is unavailable" }, 503))
          : Promise.resolve(
              json({ run_id: "r1", message_id: "m1", status: "queued", created: true }, 202),
            );
      }
      if (url.endsWith("/conversations")) return Promise.resolve(json(CREATED, 201));
      return Promise.resolve(json({}));
    }),
  );
  return calls;
}

describe("<ChatHome />", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
    pushed.length = 0;
    role.value = "admin";
  });

  it("offers a composer and names the database, with nothing to configure", async () => {
    routeFetch();

    render(<ChatHome orgId="o1" />);

    expect(await screen.findByText(/Pizza \(PostgreSQL\)/)).toBeInTheDocument();
    expect(screen.getByLabelText("Your question")).toBeEnabled();
    // The picker this screen replaced is gone, not hidden.
    expect(screen.queryByLabelText("Database")).not.toBeInTheDocument();
  });

  it("creates the thread, asks the question, then navigates to it", async () => {
    /**
     * **The reachability proof for the new home.** It asserts the three calls
     * happen in order and that the person ends up in the thread — the whole
     * flow the sidebar's "New chat" and the app's front door both lead to. A
     * test of `send()` in isolation would prove none of it.
     */
    const calls = routeFetch();

    render(<ChatHome orgId="o1" />);

    const box = await screen.findByLabelText("Your question");
    fireEvent.change(box, { target: { value: "how many orders are there?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(pushed).toEqual(["/orgs/o1/conversations/c-new"]));

    const posts = calls.filter((call) => call.init.method === "POST");
    expect(posts[0]?.url).toContain("/v1/orgs/o1/conversations");
    // No data source named: the API stamps it from the organization (D-045).
    expect(JSON.parse(String(posts[0]?.init.body ?? "{}"))).toEqual({});
    expect(posts[1]?.url).toContain("/conversations/c-new/messages");
    expect(JSON.parse(String(posts[1]?.init.body)).content).toBe("how many orders are there?");
  });

  it("sends on Enter and makes a new line on Shift+Enter", async () => {
    routeFetch();

    render(<ChatHome orgId="o1" />);

    const box = await screen.findByLabelText("Your question");
    fireEvent.change(box, { target: { value: "still typing" } });
    fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
    expect(pushed).toEqual([]);

    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(pushed).toEqual(["/orgs/o1/conversations/c-new"]));
  });

  it("keeps the draft and reuses the half-made thread when the question fails", async () => {
    /**
     * Navigating away on failure would throw away the text, which is the one
     * thing a person cannot get back. And a retry must not leave a second empty
     * chat behind, so the created thread is held and reused.
     */
    const calls = routeFetch({ askFails: true });

    render(<ChatHome orgId="o1" />);

    const box = await screen.findByLabelText("Your question");
    fireEvent.change(box, { target: { value: "how many orders are there?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText(/the model is unavailable/)).toBeInTheDocument();
    expect(pushed).toEqual([]);
    expect(box).toHaveValue("how many orders are there?");

    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => {
      const created = calls.filter(
        (call) => call.init.method === "POST" && call.url.endsWith("/conversations"),
      );
      expect(created).toHaveLength(1);
    });
  });

  it("still asks when one database is registered and none is chosen", async () => {
    /**
     * **The screen must not refuse on the platform's behalf.** Gating purely on
     * the Admin's choice told a lie: `resolve_data_source` resolves a single
     * registered source perfectly well, so *"nothing to ask about"* would have
     * been false, and the composer disabled for a question that would have been
     * answered.
     */
    routeFetch({ chosen: NOTHING_CHOSEN, sources: [SOURCE] });

    render(<ChatHome orgId="o1" />);

    expect(await screen.findByText(/Pizza \(PostgreSQL\)/)).toBeInTheDocument();
    expect(screen.getByLabelText("Your question")).toBeEnabled();
  });

  it("shows your question while it is sending, and takes it back if it fails", async () => {
    /**
     * **D-049.** Rendering the question immediately removes the blank moment
     * between pressing Send and arriving in the thread — but anything shown
     * before the server has confirmed it must look provisional, and must not
     * harden into something that looks saved when the write did not happen.
     */
    // The ask is held open, so the in-flight state can actually be observed.
    // Against a stub that resolves in a microtask it exists for less than a
    // frame, and asserting it would be a race rather than a check.
    let releaseAsk: ((value: Response) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (url.includes("/active-data-source")) return Promise.resolve(json(CHOSEN));
        if (url.includes("/data-sources")) return Promise.resolve(json([SOURCE]));
        if (url.includes("/messages")) {
          return new Promise<Response>((resolve) => {
            releaseAsk = resolve;
          });
        }
        if (url.endsWith("/conversations")) return Promise.resolve(json(CREATED, 201));
        return Promise.resolve(json({}));
      }),
    );

    render(<ChatHome orgId="o1" />);

    const box = await screen.findByLabelText("Your question");
    fireEvent.change(box, { target: { value: "how many orders are there?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    // Shown at once, said to be in flight rather than done, and the words are
    // the ones the person typed.
    expect(await screen.findByText("Sending your question…")).toBeInTheDocument();
    expect(screen.getAllByText("how many orders are there?").length).toBeGreaterThan(0);

    await waitFor(() => expect(releaseAsk).toBeDefined());
    releaseAsk?.(json({ detail: "the model is unavailable" }, 503));

    // The send failed, so the provisional bubble goes and the reason takes its
    // place. What must never happen is the question staying on screen looking
    // stored when nothing stored it.
    expect(await screen.findByText(/the model is unavailable/)).toBeInTheDocument();
    expect(screen.queryByText("Sending your question…")).not.toBeInTheDocument();
    expect(box).toHaveValue("how many orders are there?");
  });

  it("says which of the two reasons it cannot ask: several databases", async () => {
    routeFetch({ chosen: NOTHING_CHOSEN, sources: [SOURCE, SECOND_SOURCE] });

    render(<ChatHome orgId="o1" />);

    expect(await screen.findByText(/more than one database/)).toBeInTheDocument();
    expect(screen.getByText(/Choose one in Settings/)).toBeInTheDocument();
    expect(screen.getByLabelText("Your question")).toBeDisabled();
  });

  it("says which of the two reasons it cannot ask: none registered", async () => {
    routeFetch({ chosen: NOTHING_CHOSEN, sources: [] });

    render(<ChatHome orgId="o1" />);

    expect(await screen.findByText(/No database is registered yet/)).toBeInTheDocument();
    expect(screen.getByLabelText("Your question")).toBeDisabled();
  });

  it("tells a Reader who can fix it instead", async () => {
    role.value = "reader";
    routeFetch({ chosen: NOTHING_CHOSEN, sources: [] });

    render(<ChatHome orgId="o1" />);

    expect(await screen.findByText(/An Admin can add one/)).toBeInTheDocument();
    expect(screen.queryByText(/Settings → Data sources/)).not.toBeInTheDocument();
  });

  it("gives a Reader the same composer when a database is chosen", async () => {
    // Architecture 6.2 grants asking to every role. A Reader who cannot ask is
    // a broken product, not a tightened one.
    role.value = "reader";
    routeFetch();

    render(<ChatHome orgId="o1" />);

    expect(await screen.findByLabelText("Your question")).toBeEnabled();
  });
});
