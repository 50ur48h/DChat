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
  isAccepted202,
  isCardHit,
  isCatalog,
  isActiveDataSource,
  isConversation,
  isConversationMessage,
  isDataSource,
  isDefinitionProposal,
  isDefinitionVersion,
  isExecution,
  isHealth,
  isArmedRecoveryGrant,
  isInvitation,
  isRecoveryGrant,
  isKnowledgeDocument,
  isMe,
  isMember,
  isProfileResult,
  isRefreshResult,
  isRun,
  isRunEvents,
  isSemanticDefinition,
  isTestResult,
  type Accepted,
  type Accepted202,
  type CardHit,
  type Catalog,
  type ActiveDataSource,
  type Conversation,
  type ConversationMessage,
  type DataSource,
  type DefinitionProposal,
  type DefinitionVersion,
  type Execution,
  type Health,
  type ArmedRecoveryGrant,
  type Invitation,
  type RecoveryGrant,
  type KnowledgeDocument,
  type Me,
  type Member,
  type NewDataSource,
  type ProfileResult,
  type RefreshResult,
  type RequiredFilter,
  type Run,
  type RunEvent,
  type RunEvents,
  type SemanticDefinition,
  type TestResult,
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

/**
 * A multipart body is passed through untouched, and its Content-Type is left
 * unset on purpose: the browser has to write the header itself because only it
 * knows the boundary string it generated. Setting `multipart/form-data` by hand
 * produces a request the server cannot parse, and the error it gives back is
 * about a missing part rather than about a missing boundary.
 */
function isMultipart(body: unknown): body is FormData {
  return typeof FormData !== "undefined" && body instanceof FormData;
}

