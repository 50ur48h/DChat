import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Members } from "./members";

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

const MEMBERS = [
  { user_id: "u1", email: "alice@example.com", name: "Alice", role: "admin" },
  { user_id: "u2", email: null, name: "Bob", role: "reader" },
];

function stubFetch(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    ),
  );
}

describe("<Members />", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
    stubFetch(MEMBERS);
  });

  it("lets an Admin change roles and invite people", async () => {
    render(<Members orgId="org1" role="admin" />);

    expect(await screen.findByText("alice@example.com")).toBeInTheDocument();
    expect(screen.getAllByLabelText("Change role")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Send invite" })).toBeInTheDocument();
  });

  it("offers a Reader nothing they cannot do (B-008)", async () => {
    render(<Members orgId="org1" role="reader" />);

    // They can still see who is here — that is not the part they lack.
    expect(await screen.findByText("alice@example.com")).toBeInTheDocument();
    expect(screen.getByText("Bob")).toBeInTheDocument();

    expect(screen.queryByLabelText("Change role")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send invite" })).not.toBeInTheDocument();
    expect(screen.getByText("Only an Admin can change roles or invite people.")).toBeInTheDocument();
  });

  it("shows no admin controls while the role is still unknown", async () => {
    render(<Members orgId="org1" role={null} />);

    expect(await screen.findByText("alice@example.com")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send invite" })).not.toBeInTheDocument();
  });
});

describe("a way back in (B-017)", () => {
  function routeFetch(grants: unknown[]) {
    const calls: { url: string; init: RequestInit }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init: RequestInit = {}) => {
        calls.push({ url, init });
        const body = url.includes("/recovery-grants") ? grants : MEMBERS;
        return Promise.resolve(
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }),
    );
    return calls;
  }

  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
  });

  it("tells an Admin plainly when there is no way back", async () => {
    // The failure this feature exists to stop is silent: an organization is
    // fine right up until nobody can sign in. So "nothing armed" is stated,
    // not left as an empty panel.
    routeFetch([]);

    render(<Members orgId="org1" role="admin" />);

    expect(await screen.findByText(/no way back if its Admins lose access/i)).toBeInTheDocument();
  });

  it("does not show the panel to a Reader, who could not use it", async () => {
    // B-008: showing a control that can only earn a 403 teaches people the
    // product is broken rather than that they lack permission.
    routeFetch([]);

    render(<Members orgId="org1" role="reader" />);

    await screen.findByText("alice@example.com");
    expect(screen.queryByText(/If nobody can sign in/i)).not.toBeInTheDocument();
  });

  it("lists an armed grant with the date it runs out", async () => {
    // The expiry is on screen because a grant that lapses quietly recreates
    // B-017 exactly — the way back missing at the moment it is needed.
    routeFetch([
      {
        id: "g1",
        label: "Ops password manager",
        created_at: "2026-08-20T09:00:00Z",
        expires_at: "2027-08-20T09:00:00Z",
        state: "armed",
      },
    ]);

    render(<Members orgId="org1" role="admin" />);

    expect(await screen.findByText("Ops password manager")).toBeInTheDocument();
    expect(screen.getByText("armed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revoke" })).toBeInTheDocument();
  });

  it("offers no Revoke on a grant that is already spent", async () => {
    routeFetch([
      {
        id: "g1",
        label: "Used last year",
        created_at: "2026-08-20T09:00:00Z",
        expires_at: "2027-08-20T09:00:00Z",
        state: "used",
        used_at: "2026-09-01T09:00:00Z",
      },
    ]);

    render(<Members orgId="org1" role="admin" />);

    expect(await screen.findByText("Used last year")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Revoke" })).not.toBeInTheDocument();
  });
});
