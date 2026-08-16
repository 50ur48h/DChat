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

/** One column as the catalog knows it, including what a sample looked like. */
export interface CatalogColumn {
  id: string;
  name: string;
  ordinal: number;
  data_type: string;
  nullable: boolean;
  is_pk: boolean;
  description: string | null;
  null_frac: number | null;
  distinct_est: number | null;
  min_val: string | null;
  max_val: string | null;
  top_values: { value?: unknown; count?: unknown }[] | null;
  semantic_role: string | null;
  /** none | suspected | confirmed — what the classifier thinks. */
  sensitivity: string;
  sample_rows: number | null;
  /** allow | mask | deny — what applies now. */
  policy: string;
  /** True when a person set it, rather than the safe default. */
  policy_decided: boolean;
}

export interface CatalogTable {
  schema_name: string;
  table_name: string;
  kind: string;
  description: string | null;
  row_estimate: number | null;
  card_text: string | null;
  columns: CatalogColumn[];
}

export interface CatalogSnapshot {
  id: string;
  version: number;
  status: string;
  captured_at: string;
  completed_at: string | null;
  object_count: number;
  error: string | null;
}

export interface Catalog {
  snapshot: CatalogSnapshot;
  tables: CatalogTable[];
  relationships: {
    constraint_name: string;
    from_schema: string;
    from_table: string;
    from_columns: string[];
    to_schema: string;
    to_table: string;
    to_columns: string[];
    kind: string;
    confidence: number;
  }[];
}

export interface RefreshResult {
  changed: boolean;
  detail: string;
  tables: number;
  columns: number;
  relationships: number;
  snapshot: CatalogSnapshot | null;
}

export interface ProfileResult {
  status: string;
  detail: string;
  tables_profiled: number;
  columns_profiled: number;
  sensitive_columns: number;
  tables_skipped: number;
  errors: string[];
}

export interface CardHit {
  data_source_id: string;
  schema_name: string;
  table_name: string;
  card_text: string;
  rank: number;
}

/**
 * A thread of questions about one database (architecture 10.1, D-022).
 *
 * `data_source_id` is null for a conversation that named none. That is legal,
 * not broken: the run resolves the organization's single source, or refuses and
 * names the choices. `data_source_name` is null for the same reason, and also
 * when the source has since been removed.
 */
export interface Conversation {
  id: string;
  title: string | null;
  created_at: string;
  message_count: number;
  last_run_id: string | null;
  data_source_id: string | null;
  data_source_name: string | null;
}

export interface ConversationMessage {
  id: string;
  /** user | assistant */
  role: string;
  content: string;
  run_id: string | null;
  created_at: string;
}

/** What `POST …/messages` answers with: the run, not the answer. */
export interface Accepted202 {
  run_id: string;
  message_id: string;
  status: string;
  /** False when an idempotency key matched an earlier send of this question. */
  created: boolean;
}

/**
 * A claim the run made, and the executions backing it.
 *
 * `support` holds `query_executions.id` values — the citation trail. Every id in
 * it was verified by the runner against what this run actually executed, so each
 * one resolves through `GET …/runs/{r}/executions/{q}`.
 */
export interface Finding {
  id: string;
  statement: string;
  support: string[];
  confidence: string;
  /**
   * True when the composed answer rests on this finding (WP9.2). A run reaches
   * several conclusions and an answer uses some; the rest are the
   * investigation's working and belong in the trace, not under the answer.
   */
  cited: boolean;
}

export interface Run {
  id: string;
  conversation_id: string;
  /** queued | running | validating | completed | interrupted | failed | budget_exhausted */
  status: string;
  question: string;
  answer: string | null;
  findings: Finding[];
  /**
   * What this answer does not establish, in plain words — a ceiling that stopped
   * the search, a reviewer's warning, a period the data does not cover.
   * Assembled by the platform rather than written by the model, so it cannot be
   * hedged away. Rendered beside the answer and never instead of it; empty is
   * the common case and a good one.
   */
  limitations: string[];
  started_at: string | null;
  finished_at: string | null;
  failure_reason: string | null;
}

export interface RunEvent {
  seq: number;
  type: string;
  payload: Record<string, unknown>;
  ts: string;
}

export interface RunEvents {
  run_id: string;
  events: RunEvent[];
  /** Pass back as `?after=` to fetch only what has happened since. */
  last_seq: number;
}

/**
 * The query behind a citation (architecture 10.2, B-034).
 *
 * `sample_rows` was masked before it was ever stored (WP5.2b), so there is no
 * unmasked copy for this screen to have asked for. A `refused` execution reached
 * no engine: it has no rows and no duration, and carries the violation code that
 * stopped it instead.
 */
