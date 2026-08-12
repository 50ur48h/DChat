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
