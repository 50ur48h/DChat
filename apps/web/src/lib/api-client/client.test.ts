import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiBaseUrl, fetchHealth } from "./client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("apiBaseUrl", () => {
  it("falls back to localhost when unconfigured", () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "");

    expect(apiBaseUrl()).toBe("http://localhost:8000");
  });

  it("strips trailing slashes so paths never double up", () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.example.com//");

    expect(apiBaseUrl()).toBe("https://api.example.com");
  });
});

describe("fetchHealth", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
  });

  it("returns the parsed payload", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ status: "ok", version: "0.1.0", git_sha: "abc123" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchHealth()).resolves.toEqual({
      status: "ok",
      version: "0.1.0",
      git_sha: "abc123",
    });
    expect(fetchMock).toHaveBeenCalledWith("http://api.test/healthz", expect.anything());
  });

  it("raises ApiError when the API is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(fetchHealth()).rejects.toBeInstanceOf(ApiError);
    await expect(fetchHealth()).rejects.toThrow("Could not reach the API at http://api.test");
  });

  it("raises ApiError on a non-2xx status", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "nope" }, 503)));

    await expect(fetchHealth()).rejects.toMatchObject({ name: "ApiError", status: 503 });
  });

  it("raises ApiError when the body does not match the contract", async () => {
    // A hand-written client is only honest if it verifies what it received.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ status: "ok" })));

    await expect(fetchHealth()).rejects.toThrow("did not match the expected shape");
  });

  it("raises ApiError when the body is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("<html>gateway</html>", { status: 200 })),
    );

    await expect(fetchHealth()).rejects.toThrow("non-JSON body");
  });
});
