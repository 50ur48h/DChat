"use client";

/**
 * Registering the databases an organization wants analysed (plan WP3.4).
 *
 * This is the one screen in the product where a customer's database password is
 * typed, so three rules shape it rather than decorate it:
 *
 *   1. The password is a `type="password"` field, it travels in a POST body,
 *      and it is cleared the moment the submit resolves — including when the
 *      submit failed. A field that keeps its value after an error is friendlier
 *      and leaves a credential sitting in a live component for as long as the
 *      tab is open, which is the trade this screen declines.
 *   2. Nothing reads it back. The API has no field that could return one, so
 *      what a registered source shows is `username_last4` and `host_display`.
 *   3. A Reader is not shown controls they cannot use (B-008). The API refuses
 *      and audits them anyway; offering the button teaches people the product
 *      is broken rather than that they lack permission.
 *
 * The result of a test is rendered as three separate facts, because merging
 * them would be a lie: *reachable* is not *read-only*, and *encrypted* is not
 * *verified* — `require` encrypts without checking any certificate (B-013).
 */

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge, type Tone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { Row, Stack } from "@/components/ui/page";
import {
  createApi,
  type DataSource,
  type ProfileResult,
  type RefreshResult,
  type TestResult,
} from "@/lib/api-client";
import { useSession } from "@/lib/auth/session";

import styles from "./data-sources.module.css";

const ENGINES = ["pg", "mssql"] as const;

/** Mirrors the API's `TlsMode`. Ordered weakest first, as the ladder reads. */
const TLS_MODES = ["prefer", "require", "verify-ca", "verify-full"] as const;

const ENGINE_NAMES: Record<string, string> = {
  pg: "PostgreSQL",
  mssql: "SQL Server",
};

const STATUS_TONES: Record<string, Tone> = {
  verified: "mint",
  error: "rose",
  registered: "neutral",
};

const DEFAULT_PORTS: Record<string, string> = { pg: "5432", mssql: "1433" };

interface Draft {
  name: string;
  engine: string;
  host: string;
  port: string;
  database: string;
  username: string;
  password: string;
  tlsMode: string;
}

const EMPTY: Draft = {
  name: "",
  engine: "pg",
  host: "",
  port: DEFAULT_PORTS.pg ?? "5432",
  database: "",
  username: "",
  password: "",
  tlsMode: "",
};

function statusWord(source: DataSource): string {
  if (source.status === "verified") return "read-only verified";
  if (source.status === "error") return "not usable";
  return "not checked yet";
}

function when(timestamp: string | null): string {
  if (!timestamp) return "never";
  const parsed = new Date(timestamp);
  return Number.isNaN(parsed.getTime()) ? timestamp : parsed.toLocaleString();
}

