/**
 * Typed fetch helper for the data-agent API.
 *
 * The browser talks to the API directly (architecture Part 3.1) — no BFF, no
 * server-side proxy — so the base URL is public configuration and nothing
 * secret passes through this module. The bearer token is supplied per call by
 * the session, which is why `createApi` takes a token getter rather than a
 * token: a token read once would be the one that expires.
 */

import {
  isAccepted,
  isHealth,
  isInvitation,
  isMe,
  isMember,
  type Accepted,
  type Health,
  type Invitation,
  type Me,
  type Member,
} from "./types";

/** A failed API call, carrying enough to render an honest message. */
export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number, options?: ErrorOptions) {
    super(message, options);
    this.name = "ApiError";
    this.status = status;
  }
}

export function apiBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_URL;
  return (configured && configured.length > 0 ? configured : "http://localhost:8000").replace(
    /\/+$/,
    "",
  );
}

interface RequestOptions {
  method?: string | undefined;
  body?: unknown;
  token?: string | null | undefined;
  signal?: AbortSignal | null | undefined;
}

async function request(path: string, options: RequestOptions = {}): Promise<unknown> {
  const { method = "GET", body, token, signal = null } = options;

  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}${path}`, {
      method,
      headers,
      signal,
      body: body === undefined ? null : JSON.stringify(body),
    });
  } catch (cause) {
    throw new ApiError(`Could not reach the API at ${apiBaseUrl()}`, 0, { cause });
  }

  if (response.status === 204) return null;

  let payload: unknown = null;
  const text = await response.text();
  if (text.length > 0) {
    try {
      payload = JSON.parse(text) as unknown;
    } catch (cause) {
      throw new ApiError(`API returned a non-JSON body for ${path}`, response.status, { cause });
    }
  }

  if (!response.ok) {
    // The API's own message when it gave one: "Your role does not permit this
    // action" is far more use to a person than "403".
    const detail = (payload as { detail?: unknown } | null)?.detail;
    throw new ApiError(
      typeof detail === "string" ? detail : `API returned ${response.status} for ${path}`,
      response.status,
    );
  }

  return payload;
}

function narrow<T>(value: unknown, guard: (candidate: unknown) => candidate is T, what: string): T {
  if (!guard(value)) {
    throw new ApiError(`The API's ${what} response did not match the expected shape`, 200);
  }
  return value;
}

/** `GET /healthz` — unauthenticated, so it stands apart from the rest. */
export async function fetchHealth(signal?: AbortSignal): Promise<Health> {
  return narrow(await request("/healthz", { signal: signal ?? null }), isHealth, "health");
}

export interface Api {
  me(): Promise<Me>;
  createOrg(name: string): Promise<void>;
  members(orgId: string): Promise<Member[]>;
  invite(orgId: string, email: string, role: string): Promise<Invitation>;
  acceptInvitation(token: string): Promise<Accepted>;
  changeRole(orgId: string, userId: string, role: string): Promise<void>;
  removeMember(orgId: string, userId: string): Promise<void>;
}

/** Binds the API to a session's token getter. */
export function createApi(getToken: () => Promise<string | null>): Api {
  const call = async (path: string, options: Omit<RequestOptions, "token"> = {}) =>
    request(path, { ...options, token: await getToken() });

  return {
    async me() {
      return narrow(await call("/v1/me"), isMe, "profile");
    },
    async createOrg(name) {
      await call("/v1/orgs", { method: "POST", body: { name } });
    },
    async members(orgId) {
      const payload = await call(`/v1/orgs/${orgId}/members`);
      if (!Array.isArray(payload) || !payload.every(isMember)) {
        throw new ApiError("The API's members response did not match the expected shape", 200);
      }
      return payload;
    },
    async invite(orgId, email, role) {
      return narrow(
        await call(`/v1/orgs/${orgId}/invitations`, { method: "POST", body: { email, role } }),
        isInvitation,
        "invitation",
      );
    },
    async acceptInvitation(token) {
      return narrow(
        await call("/v1/invitations/accept", { method: "POST", body: { token } }),
        isAccepted,
        "invitation acceptance",
      );
    },
    async changeRole(orgId, userId, role) {
      await call(`/v1/orgs/${orgId}/members/${userId}`, { method: "PATCH", body: { role } });
    },
    async removeMember(orgId, userId) {
      await call(`/v1/orgs/${orgId}/members/${userId}`, { method: "DELETE" });
    },
  };
}
