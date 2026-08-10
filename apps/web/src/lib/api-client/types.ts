/**
 * Response shapes of the data-agent API.
 *
 * Hand-written for now. From Phase 7 these are generated from the FastAPI
 * OpenAPI schema (`openapi-typescript`) so contract drift breaks CI instead of
 * production — see BACKLOG B-003 and architecture Part 3.1.
 */

export interface Health {
  status: "ok";
  version: string;
  git_sha: string;
}

/** Runtime narrowing, because `await res.json()` is `any` and a cast would lie. */
export function isHealth(value: unknown): value is Health {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    candidate.status === "ok" &&
    typeof candidate.version === "string" &&
    typeof candidate.git_sha === "string"
  );
}
