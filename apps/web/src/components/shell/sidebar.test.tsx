import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Sidebar } from "./sidebar";

const path = { value: "/orgs/o1/conversations" };

vi.mock("next/navigation", () => ({
  usePathname: () => path.value,
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

const THREAD = {
  id: "c1",
  title: "Waste by outlet",
  created_at: "2026-08-25T10:00:00Z",
  message_count: 4,
  last_run_id: "r1",
  data_source_id: "d1",
  data_source_name: "Pizza (PostgreSQL)",
  archived_at: null,
};

const UNTITLED = { ...THREAD, id: "c2", title: null };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stubFetch(threads: unknown[] = [THREAD]) {
  const calls: { url: string; init: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init: RequestInit = {}) => {
      calls.push({ url, init });
      if (init.method === "PATCH") return Promise.resolve(json(THREAD));
      if (url.includes("/archive")) return Promise.resolve(json({ ...THREAD, archived_at: "now" }));
      return Promise.resolve(json(threads));
    }),
  );
  return calls;
}

const person = { email: "alice@example.com", name: "Alice" };

describe("<Sidebar />", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
    path.value = "/orgs/o1/conversations";
    window.localStorage.clear();
  });

  afterEach(() => {
    window.localStorage.clear();
  });

  it("lists your chats, with a way to start one", async () => {
    stubFetch();

    render(<Sidebar orgId="o1" orgName="Demo" person={person} />);

    expect(await screen.findByText("Waste by outlet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /New chat/ })).toHaveAttribute(
      "href",
      "/orgs/o1/conversations",
    );
    expect(screen.getByText("Demo")).toBeInTheDocument();
    expect(screen.getByText("alice@example.com")).toBeInTheDocument();
  });

  it("shows an untitled thread as something rather than as a blank", async () => {
    stubFetch([UNTITLED]);

    render(<Sidebar orgId="o1" orgName="Demo" person={person} />);

    expect(await screen.findByRole("link", { name: "New chat" })).toBeInTheDocument();
  });

  it("renames a chat in place", async () => {
    const calls = stubFetch();

    render(<Sidebar orgId="o1" orgName="Demo" person={person} />);

    fireEvent.click(await screen.findByRole("button", { name: /Rename Waste by outlet/ }));
    const box = screen.getByLabelText("Chat title");
    fireEvent.change(box, { target: { value: "Waste, by outlet" } });
    fireEvent.keyDown(box, { key: "Enter" });

    await waitFor(() => {
      const patch = calls.find((call) => call.init.method === "PATCH");
      expect(patch?.url).toContain("/v1/orgs/o1/conversations/c1");
      expect(JSON.parse(String(patch?.init.body))).toEqual({ title: "Waste, by outlet" });
    });
  });

  it("says archive, does archive, and says it can be undone", async () => {
    /**
     * **The word has to match the action** (D-039, and the owner's instruction).
     * A trash icon that quietly archived would be the same class of lie as a
     * badge reading *answered* on a refusal (B-133). So: the control is a word,
     * it is the true word, and the confirmation says the reverse exists — which
     * `<ArchivedChats />` in Settings is what makes true.
     */
    const calls = stubFetch();

    render(<Sidebar orgId="o1" orgName="Demo" person={person} />);

    // Nothing anywhere offers to delete.
    expect(await screen.findByText("Waste by outlet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Archive Waste by outlet/ }));
    expect(screen.getByText(/you can bring it back/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Archive" }));

    await waitFor(() => {
      const archived = calls.find((call) => call.url.includes("/archive"));
      expect(archived?.init.method).toBe("POST");
      expect(JSON.parse(String(archived?.init.body))).toEqual({ archived: true });
    });
  });

  it("collapses, expands, and remembers which across a remount", async () => {
    stubFetch();

    const first = render(<Sidebar orgId="o1" orgName="Demo" person={person} />);
    fireEvent.click(await screen.findByRole("button", { name: "Collapse the sidebar" }));

    expect(screen.getByRole("button", { name: "Expand the sidebar" })).toBeInTheDocument();
    expect(screen.queryByText("Waste by outlet")).not.toBeInTheDocument();

    // A remount is what a reload looks like to the component.
    first.unmount();
    render(<Sidebar orgId="o1" orgName="Demo" person={person} />);

    expect(await screen.findByRole("button", { name: "Expand the sidebar" })).toBeInTheDocument();
  });

  it("marks the chat you are looking at", async () => {
    path.value = "/orgs/o1/conversations/c1";
    stubFetch();

    render(<Sidebar orgId="o1" orgName="Demo" person={person} />);

    expect(await screen.findByRole("link", { name: "Waste by outlet" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("does not report an empty list before it has loaded one", async () => {
    // The same mistake the conversations screen once made: an empty state
    // standing in for a loading one reads as "you have no chats" rather than
    // "wait".
    let release: ((value: Response) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            release = resolve;
          }),
      ),
    );

    render(<Sidebar orgId="o1" orgName="Demo" person={person} />);

    // Awaited, not asserted synchronously: resolving the token is itself async,
    // so `fetch` — and therefore `release` — does not exist yet on the first
    // tick, and a bare `expect` here would pass while leaving nothing to release.
    await waitFor(() => expect(release).toBeDefined());
    // The skeleton's single announced label, not the word "Loading…": a screen
    // reader should hear this once rather than four anonymous boxes (D-049).
    expect(screen.getByText("Loading your chats")).toBeInTheDocument();
    expect(screen.queryByText(/No chats yet/)).not.toBeInTheDocument();

    release?.(json([]));
    expect(await screen.findByText(/No chats yet/)).toBeInTheDocument();
  });
});