/** What a test found, said plainly and without rounding anything up. */
function Result({ result }: { result: TestResult }) {
  return (
    <div className={result.readonly_verified ? styles.resultOk : styles.resultBad}>
      <p className={styles.resultDetail}>{result.detail}</p>
      <dl className={styles.facts}>
        <dt>Reachable</dt>
        <dd>{result.reachable ? "yes" : "no"}</dd>
        <dt>Read-only</dt>
        <dd>{result.readonly_verified ? "proven" : "not proven"}</dd>
        {result.tls_detail && (
          <>
            <dt>Encryption</dt>
            <dd>{result.tls_detail}</dd>
          </>
        )}
        {result.server_version && (
          <>
            <dt>Server</dt>
            <dd>{result.server_version}</dd>
          </>
        )}
        <dt>Checked</dt>
        <dd>{when(result.checked_at)}</dd>
      </dl>
      {result.evidence.length > 0 && (
        <ul className={styles.evidence}>
          {result.evidence.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function DataSources({ orgId, role }: { orgId: string; role: string | null }) {
  const session = useSession();
  const api = useMemo(() => createApi(session.getToken), [session.getToken]);

  // Unknown role means no admin controls. Failing closed costs an Admin one
  // page load; failing open shows buttons to people the API will refuse.
  const isAdmin = role === "admin";
  // Reading a database into the catalog is Contributor work: it changes nothing
  // in the customer's database and nothing about who may see what.
  const canRefresh = isAdmin || role === "contributor";

  const [sources, setSources] = useState<DataSource[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, TestResult>>({});
  const [catalogNotes, setCatalogNotes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [rotating, setRotating] = useState<string | null>(null);
  const [newPassword, setNewPassword] = useState("");

  const load = useCallback(async () => {
    setSources(await api.dataSources(orgId));
  }, [api, orgId]);

  useEffect(() => {
    // Guarded so a slow response cannot write into an unmounted screen.
    let active = true;
    void (async () => {
      try {
        const next = await api.dataSources(orgId);
        if (active) setSources(next);
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "Could not load");
      }
    })();
    return () => {
      active = false;
    };
  }, [api, orgId]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      await load();
    } catch (cause) {
      // The API's own words. "TLS mode 'prefer' allows an unencrypted
      // connection…" names the fix; "422" does not.
      setError(cause instanceof Error ? cause.message : "That did not work");
    } finally {
      setBusy(false);
    }
  };

  const register = (event: React.FormEvent) => {
    event.preventDefault();
    const submitted = draft;
    void run(async () => {
      try {
        await api.registerDataSource(orgId, {
          name: submitted.name.trim(),
          engine: submitted.engine,
          host: submitted.host.trim(),
          port: Number(submitted.port),
          database: submitted.database.trim(),
          username: submitted.username.trim(),
          password: submitted.password,
          ...(submitted.tlsMode ? { tls_mode: submitted.tlsMode } : {}),
        });
        setDraft(EMPTY);
      } finally {
        // Whatever happened. A rejected registration is exactly when a person
        // walks away from the tab, and the password should not still be here.
        setDraft((current) => ({ ...current, password: "" }));
      }
    });
  };

  const rotate = (source: DataSource) => {
    const submitted = newPassword;
    void run(async () => {
      try {
        await api.rotateCredentials(orgId, source.id, { password: submitted });
        setRotating(null);
      } finally {
        setNewPassword("");
      }
    });
  };

  const test = (source: DataSource) =>
    void run(async () => {
      const result = await api.testDataSource(orgId, source.id);
      setResults((current) => ({ ...current, [source.id]: result }));
    });

  const note = (source: DataSource, said: RefreshResult | ProfileResult) =>
    setCatalogNotes((current) => ({ ...current, [source.id]: said.detail }));

  const refresh = (source: DataSource) =>
    void run(async () => {
      note(source, await api.refreshCatalog(orgId, source.id));
    });

  const profileSource = (source: DataSource) =>
    void run(async () => {
      note(source, await api.profileCatalog(orgId, source.id));
    });

  return (
    <Stack>
      <Card
        title="Data sources"
        subtitle={
          isAdmin
            ? "The databases this organization can ask questions about."
            : "The databases this organization can ask questions about. Only an Admin can add or change them."
        }
      >
        {sources === null && !error && <p className={styles.muted}>Loading…</p>}
        {sources?.length === 0 && (
          <p className={styles.muted}>
            {isAdmin
              ? "None yet. Register one below — use a login that can only read."
              : "None yet. An Admin can register one."}
          </p>
        )}

        {sources && sources.length > 0 && (
          <ul className={styles.list}>
            {sources.map((source) => (
              <li className={styles.item} key={source.id}>
                <div className={styles.itemHead}>
                  <div>
                    <h3 className={styles.name}>{source.name}</h3>
                    <p className={styles.address}>
                      <code>{source.host_display}</code>
                    </p>
                  </div>
                  <Row>
                    <Badge tone="sky">{ENGINE_NAMES[source.engine] ?? source.engine}</Badge>
                    <Badge tone={STATUS_TONES[source.status] ?? "neutral"}>
                      {statusWord(source)}
                    </Badge>
                    {/* The mode, not a tick: `require` encrypts and verifies
                        nothing, and a green shield would say otherwise. */}
                    <Badge tone={source.tls_mode === "prefer" ? "peach" : "lilac"}>
                      TLS: {source.tls_mode}
                    </Badge>
                  </Row>
                </div>

                <dl className={styles.facts}>
                  <dt>Connects as</dt>
                  <dd>
                    an account ending <code>{source.username_last4}</code>
                  </dd>
                  <dt>Last proven read-only</dt>
                  <dd>{when(source.last_verified_at)}</dd>
                </dl>

                {results[source.id] && <Result result={results[source.id] as TestResult} />}

                {catalogNotes[source.id] && (
                  <p className={styles.muted}>{catalogNotes[source.id]}</p>
                )}

                {(canRefresh || isAdmin) && (
                  <div className={styles.actions}>
                    <Link href={`/orgs/${orgId}/data-sources/${source.id}/catalog`}>
                      <Button>Catalog</Button>
                    </Link>
                    {canRefresh && (
                      <>
                        <Button disabled={busy} onClick={() => refresh(source)}>
                          Refresh catalog
                        </Button>
                        <Button disabled={busy} onClick={() => profileSource(source)}>
                          Profile columns
                        </Button>
                      </>
                    )}
                  </div>
                )}

                {isAdmin && (
                  <div className={styles.actions}>
                    <Button disabled={busy} onClick={() => test(source)}>
                      Test connection
                    </Button>
                    <Button
                      disabled={busy}
                      onClick={() => {
                        setNewPassword("");
                        setRotating(rotating === source.id ? null : source.id);
                      }}
                    >
                      {rotating === source.id ? "Cancel" : "Rotate password"}
                    </Button>
                    <Button
                      variant="danger"
                      disabled={busy}
                      onClick={() => void run(() => api.removeDataSource(orgId, source.id))}
                    >
                      Remove
                    </Button>
                  </div>
                )}

                {isAdmin && rotating === source.id && (
                  <form
                    className={styles.rotate}
                    onSubmit={(event) => {
                      event.preventDefault();
                      rotate(source);
                    }}
                  >
                    <Row>
                      <Input
                        label="New password"
                        type="password"
                        autoComplete="new-password"
                        value={newPassword}
                        onChange={(event) => setNewPassword(event.target.value)}
                      />
                      <Button variant="primary" type="submit" disabled={busy || !newPassword}>
                        Save
                      </Button>
                    </Row>
                    <p className={styles.muted}>
                      Rotating retires the read-only verification until the connection is tested
                      again.
                    </p>
                  </form>
                )}
              </li>
            ))}
          </ul>
        )}

        {error && <p className={styles.error}>{error}</p>}
      </Card>

      {isAdmin && (
        <Card
          title="Register a database"
          subtitle="Use a login that can only read. The password is stored encrypted, outside this application's database, and is never shown again."
        >
          <form onSubmit={register}>
            <Row>
              <Input
                label="Name"
                value={draft.name}
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                placeholder="Pizza demo"
              />
              <Select
                label="Engine"
                options={ENGINES}
                value={draft.engine}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    engine: event.target.value,
                    port: DEFAULT_PORTS[event.target.value] ?? draft.port,
                  })
                }
              />
            </Row>
            <Row>
              <Input
                label="Host"
                value={draft.host}
                onChange={(event) => setDraft({ ...draft, host: event.target.value })}
                placeholder="localhost"
              />
              <Input
                label="Port"
                type="number"
                value={draft.port}
                onChange={(event) => setDraft({ ...draft, port: event.target.value })}
              />
              <Input
                label="Database"
                value={draft.database}
                onChange={(event) => setDraft({ ...draft, database: event.target.value })}
                placeholder="pizza"
              />
            </Row>
            <Row>
              <Input
                label="Username"
                autoComplete="off"
                value={draft.username}
                onChange={(event) => setDraft({ ...draft, username: event.target.value })}
                placeholder="pizza_readonly"
              />
              <Input
                label="Password"
                type="password"
                autoComplete="new-password"
                value={draft.password}
                onChange={(event) => setDraft({ ...draft, password: event.target.value })}
              />
              <Select
                label="Encryption"
                options={["", ...TLS_MODES]}
                value={draft.tlsMode}
                onChange={(event) => setDraft({ ...draft, tlsMode: event.target.value })}
              />
            </Row>
            <Row>
              <Button
                variant="primary"
                type="submit"
                disabled={
                  busy ||
                  !draft.name.trim() ||
                  !draft.host.trim() ||
                  !draft.database.trim() ||
                  !draft.username.trim() ||
                  !draft.password
                }
              >
                {busy ? "Registering…" : "Register"}
              </Button>
            </Row>
            <p className={styles.muted}>
              Leave Encryption blank to use this deployment&rsquo;s policy: TLS is required for any
              database that is not on the server itself. The password field is cleared when you
              submit, whether it worked or not.
            </p>
          </form>
        </Card>
      )}
    </Stack>
  );
}
