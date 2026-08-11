import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AcceptInvite } from "./accept-invite";

const search = { value: new URLSearchParams() };

vi.mock("next/navigation", () => ({
  useSearchParams: () => search.value,
}));

const session = {
  mode: "dev" as const,
  who: "bob",
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

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("<AcceptInvite />", () => {
  beforeEach(() => {
    search.value = new URLSearchParams();
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
  });

  it("prefills the token from the invitation link", async () => {
    search.value = new URLSearchParams("token=abc123");
    vi.stubGlobal("fetch", vi.fn());

    render(<AcceptInvite />);

    await waitFor(() =>
      expect(screen.getByLabelText("Invitation token")).toHaveValue("abc123"),
    );
  });

  it("confirms which organization you joined, and with what role", async () => {
    search.value = new URLSearchParams("token=abc123");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() =>
        Promise.resolve(json({ org_id: "o1", org_name: "Acme", role: "reader" })),
      ),
    );

    render(<AcceptInvite />);
    (await screen.findByRole("button", { name: "Join" })).click();

    expect(await screen.findByText("You're in")).toBeInTheDocument();
    expect(screen.getByText("reader")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Go to Acme/ })).toHaveAttribute("href", "/orgs/o1");
  });

  it("shows the API's wording for a bad token, without guessing why", async () => {
    search.value = new URLSearchParams("token=nope");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() =>
        Promise.resolve(
          json({ detail: "That invitation is not valid. Ask an admin for a new one." }, 400),
        ),
      ),
    );

    render(<AcceptInvite />);
    (await screen.findByRole("button", { name: "Join" })).click();

    expect(
      await screen.findByText("That invitation is not valid. Ask an admin for a new one."),
    ).toBeInTheDocument();
  });

  it("keeps Join disabled until there is a token to send", () => {
    vi.stubGlobal("fetch", vi.fn());

    render(<AcceptInvite />);

    expect(screen.getByRole("button", { name: "Join" })).toBeDisabled();
  });
});
