"use client";

/**
 * Your conversations, and starting a new one (plan WP7.3, D-022).
 *
 * The database is chosen **here**, when the thread starts, and never again —
 * because a follow-up question has to reach the same source as the question it
 * follows, or two answers in one thread come from two databases with nothing
 * saying so.
 *
 * **The picker never guesses, and neither does the API.** With one registered
 * source it is preselected, because there is nothing to choose. With several,
 * nothing is selected and the button stays disabled until somebody picks: the
 * scheduler would refuse such a run anyway (WP7.2c), and a screen that quietly
 * chose the first one would be making exactly the decision the API declines to
 * make. A silently wrong database produces a confident, correctly-cited answer
 * about someone else's data.
 *
 * With no sources at all the form is replaced by a sentence saying so and
 * pointing at the screen that fixes it, rather than a control that can only
 * fail.
 *
 * Every role may be here: architecture 6.2 grants "ask questions / view own
 * conversations & traces" to Reader, Contributor and Admin alike. The list shows
 * only your own — a colleague's is a 404 even to an Admin (B-037).
 */

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Row, Stack } from "@/components/ui/page";
import { ApiError, createApi, type Conversation, type DataSource } from "@/lib/api-client";
import { useSession } from "@/lib/auth/session";

import styles from "./conversations.module.css";

function when(timestamp: string): string {
  const parsed = new Date(timestamp);
  return Number.isNaN(parsed.getTime()) ? "" : parsed.toLocaleDateString();
}

export function Conversations({ orgId }: { orgId: string }) {
  const session = useSession();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [sources, setSources] = useState<DataSource[] | null>(null);
  const [chosen, setChosen] = useState("");
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [started, setStarted] = useState<string | null>(null);

  const api = useMemo(() => createApi(session.getToken), [session.getToken]);

  useEffect(() => {
    // Guarded so a slow response cannot write into an unmounted screen.
    let active = true;
    void (async () => {
      try {
        const [threads, registered] = await Promise.all([
          api.conversations(orgId),
          api.dataSources(orgId),
        ]);
        if (!active) return;
        setConversations(threads);
        setSources(registered);
        // Preselected only when there is nothing to choose between. With
        // several, the empty value stands and the button stays disabled —
        // choosing the first would be exactly the guess the API declines.
        if (registered.length === 1 && registered[0]) setChosen(registered[0].id);
      } catch (cause) {
        if (active) {
          setError(cause instanceof ApiError ? cause.message : "This page could not be loaded.");
        }
      }
    })();
    return () => {
      active = false;
    };
  }, [api, orgId]);

  const start = useCallback(async () => {
    setStarting(true);
    setError(null);
    try {
      const created = await api.createConversation(orgId, {
        dataSourceId: chosen || undefined,
      });
      setStarted(created.id);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The conversation could not be started.");
    } finally {
      setStarting(false);
    }
  }, [api, orgId, chosen]);

  const several = (sources?.length ?? 0) > 1;

  return (
    <Stack>
      <Card title="Start a conversation" subtitle="Questions here are answered against one database.">
        {sources === null ? (
          <p className={styles.note}>Loading…</p>
        ) : sources.length === 0 ? (
          <p className={styles.note}>
            No database is registered for this organization yet, so there is nothing to ask about.
            An Admin can add one on the{" "}
            <Link href={`/orgs/${orgId}/data-sources`} className={styles.link}>
              data sources
            </Link>{" "}
            screen.
          </p>
        ) : (
          <>
            <div className={styles.field}>
              <label className={styles.label} htmlFor="data-source">
                Database
              </label>
              <select
                id="data-source"
                className={styles.select}
                value={chosen}
                disabled={starting}
                onChange={(event) => setChosen(event.target.value)}
              >
                {several && <option value="">Choose a database…</option>}
                {sources.map((source) => (
                  <option key={source.id} value={source.id}>
                    {source.name}
                  </option>
                ))}
              </select>
            </div>
            {several && chosen === "" && (
              <p className={styles.note}>
                This organization has more than one database. Pick the one your question is
                about — nothing will guess for you, because a confident answer drawn from the
                wrong database is the one mistake that does not look like a mistake.
              </p>
            )}
            <Row>
              <Button
                variant="primary"
                disabled={starting || chosen === ""}
                onClick={() => void start()}
              >
                {starting ? "Starting…" : "Start"}
              </Button>
            </Row>
          </>
        )}
        {error && <p className={styles.error}>{error}</p>}
        {started && (
          <p className={styles.note}>
            <Link href={`/orgs/${orgId}/conversations/${started}`} className={styles.link}>
              Open the new conversation
            </Link>
          </p>
        )}
      </Card>

      <Card title="Your conversations" subtitle="Only yours — a colleague's is not visible here.">
        {conversations.length === 0 ? (
          <p className={styles.note}>Nothing yet. Start one above.</p>
        ) : (
          <ul className={styles.list}>
            {conversations.map((conversation) => (
              <li key={conversation.id} className={styles.item}>
                <Link
                  href={`/orgs/${orgId}/conversations/${conversation.id}`}
                  className={styles.itemLink}
                >
                  {conversation.title ?? "Untitled conversation"}
                </Link>
                <Row>
                  {conversation.data_source_name ? (
                    <Badge tone="lilac">{conversation.data_source_name}</Badge>
                  ) : (
                    <Badge tone="peach">no database chosen</Badge>
                  )}
                  <span className={styles.meta}>
                    {conversation.message_count} message
                    {conversation.message_count === 1 ? "" : "s"} · {when(conversation.created_at)}
                  </span>
                </Row>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </Stack>
  );
}
