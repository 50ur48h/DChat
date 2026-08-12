import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, createApi } from "./client";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const ME = {
  subject: "alice",
  user_id: "u1",
  email: "alice@example.com",
  name: null,
  memberships: [{ org_id: "o1", org_name: "Acme", role: "admin" }],
};

describe("createApi", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
  });

  it("attaches the bearer token from the session, per call", async () => {
    // A fresh Response per call: a body can only be read once, and reusing
    // one object would fail for reasons that have nothing to do with the code.
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(json(ME)));
    vi.stubGlobal("fetch", fetchMock);
    let issued = 0;
    const api = createApi(async () => `token-${++issued}`);

    await api.me();
    await api.me();

    const headers = fetchMock.mock.calls.map(
      (call) => (call[1] as RequestInit).headers as Record<string, string>,
    );
    // Two calls, two reads: a token captured once would be the one that expires.
    expect(headers.map((h) => h.Authorization)).toEqual(["Bearer token-1", "Bearer token-2"]);
  });

  it("sends no Authorization header when nobody is signed in", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(ME));
    vi.stubGlobal("fetch", fetchMock);

    await createApi(async () => null).me();

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
  });

  it("surfaces the API's own message rather than a bare status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(json({ detail: "Your role does not permit this action" }, 403)),
    );

    await expect(createApi(async () => "t").members("o1")).rejects.toThrow(
      "Your role does not permit this action",
    );
  });

  it("reports the last-Admin conflict verbatim", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(json({ detail: "This is the only Admin." }, 409)),
    );

    await expect(createApi(async () => "t").changeRole("o1", "u1", "reader")).rejects.toMatchObject({
      status: 409,
      message: "This is the only Admin.",
    });
  });

  it("rejects a response that does not match the contract", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ subject: "alice" })));

    await expect(createApi(async () => "t").me()).rejects.toBeInstanceOf(ApiError);
  });

  it("handles a 204 with no body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));

    await expect(createApi(async () => "t").removeMember("o1", "u1")).resolves.toBeUndefined();
  });

  it("rejects a members list containing something that is not a member", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json([{ user_id: "u1" }])));

    await expect(createApi(async () => "t").members("o1")).rejects.toThrow("did not match");
  });
});
