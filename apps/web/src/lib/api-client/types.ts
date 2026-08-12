/**
 * Response shapes of the data-agent API.
 *
 * Hand-written, and narrowed at runtime rather than cast — a lying type is worse
 * than no type. Generated from the OpenAPI schema in Phase 7 (backlog B-003).
 */

export interface Health {
  status: "ok";
  version: string;
  git_sha: string;
}

export interface Membership {
  org_id: string;
  org_name: string;
  role: string;
}

export interface Me {
  subject: string;
  user_id: string;
  /** Null when the identity provider sent no email claim (backlog B-009). */
  email: string | null;
  name: string | null;
  memberships: Membership[];
}

export interface Member {
  user_id: string;
  email: string | null;
  name: string | null;
  role: string;
}

export interface Invitation {
  invitation_id: string;
  email: string;
  role: string;
  expires_at: string;
  token: string;
}

/**
 * A registered customer database.
 *
 * What is missing is the point: there is no `password` and no `username`. The
 * API has no such field to send (architecture Part 7.3), so this type could not
 * carry one even if a screen asked. `username_last4` identifies the account and
 * `secret_ref` says where its credentials are kept, not what they are.
 */
export interface DataSource {
  id: string;
  name: string;
  engine: string;
  host: string;
  port: number;
  database: string;
  host_display: string;
  /** registered | verified | error */
  status: string;
  secret_ref: string;
  username_last4: string;
  /** disable | prefer | require | verify-ca | verify-full (B-013). */
  tls_mode: string;
  readonly_verified: boolean;
  last_verified_at: string | null;
  created_by: string | null;
  created_at: string;
}

/** The outcome of `POST …/test`. A bad result is still a successful call. */
export interface TestResult {
  reachable: boolean;
  readonly_verified: boolean;
  status: string;
  /** Sanitized by the API: no connection string, no address, no credential. */
  detail: string;
  checked_at: string;
  server_version: string | null;
  tls_mode: string | null;
  /** What the server said, where it would say. Null when never established. */
  tls_encrypted: boolean | null;
  tls_detail: string | null;
  evidence: string[];
}

/** What registering a data source needs. The password goes nowhere else. */
export interface NewDataSource {
  name: string;
  engine: string;
  host: string;
  port: number;
  database: string;
  username: string;
  password: string;
  tls_mode?: string | undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/** A field the API may legitimately send as null — an absent identity claim. */
function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

export function isHealth(value: unknown): value is Health {
  return (
    isRecord(value) &&
    value.status === "ok" &&
    typeof value.version === "string" &&
    typeof value.git_sha === "string"
  );
}

function isMembership(value: unknown): value is Membership {
  return (
    isRecord(value) &&
    typeof value.org_id === "string" &&
    typeof value.org_name === "string" &&
    typeof value.role === "string"
  );
}

export function isMe(value: unknown): value is Me {
  return (
    isRecord(value) &&
    typeof value.subject === "string" &&
    typeof value.user_id === "string" &&
    isNullableString(value.email) &&
    Array.isArray(value.memberships) &&
    value.memberships.every(isMembership)
  );
}

export function isMember(value: unknown): value is Member {
  return (
    isRecord(value) &&
    typeof value.user_id === "string" &&
    isNullableString(value.email) &&
    typeof value.role === "string"
  );
}

export interface Accepted {
  org_id: string;
  org_name: string;
  role: string;
}

export function isAccepted(value: unknown): value is Accepted {
  return (
    isRecord(value) &&
    typeof value.org_id === "string" &&
    typeof value.org_name === "string" &&
    typeof value.role === "string"
  );
}

export function isDataSource(value: unknown): value is DataSource {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.name === "string" &&
    typeof value.engine === "string" &&
    typeof value.host_display === "string" &&
    typeof value.status === "string" &&
    typeof value.username_last4 === "string" &&
    typeof value.tls_mode === "string" &&
    typeof value.readonly_verified === "boolean" &&
    isNullableString(value.last_verified_at)
  );
}

function isNullableBoolean(value: unknown): value is boolean | null {
  return value === null || typeof value === "boolean";
}

export function isTestResult(value: unknown): value is TestResult {
  return (
    isRecord(value) &&
    typeof value.reachable === "boolean" &&
    typeof value.readonly_verified === "boolean" &&
    typeof value.status === "string" &&
    typeof value.detail === "string" &&
    isNullableString(value.server_version) &&
    isNullableString(value.tls_mode) &&
    isNullableBoolean(value.tls_encrypted) &&
    isNullableString(value.tls_detail) &&
    Array.isArray(value.evidence) &&
    value.evidence.every((note) => typeof note === "string")
  );
}

export function isInvitation(value: unknown): value is Invitation {
  return (
    isRecord(value) &&
    typeof value.invitation_id === "string" &&
    typeof value.token === "string" &&
    typeof value.role === "string" &&
    typeof value.expires_at === "string"
  );
}
