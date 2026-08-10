/**
 * Typed fetch helper for the data-agent API.
 *
 * The browser talks to the API directly (architecture Part 3.1) — there is no
 * BFF and no server-side proxy — so the base URL is public configuration.
 * Nothing secret ever passes through this module.
 */

import { isHealth, type Health } from "./types";

/** A failed API call, carrying enough to render an honest message. */
export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number, options?: ErrorOptions) {
    super(message, options);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Base URL of the API.
 *
 * Read at call time rather than module load, so tests and the standalone
 * server both see the value that is actually in the environment.
 */
export function apiBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_URL;
  return (configured && configured.length > 0 ? configured : "http://localhost:8000").replace(
    /\/+$/,
    "",
  );
}

async function request(path: string, init?: RequestInit): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}${path}`, {
      ...init,
      headers: { Accept: "application/json", ...init?.headers },
    });
  } catch (cause) {
    // Network-level failure: DNS, refused connection, CORS preflight, offline.
    throw new ApiError(`Could not reach the API at ${apiBaseUrl()}`, 0, { cause });
  }

  if (!response.ok) {
    throw new ApiError(`API returned ${response.status} for ${path}`, response.status);
  }

  try {
    return (await response.json()) as unknown;
  } catch (cause) {
    throw new ApiError(`API returned a non-JSON body for ${path}`, response.status, { cause });
  }
}

/** `GET /healthz` — the only endpoint that exists in Phase 0. */
export async function fetchHealth(signal?: AbortSignal): Promise<Health> {
  const body = await request("/healthz", { signal: signal ?? null });

  if (!isHealth(body)) {
    throw new ApiError("API health response did not match the expected shape", 200);
  }

  return body;
}