async function request(path: string, options: RequestOptions = {}): Promise<unknown> {
  const { method = "GET", body, token, signal = null } = options;

  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined && !isMultipart(body)) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}${path}`, {
      method,
      headers,
      signal,
      body: body === undefined ? null : isMultipart(body) ? body : JSON.stringify(body),
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
  /** B-017. The token comes back once, here, and is never retrievable again. */
  armRecoveryGrant(orgId: string, label: string): Promise<ArmedRecoveryGrant>;
  recoveryGrants(orgId: string): Promise<RecoveryGrant[]>;
  revokeRecoveryGrant(orgId: string, grantId: string): Promise<RecoveryGrant>;
  acceptInvitation(token: string): Promise<Accepted>;
  changeRole(orgId: string, userId: string, role: string): Promise<void>;
  removeMember(orgId: string, userId: string): Promise<void>;
  dataSources(orgId: string): Promise<DataSource[]>;
  /**
   * The database this organization asks questions of (D-045).
   *
   * Readable by any member — the chat screen has to know whether asking is
   * possible before it offers a composer. Setting it is Admin-only and the API
   * refuses anyone else, whatever the browser believes.
   */
  activeDataSource(orgId: string): Promise<ActiveDataSource>;
  setActiveDataSource(orgId: string, dataSourceId: string | null): Promise<ActiveDataSource>;
  registerDataSource(orgId: string, source: NewDataSource): Promise<DataSource>;
  rotateCredentials(
    orgId: string,
    dataSourceId: string,
    credentials: { username?: string | undefined; password: string },
  ): Promise<DataSource>;
  testDataSource(orgId: string, dataSourceId: string): Promise<TestResult>;
  removeDataSource(orgId: string, dataSourceId: string): Promise<void>;
  refreshCatalog(orgId: string, dataSourceId: string): Promise<RefreshResult>;
  profileCatalog(orgId: string, dataSourceId: string): Promise<ProfileResult>;
  catalog(orgId: string, dataSourceId: string): Promise<Catalog>;
  searchCatalog(orgId: string, query: string, dataSourceId?: string): Promise<CardHit[]>;
  setColumnPolicy(
    orgId: string,
    dataSourceId: string,
    columnId: string,
    decision: { policy: string; reason?: string | undefined },
  ): Promise<void>;
  documents(orgId: string): Promise<KnowledgeDocument[]>;
  /**
   * Uploads a file and indexes it. Takes a `File` rather than bytes so the
   * browser writes its own multipart boundary — see `isMultipart` in this file.
   */
  uploadDocument(orgId: string, file: File, title?: string): Promise<KnowledgeDocument>;
  reindexDocument(orgId: string, documentId: string): Promise<KnowledgeDocument>;
  removeDocument(orgId: string, documentId: string): Promise<void>;
  /**
   * The semantic layer (WP10.2d). **Every one of these is Admin** — an accepted
   * definition constrains generated SQL, so it is a privileged object rather
   * than content. A screen that calls them for a Reader earns a 403 the API
   * records; `useOrgRole` is what stops it being offered (B-008).
   */
  definitions(
    orgId: string,
    dataSourceId: string,
    status?: "active" | "retired" | undefined,
  ): Promise<SemanticDefinition[]>;
  definitionProposals(orgId: string, dataSourceId: string): Promise<DefinitionProposal[]>;
  createDefinition(
    orgId: string,
    dataSourceId: string,
    definition: {
      name: string;
      description: string;
      expression?: string | undefined;
      caveat?: string | undefined;
      synonyms?: string[] | undefined;
      required_filters?: RequiredFilter[] | undefined;
    },
  ): Promise<SemanticDefinition>;
  importDefinitions(
    orgId: string,
    dataSourceId: string,
    mapping: {
      table: string;
      schema?: string | undefined;
      name_column: string;
      description_column: string;
      expression_column?: string | undefined;
      synonyms_column?: string | undefined;
      caveat_column?: string | undefined;
    },
  ): Promise<DefinitionProposal[]>;
  /**
   * Bless a proposal, and say what it requires.
   *
   * The filters are what turn prose into a constraint (D-033), and an empty
   * list is a real answer rather than a missing one: an Admin may accept a
   * definition as prose, which puts it in front of the model without binding it.
   */
  acceptProposal(
    orgId: string,
    dataSourceId: string,
    definitionId: string,
    requiredFilters: RequiredFilter[],
    synonyms?: string[] | undefined,
  ): Promise<SemanticDefinition>;
  rejectProposal(orgId: string, dataSourceId: string, definitionId: string): Promise<void>;
  /**
   * Correct a definition that is already in force (**B-088**).
   *
   * **Only the fields that changed are sent**, and that is the contract rather
   * than an optimisation: the API reads an absent field as *leave it alone* and
   * a present one as *replace it*, so resending a description nobody edited is
   * how a description quietly loses a sentence. `expression: null` clears the
   * formula, which is the one place where absent and null differ.
   */
  updateDefinition(
    orgId: string,
    dataSourceId: string,
    definitionId: string,
    changes: {
      description?: string | undefined;
      expression?: string | null | undefined;
      synonyms?: string[] | undefined;
      required_filters?: RequiredFilter[] | undefined;
    },
  ): Promise<SemanticDefinition>;
  /**
   * Take a definition out of force, keeping what it said.
   *
   * Retired rather than deleted, like everything else in this layer: an answer
   * checked against it last month is still explainable this month.
   */
  retireDefinition(orgId: string, dataSourceId: string, definitionId: string): Promise<void>;
  /**
   * Bring a retired definition back into force (**B-094**).
   *
   * `requiredFilters` is for the case that would otherwise be a dead end: a
   * retired definition cannot be edited, so one whose catalog has moved on is
   * repaired in the same act that brings it back. Omit it to keep what the
   * definition was retired holding, which is the ordinary case.
   */
  reinstateDefinition(
    orgId: string,
    dataSourceId: string,
    definitionId: string,
    requiredFilters?: RequiredFilter[] | undefined,
  ): Promise<SemanticDefinition>;
  /** Everything this definition has said, oldest first (B-088, D-036). */
  definitionVersions(
    orgId: string,
    dataSourceId: string,
    definitionId: string,
  ): Promise<DefinitionVersion[]>;
  /** `archived` picks one list or the other, never both (D-039). */
  conversations(orgId: string, options?: { archived?: boolean }): Promise<Conversation[]>;
  renameConversation(orgId: string, conversationId: string, title: string): Promise<Conversation>;
  /** Put a thread away, or bring it back. Never destroys its runs or its trace. */
  archiveConversation(
    orgId: string,
    conversationId: string,
    archived: boolean,
  ): Promise<Conversation>;
  createConversation(
    orgId: string,
    options?: { title?: string | undefined; dataSourceId?: string | undefined },
  ): Promise<Conversation>;
  conversation(orgId: string, conversationId: string): Promise<Conversation>;
  messages(orgId: string, conversationId: string): Promise<ConversationMessage[]>;
  ask(
    orgId: string,
    conversationId: string,
    content: string,
    idempotencyKey: string,
  ): Promise<Accepted202>;
  run(orgId: string, runId: string, signal?: AbortSignal): Promise<Run>;
  /** Every run in the thread, oldest first — what a card per answer needs (B-106). */
  conversationRuns(orgId: string, conversationId: string): Promise<Run[]>;
  runEvents(orgId: string, runId: string, after?: number): Promise<RunEvents>;
  execution(orgId: string, runId: string, executionId: string): Promise<Execution>;
  streamRunEvents(
    orgId: string,
    runId: string,
    options: {
      after?: number | undefined;
      onEvent: (event: RunEvent) => void;
      signal: AbortSignal;
    },
  ): Promise<void>;
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
    async armRecoveryGrant(orgId, label) {
      return narrow(
        await call(`/v1/orgs/${orgId}/recovery-grants`, { method: "POST", body: { label } }),
        isArmedRecoveryGrant,
        "recovery grant",
      );
    },
    async recoveryGrants(orgId) {
      const payload = await call(`/v1/orgs/${orgId}/recovery-grants`);
      if (!Array.isArray(payload) || !payload.every(isRecoveryGrant)) {
        throw new ApiError(
          "The API's recovery-grants response did not match the expected shape",
          200,
        );
      }
      return payload;
    },
    async revokeRecoveryGrant(orgId, grantId) {
      return narrow(
        await call(`/v1/orgs/${orgId}/recovery-grants/${grantId}/revoke`, {
          method: "POST",
          body: {},
        }),
        isRecoveryGrant,
        "recovery grant",
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
    async dataSources(orgId) {
      const payload = await call(`/v1/orgs/${orgId}/data-sources`);
      if (!Array.isArray(payload) || !payload.every(isDataSource)) {
        throw new ApiError("The API's data sources response did not match the expected shape", 200);
      }
      return payload;
    },
    async activeDataSource(orgId) {
      return narrow(
        await call(`/v1/orgs/${orgId}/active-data-source`),
        isActiveDataSource,
        "active data source",
      );
    },
    // A PUT, mirroring the API: null is a real value here, meaning "no choice",
    // and a PATCH of a partial body could not tell that from a field left out.
    async setActiveDataSource(orgId, dataSourceId) {
      return narrow(
        await call(`/v1/orgs/${orgId}/active-data-source`, {
          method: "PUT",
          body: { data_source_id: dataSourceId },
        }),
        isActiveDataSource,
        "active data source",
      );
    },
    // The credential travels in a POST body and nowhere else — never a query
    // string, which would put it in browser history, in a referrer header, and
    // in every access log between here and the API.
    async registerDataSource(orgId, source) {
      return narrow(
        await call(`/v1/orgs/${orgId}/data-sources`, { method: "POST", body: source }),
        isDataSource,
        "data source",
      );
    },
    async rotateCredentials(orgId, dataSourceId, credentials) {
      return narrow(
        await call(`/v1/orgs/${orgId}/data-sources/${dataSourceId}`, {
          method: "PATCH",
          body: credentials,
        }),
        isDataSource,
        "data source",
      );
    },
    async testDataSource(orgId, dataSourceId) {
      return narrow(
        await call(`/v1/orgs/${orgId}/data-sources/${dataSourceId}/test`, { method: "POST" }),
        isTestResult,
        "connection test",
      );
    },
    async removeDataSource(orgId, dataSourceId) {
      await call(`/v1/orgs/${orgId}/data-sources/${dataSourceId}`, { method: "DELETE" });
    },
    async documents(orgId) {
      const payload = await call(`/v1/orgs/${orgId}/documents`);
      if (!Array.isArray(payload) || !payload.every(isKnowledgeDocument)) {
        throw new ApiError("The API's documents response did not match the expected shape", 200);
      }
      return payload;
    },
    async uploadDocument(orgId, file, title) {
      const form = new FormData();
      form.append("file", file);
      // Only when given: an empty `title` part would beat the API's own default
      // of the filename, and land a document called "" in the list.
      if (title !== undefined && title.trim().length > 0) form.append("title", title.trim());
      return narrow(
        await call(`/v1/orgs/${orgId}/documents`, { method: "POST", body: form }),
        isKnowledgeDocument,
        "document",
      );
    },
    async reindexDocument(orgId, documentId) {
      return narrow(
        await call(`/v1/orgs/${orgId}/documents/${documentId}/reindex`, { method: "POST" }),
        isKnowledgeDocument,
        "document",
      );
    },
    async removeDocument(orgId, documentId) {
      await call(`/v1/orgs/${orgId}/documents/${documentId}`, { method: "DELETE" });
    },
    async definitions(orgId, dataSourceId, status) {
      const payload = await call(
        `/v1/orgs/${orgId}/data-sources/${dataSourceId}/definitions` +
          (status ? `?status=${status}` : ""),
      );
      if (!Array.isArray(payload) || !payload.every(isSemanticDefinition)) {
        throw new ApiError("The API's definitions response did not match the expected shape", 200);
      }
      return payload;
    },
    async definitionProposals(orgId, dataSourceId) {
      const payload = await call(
        `/v1/orgs/${orgId}/data-sources/${dataSourceId}/definitions/proposals`,
      );
      if (!Array.isArray(payload) || !payload.every(isDefinitionProposal)) {
        throw new ApiError("The API's proposals response did not match the expected shape", 200);
      }
      return payload;
    },
    async createDefinition(orgId, dataSourceId, definition) {
      return narrow(
        await call(`/v1/orgs/${orgId}/data-sources/${dataSourceId}/definitions`, {
          method: "POST",
          body: definition,
        }),
        isSemanticDefinition,
        "definition",
      );
    },
    async importDefinitions(orgId, dataSourceId, mapping) {
      const payload = await call(
        `/v1/orgs/${orgId}/data-sources/${dataSourceId}/definitions/import`,
        { method: "POST", body: mapping },
      );
      if (!Array.isArray(payload) || !payload.every(isDefinitionProposal)) {
        throw new ApiError("The API's import response did not match the expected shape", 201);
      }
      return payload;
    },
    async acceptProposal(orgId, dataSourceId, definitionId, requiredFilters, synonyms) {
      return narrow(
        await call(
          `/v1/orgs/${orgId}/data-sources/${dataSourceId}/definitions/${definitionId}/accept`,
          {
            method: "POST",
            body: {
              required_filters: requiredFilters,
              // Omitted rather than sent empty when the Admin typed nothing:
              // the API reads an absent field as "keep what the import found"
              // and an empty list as "replace it with none", and stripping a
              // metric's own label would make it unreachable (B-085).
              ...(synonyms && synonyms.length > 0 ? { synonyms } : {}),
            },
          },
        ),
        isSemanticDefinition,
        "definition",
      );
    },
    async updateDefinition(orgId, dataSourceId, definitionId, changes) {
      return narrow(
        await call(`/v1/orgs/${orgId}/data-sources/${dataSourceId}/definitions/${definitionId}`, {
          method: "PATCH",
          // `changes` is passed through as the caller built it. The screen is
          // what decides which fields moved, because it is the only place that
          // knows what the Admin actually touched.
          body: changes,
        }),
        isSemanticDefinition,
        "definition",
      );
    },
    async retireDefinition(orgId, dataSourceId, definitionId) {
      await call(`/v1/orgs/${orgId}/data-sources/${dataSourceId}/definitions/${definitionId}`, {
        method: "DELETE",
      });
    },
    async reinstateDefinition(orgId, dataSourceId, definitionId, requiredFilters) {
      return narrow(
        await call(
          `/v1/orgs/${orgId}/data-sources/${dataSourceId}/definitions/${definitionId}/reinstate`,
          {
            method: "POST",
            // Omitted rather than sent empty when there is nothing to correct:
            // the API reads an absent list as "as it was retired" and an empty
            // one as "replace them with none", which is a different request.
            body: requiredFilters ? { required_filters: requiredFilters } : {},
          },
        ),
        isSemanticDefinition,
        "definition",
      );
    },
    async definitionVersions(orgId, dataSourceId, definitionId) {
      const payload = await call(
        `/v1/orgs/${orgId}/data-sources/${dataSourceId}/definitions/${definitionId}/versions`,
      );
      if (!Array.isArray(payload) || !payload.every(isDefinitionVersion)) {
        throw new ApiError("The API's versions response did not match the expected shape", 200);
      }
      return payload;
    },
    async rejectProposal(orgId, dataSourceId, definitionId) {
      await call(
        `/v1/orgs/${orgId}/data-sources/${dataSourceId}/definitions/${definitionId}/reject`,
        { method: "POST" },
      );
    },
    async refreshCatalog(orgId, dataSourceId) {
      return narrow(
        await call(`/v1/orgs/${orgId}/data-sources/${dataSourceId}/refresh`, { method: "POST" }),
        isRefreshResult,
        "catalog refresh",
      );
    },
    async profileCatalog(orgId, dataSourceId) {
      return narrow(
        await call(`/v1/orgs/${orgId}/data-sources/${dataSourceId}/profile`, { method: "POST" }),
        isProfileResult,
        "catalog profile",
      );
    },
    async catalog(orgId, dataSourceId) {
      return narrow(
        await call(`/v1/orgs/${orgId}/data-sources/${dataSourceId}/catalog`),
        isCatalog,
        "catalog",
      );
    },
    async searchCatalog(orgId, query, dataSourceId) {
      const parameters = new URLSearchParams({ q: query });
      if (dataSourceId) parameters.set("data_source_id", dataSourceId);
      const payload = await call(`/v1/orgs/${orgId}/catalog/search?${parameters.toString()}`);
      if (!Array.isArray(payload) || !payload.every(isCardHit)) {
        throw new ApiError("The API's search response did not match the expected shape", 200);
      }
      return payload;
    },
    async setColumnPolicy(orgId, dataSourceId, columnId, decision) {
      await call(`/v1/orgs/${orgId}/data-sources/${dataSourceId}/columns/${columnId}/policy`, {
        method: "PATCH",
        body: decision,
      });
    },
    async conversations(orgId, options = {}) {
      const query = options.archived ? "?archived=true" : "";
      const payload = await call(`/v1/orgs/${orgId}/conversations${query}`);
      if (!Array.isArray(payload) || !payload.every(isConversation)) {
        throw new ApiError("The API's conversations response did not match the expected shape", 200);
      }
      return payload;
    },
    async renameConversation(orgId, conversationId, title) {
      return narrow(
        await call(`/v1/orgs/${orgId}/conversations/${conversationId}`, {
          method: "PATCH",
          body: { title },
        }),
        isConversation,
        "conversation",
      );
    },
    // A POST to `…/archive` rather than a DELETE, mirroring the API: nothing is
    // removed, and the reverse direction exists, which a DELETE could not offer.
    async archiveConversation(orgId, conversationId, archived) {
      return narrow(
        await call(`/v1/orgs/${orgId}/conversations/${conversationId}/archive`, {
          method: "POST",
          body: { archived },
        }),
        isConversation,
        "conversation",
      );
    },
    // `data_source_id` is what makes a thread answerable in an organization with
    // more than one database (D-022). Omitting it is legal and means "the single
    // source, if there is one" — so it is only sent when the caller chose.
    async createConversation(orgId, options = {}) {
      const body: Record<string, string> = {};
      if (options.title) body.title = options.title;
      if (options.dataSourceId) body.data_source_id = options.dataSourceId;
      return narrow(
        await call(`/v1/orgs/${orgId}/conversations`, { method: "POST", body }),
        isConversation,
        "conversation",
      );
    },
    async conversation(orgId, conversationId) {
      return narrow(
        await call(`/v1/orgs/${orgId}/conversations/${conversationId}`),
        isConversation,
        "conversation",
      );
    },
    async messages(orgId, conversationId) {
      const payload = await call(`/v1/orgs/${orgId}/conversations/${conversationId}/messages`);
      if (!Array.isArray(payload) || !payload.every(isConversationMessage)) {
        throw new ApiError("The API's messages response did not match the expected shape", 200);
      }
      return payload;
    },
    // The idempotency key is required by the API and generated by the caller, so
    // a double-tapped send returns the run that already exists instead of paying
    // for a second one.
    async ask(orgId, conversationId, content, idempotencyKey) {
      return narrow(
        await call(`/v1/orgs/${orgId}/conversations/${conversationId}/messages`, {
          method: "POST",
          body: { content, idempotency_key: idempotencyKey },
        }),
        isAccepted202,
        "accepted question",
      );
    },
    async run(orgId, runId, signal) {
      return narrow(
        await call(`/v1/orgs/${orgId}/runs/${runId}`, { signal: signal ?? null }),
        isRun,
        "run",
      );
    },
    // One request for the whole thread rather than one per assistant message
    // (**B-106**): the number of round trips to render a conversation should not
    // grow with how much somebody has used it.
    async conversationRuns(orgId, conversationId) {
      const payload = await call(`/v1/orgs/${orgId}/conversations/${conversationId}/runs`);
      if (!Array.isArray(payload) || !payload.every(isRun)) {
        throw new ApiError("The API's conversation runs response did not match the expected shape", 200);
      }
      return payload;
    },
    async runEvents(orgId, runId, after = 0) {
      return narrow(
        await call(`/v1/orgs/${orgId}/runs/${runId}/events?after=${after}`),
        isRunEvents,
        "run events",
      );
    },
    async execution(orgId, runId, executionId) {
      return narrow(
        await call(`/v1/orgs/${orgId}/runs/${runId}/executions/${executionId}`),
        isExecution,
        "execution",
      );
    },
    /**
     * Follow a run's trace as it happens (architecture 10.3).
     *
     * **`fetch` rather than `EventSource`, and that is a security decision.**
     * `EventSource` cannot set headers, so the only way to authenticate it is a
     * token in the query string — which this codebase has already refused once,
     * for the data-source password, and for the same reasons: query strings land
     * in browser history, in referrer headers and in every access log between
     * here and the API. Streaming the response body costs a manual frame parser
     * and a reconnect loop, and keeps **one** auth path rather than two.
     *
     * Reconnection is ours for the same reason. `Last-Event-ID` is sent on the
     * way back, so the server resumes from the durable rows and nothing is
     * missed — the property the whole design rests on.
     */
    async streamRunEvents(orgId, runId, { after = 0, onEvent, signal }) {
      let lastSeq = after;
      while (!signal.aborted) {
        try {
          const response = await fetch(
            `${apiBaseUrl()}/v1/orgs/${orgId}/runs/${runId}/events?after=${lastSeq}`,
            {
              signal,
              headers: {
                Accept: "text/event-stream",
                Authorization: `Bearer ${(await getToken()) ?? ""}`,
                ...(lastSeq > 0 ? { "Last-Event-ID": String(lastSeq) } : {}),
              },
            },
          );
          if (!response.ok || !response.body) {
            throw new ApiError(`The trace stream failed with ${response.status}`, response.status);
          }
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffered = "";
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            buffered += decoder.decode(value, { stream: true });
            // Frames are separated by a blank line; anything after the last one
            // is a partial frame and waits for the next chunk.
            const frames = buffered.split("\n\n");
            buffered = frames.pop() ?? "";
            for (const frame of frames) {
              const data = frame
                .split("\n")
                .filter((row) => row.startsWith("data: "))
                .map((row) => row.slice("data: ".length))
                .join("");
              if (!data) continue;
              try {
                const event = JSON.parse(data) as RunEvent;
                if (typeof event.seq === "number" && event.seq > lastSeq) lastSeq = event.seq;
                onEvent(event);
              } catch {
                // One unparseable frame is one missing step, not a reason to
                // tear down a stream that is otherwise delivering.
              }
            }
          }
          // The server closes the stream when the run ends, so a clean end of
          // body means there is nothing more coming.
          return;
        } catch (cause) {
          if (signal.aborted) return;
          if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) throw cause;
          // A dropped connection: wait, then resume from `lastSeq`. The rows are
          // durable, so nothing is lost by having been away.
          await new Promise((resolve) => setTimeout(resolve, 1000));
        }
      }
    },
  };
}
