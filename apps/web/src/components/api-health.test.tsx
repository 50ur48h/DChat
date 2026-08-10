import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiHealth } from "./api-health";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("<ApiHealth />", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
  });

  it("shows the build identity once the API answers", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ status: "ok", version: "0.1.0", git_sha: "abc123" }),
      ),
    );

    render(<ApiHealth />);

    expect(await screen.findByText("Healthy")).toBeInTheDocument();
    expect(screen.getByText("0.1.0")).toBeInTheDocument();
    expect(screen.getByText("abc123")).toBeInTheDocument();
  });

  it("says the API is unreachable instead of rendering nothing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    render(<ApiHealth />);

    expect(await screen.findByText("Unreachable")).toBeInTheDocument();
    expect(
      screen.getByText("Could not reach the API at http://api.test"),
    ).toBeInTheDocument();
  });

  it("reports a failing API by status rather than claiming health", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, 500)));

    render(<ApiHealth />);

    expect(await screen.findByText("Unreachable")).toBeInTheDocument();
    expect(screen.getByText(/API returned 500/)).toBeInTheDocument();
  });

  it("shows which URL it is probing", () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));

    render(<ApiHealth />);

    expect(screen.getByText("Checking…")).toBeInTheDocument();
    expect(screen.getByText("http://api.test/healthz")).toBeInTheDocument();
  });
});
