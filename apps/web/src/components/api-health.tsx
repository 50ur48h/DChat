"use client";

import { useEffect, useState } from "react";

import { ApiError, apiBaseUrl, fetchHealth, type Health } from "@/lib/api-client";

import styles from "./api-health.module.css";

type State =
  | { kind: "loading" }
  | { kind: "healthy"; health: Health }
  | { kind: "unreachable"; message: string };

/**
 * Walking-skeleton widget: proves the browser can reach the API.
 *
 * Failure is a first-class state here, not a thrown error — an unreachable API
 * during local development is the normal case, and saying so plainly is more
 * useful than an empty page.
 */
export function ApiHealth() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();

    fetchHealth(controller.signal)
      .then((health) => setState({ kind: "healthy", health }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          kind: "unreachable",
          message: error instanceof ApiError ? error.message : "Unexpected error calling the API",
        });
      });

    return () => controller.abort();
  }, []);

  return (
    <section className={styles.card} aria-labelledby="api-health-heading">
      <h2 className={styles.heading} id="api-health-heading">
        API health
      </h2>

      {state.kind === "loading" && (
        <p className={styles.status} role="status">
          Checking…
        </p>
      )}

      {state.kind === "healthy" && (
        <>
          <p className={styles.status} role="status">
            <span className={styles.dotOk} aria-hidden="true" /> Healthy
          </p>
          <dl className={styles.details}>
            <dt>Version</dt>
            <dd>{state.health.version}</dd>
            <dt>Commit</dt>
            <dd>
              <code>{state.health.git_sha}</code>
            </dd>
          </dl>
        </>
      )}

      {state.kind === "unreachable" && (
        <>
          <p className={styles.status} role="status">
            <span className={styles.dotBad} aria-hidden="true" /> Unreachable
          </p>
          <p className={styles.error}>{state.message}</p>
        </>
      )}

      <p className={styles.target}>
        {/* One interpolation, so the URL is a single text node in the DOM. */}
        <code>{`${apiBaseUrl()}/healthz`}</code>
      </p>
    </section>
  );
}
