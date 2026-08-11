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
  email: string;
  name: string | null;
  memberships: Membership[];
}

export interface Member {
  user_id: string;
  email: string;
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
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
    typeof value.email === "string" &&
    Array.isArray(value.memberships) &&
    value.memberships.every(isMembership)
  );
}

export function isMember(value: unknown): value is Member {
  return (
    isRecord(value) &&
    typeof value.user_id === "string" &&
    typeof value.email === "string" &&
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

export function isInvitation(value: unknown): value is Invitation {
  return (
    isRecord(value) &&
    typeof value.invitation_id === "string" &&
    typeof value.token === "string" &&
    typeof value.role === "string" &&
    typeof value.expires_at === "string"
  );
}
