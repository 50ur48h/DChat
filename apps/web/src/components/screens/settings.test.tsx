import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Settings } from "./settings";

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

const ME = {
  subject: "sub-alice",
  user_id: "u1",
  email: "alice@example.com",
  name: "Alice",
  memberships: [{ org_id: "o1", org_name: "Demo", role: "admin" }],
};

const CHOSEN = { data_source_id: "d1", data_source_name: "Pizza (PostgreSQL)" };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stubFetch(
  options: {
    me?: unknown;
    chosen?: unknown;
    archived?: unknown[];
    showCost?: boolean;
  } = {},
) {
  const calls: { url: string; init: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init: RequestInit = {}) => {
      calls.push({ url, init });
      if (url.includes("/active-data-source")) return Promise.resolve(json(options.chosen ?? CHOSEN));
      // The settings page loads its three facts together and renders none of
      // them if any request fails, which is how it already treats the active
      // data source. A stub missing this route blanks the whole screen.
      if (url.includes("/show-run-cost")) {
        return Promise.resolve(json({ visible: options.showCost ?? true }));
      }
      if (url.includes("/conversations")) return Promise.resolve(json(options.archived ?? []));
      if (url.endsWith("/v1/me")) return Promise.resolve(json(options.me ?? ME));
      return Promise.resolve(json({}));
    }),
  );
  return calls;
}

describe("<Settings />", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
    role.value = "admin";
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  afterEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  it("shows who you are, your role, and what the organization answers from", async () => {
    stubFetch();

    render(<Settings orgId="o1" />);

    expect(await screen.findByText("alice@example.com")).toBeInTheDocument();
    expect(screen.getByText("admin")).toBeInTheDocument();
    expect(screen.getByText("Pizza (PostgreSQL)")).toBeInTheDocument();
  });

  it("names the unbuilt sections without pretending they work", async () => {
    /**
     * **An honest gap, not a convincing lie.** A disabled select under "Tone"
     * would look operable and do nothing — the interface asserting something the
     * system cannot back, which is B-133's defect in a different costume. So
     * these are prose and a badge, and there is no control to press.
     */
    stubFetch();

    render(<Settings orgId="o1" />);

    for (const unbuilt of ["System instructions", "Tone", "Preferences"]) {
      expect(await screen.findByText(unbuilt)).toBeInTheDocument();
    }
    expect(screen.getAllByText("Coming soon")).toHaveLength(3);

    // Nothing in those sections is a control, disabled or otherwise.
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("switches to dark and remembers it", async () => {
    stubFetch();

    render(<Settings orgId="o1" />);

    // Light is where everyone starts, whatever the operating system says.
    expect(document.documentElement.getAttribute("data-theme")).toBeNull();

    fireEvent.click(await screen.findByRole("button", { name: "Dark" }));

    await waitFor(() =>
      expect(document.documentElement.getAttribute("data-theme")).toBe("dark"),
    );
    expect(window.localStorage.getItem("dataagent.theme")).toBe("dark");

    fireEvent.click(screen.getByRole("button", { name: "Light" }));
    await waitFor(() => expect(document.documentElement.getAttribute("data-theme")).toBeNull());
    expect(window.localStorage.getItem("dataagent.theme")).toBe("light");
  });

  it("hides admin destinations from a Reader (B-008)", async () => {
    role.value = "reader";
    // Nothing chosen, so the Reader-specific wording below is reachable at all.
    stubFetch({
      me: { ...ME, memberships: [{ org_id: "o1", org_name: "Demo", role: "reader" }] },
      chosen: { data_source_id: null, data_source_name: null },
    });

    render(<Settings orgId="o1" />);

    expect(await screen.findByRole("link", { name: "Members" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Data sources" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Documents" })).not.toBeInTheDocument();
    // And the Reader is told who can choose, rather than shown a control.
    expect(screen.getByText(/An Admin can choose one/)).toBeInTheDocument();
  });

  it("restores an archived chat, which is what makes 'you can bring it back' true", async () => {
    const archived = [
      {
        id: "c9",
        title: "Old question",
        created_at: "2026-08-20T09:00:00Z",
        message_count: 2,
        last_run_id: null,
        data_source_id: "d1",
        data_source_name: "Pizza (PostgreSQL)",
        archived_at: "2026-08-24T09:00:00Z",
      },
    ];
    const calls = stubFetch({ archived });

    render(<Settings orgId="o1" />);

    expect(await screen.findByText("Old question")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));

    await waitFor(() => {
      const restored = calls.find((call) => call.url.includes("/archive"));
      expect(restored?.init.method).toBe("POST");
      expect(JSON.parse(String(restored?.init.body))).toEqual({ archived: false });
    });
  });

  it("turns spend off for the whole organization, and says so", async () => {
    // **D-066, and it is a switch rather than a permission.** Off hides cost
    // from everyone including the Admin who set it, so the label must not read
    // as "hidden from others".
    const calls = stubFetch({ showCost: true });

    render(<Settings orgId="o1" />);
    expect(await screen.findByText("Shown to everyone")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Hide cost on answers" }));

    await waitFor(() =>
      expect(
        calls.some(
          (call) => call.url.includes("/show-run-cost") && call.init.method === "PUT",
        ),
      ).toBe(true),
    );
    const sent = calls.filter((call) => call.init.method === "PUT").at(-1);
    expect(JSON.parse(String(sent?.init.body))).toEqual({ visible: false });
  });

  it("offers a Reader no way to change it (B-008)", async () => {
    role.value = "reader";
    stubFetch({
      me: { ...ME, memberships: [{ org_id: "o1", org_name: "Demo", role: "reader" }] },
      showCost: false,
    });

    render(<Settings orgId="o1" />);

    expect(await screen.findByText("Hidden from everyone")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /cost on answers/ }),
    ).not.toBeInTheDocument();
  });
});