export interface Execution {
  id: string;
  run_id: string;
  /** ok | error | refused */
  status: string;
  sql: string;
  tables: string[];
  columns: string[];
  row_count: number | null;
  duration_ms: number | null;
  violation_code: string | null;
  error: string | null;
  sensitive_accessed: boolean;
  masked_columns: string[];
  sample_rows: unknown[][];
  truncated: boolean;
  created_at: string;
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

function isCatalogColumn(value: unknown): value is CatalogColumn {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.name === "string" &&
    typeof value.data_type === "string" &&
    typeof value.sensitivity === "string" &&
    typeof value.policy === "string" &&
    typeof value.policy_decided === "boolean"
  );
}

function isCatalogTable(value: unknown): value is CatalogTable {
  return (
    isRecord(value) &&
    typeof value.schema_name === "string" &&
    typeof value.table_name === "string" &&
    typeof value.kind === "string" &&
    Array.isArray(value.columns) &&
    value.columns.every(isCatalogColumn)
  );
}

function isSnapshot(value: unknown): value is CatalogSnapshot {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.version === "number" &&
    typeof value.status === "string"
  );
}

export function isCatalog(value: unknown): value is Catalog {
  return (
    isRecord(value) &&
    isSnapshot(value.snapshot) &&
    Array.isArray(value.tables) &&
    value.tables.every(isCatalogTable) &&
    Array.isArray(value.relationships)
  );
}

export function isRefreshResult(value: unknown): value is RefreshResult {
  return (
    isRecord(value) &&
    typeof value.changed === "boolean" &&
    typeof value.detail === "string" &&
    (value.snapshot === null || isSnapshot(value.snapshot))
  );
}

export function isProfileResult(value: unknown): value is ProfileResult {
  return (
    isRecord(value) &&
    typeof value.status === "string" &&
    typeof value.detail === "string" &&
    typeof value.sensitive_columns === "number"
  );
}

export function isCardHit(value: unknown): value is CardHit {
  return (
    isRecord(value) &&
    typeof value.table_name === "string" &&
    typeof value.card_text === "string" &&
    typeof value.rank === "number"
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

export function isConversation(value: unknown): value is Conversation {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    isNullableString(value.title) &&
    typeof value.created_at === "string" &&
    isNullableString(value.data_source_id) &&
    isNullableString(value.data_source_name)
  );
}

export function isConversationMessage(value: unknown): value is ConversationMessage {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.role === "string" &&
    typeof value.content === "string" &&
    isNullableString(value.run_id) &&
    typeof value.created_at === "string"
  );
}

export function isAccepted202(value: unknown): value is Accepted202 {
  return (
    isRecord(value) &&
    typeof value.run_id === "string" &&
    typeof value.message_id === "string" &&
    typeof value.status === "string" &&
    typeof value.created === "boolean"
  );
}

function isFinding(value: unknown): value is Finding {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.statement === "string" &&
    typeof value.confidence === "string" &&
    Array.isArray(value.support) &&
    value.support.every((id) => typeof id === "string") &&
    (value.cited === undefined || typeof value.cited === "boolean")
  );
}

export function isRun(value: unknown): value is Run {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.conversation_id === "string" &&
    typeof value.status === "string" &&
    typeof value.question === "string" &&
    isNullableString(value.answer) &&
    isNullableString(value.failure_reason) &&
    Array.isArray(value.findings) &&
    value.findings.every(isFinding) &&
    (value.limitations === undefined ||
      (Array.isArray(value.limitations) &&
        value.limitations.every((note) => typeof note === "string")))
  );
}

function isRunEvent(value: unknown): value is RunEvent {
  return (
    isRecord(value) &&
    typeof value.seq === "number" &&
    typeof value.type === "string" &&
    typeof value.ts === "string" &&
    isRecord(value.payload)
  );
}

export function isRunEvents(value: unknown): value is RunEvents {
  return (
    isRecord(value) &&
    typeof value.run_id === "string" &&
    typeof value.last_seq === "number" &&
    Array.isArray(value.events) &&
    value.events.every(isRunEvent)
  );
}

function isNullableNumber(value: unknown): value is number | null {
  return value === null || typeof value === "number";
}

export function isExecution(value: unknown): value is Execution {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.run_id === "string" &&
    typeof value.status === "string" &&
    typeof value.sql === "string" &&
    isNullableNumber(value.row_count) &&
    isNullableNumber(value.duration_ms) &&
    isNullableString(value.violation_code) &&
    isNullableString(value.error) &&
    typeof value.sensitive_accessed === "boolean" &&
    typeof value.truncated === "boolean" &&
    Array.isArray(value.columns) &&
    Array.isArray(value.masked_columns) &&
    Array.isArray(value.sample_rows) &&
    value.sample_rows.every((row) => Array.isArray(row))
  );
}
