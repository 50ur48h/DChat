"use client";

/**
 * Where a chat begins: a composer, and nothing to configure first (WP13.1b).
 *
 * This replaces the "Start a conversation" screen — a database picker, a Start
 * button, and a link to the thread it made. Three steps before a question could
 * be typed, and D-045 removed the reason for the first of them.
 *
 * **The conversation is created on send, not on arrival.** A "New chat" that
 * created a row immediately would fill the sidebar with empty threads every time
 * somebody clicked it and changed their mind. So this screen holds a draft, and
 * one send does three things in order: create the thread, post the question,
 * then navigate to it. The conversation screen picks the run up from
 * `last_run_id` on mount and polls it to completion — the machinery that already
 * works, reused rather than re-implemented.
 *
 * **The failure case is the reason the steps are separate.** If the thread is
 * created and the question then fails to post, the person stays here with their
 * text and the reason — navigating away would throw the draft away, which is the
 * one thing they cannot get back. The half-made thread is **held and reused** on
 * the next attempt rather than abandoned, so retrying does not leave a trail of
 * empty chats in the sidebar, and the idempotency key is held with it (D-019) so
 * a retry after a timeout replays the same key instead of buying a second run.
 *
 * **A Reader sees exactly this screen.** Architecture 6.2 grants asking to every
 * role, and nothing here is privileged. What differs is what an *Admin* can do
 * about a missing database, and the message below says which of the two you are.
 */

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { ApiError, createApi, type ActiveDataSource, type DataSource } from "@/lib/api-client";
import { useSession } from "@/lib/auth/session";
import { useOrgRole } from "@/lib/use-org-role";

import styles from "./chat-home.module.css";

export function ChatHome({ orgId }: { orgId: string }) {
  const session = useSession();
  const router = useRouter();
  const api = useMemo(() => createApi(session.getToken), [session.getToken]);
  const { role } = useOrgRole(orgId);

  const [question, setQuestion] = useState("");
  const [active, setActive] = useState<ActiveDataSource | null>(null);
  //: Read alongside the choice so this screen can tell a person the truth about
  //: whether asking will work — see `answersFrom` below.
  const [sources, setSources] = useState<DataSource[] | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLTextAreaElement | null>(null);

  /**
   * One key per draft, so a retry after a timeout replays it (D-019).
   *
   * Held in a ref rather than state: it must survive a re-render without
   * causing one, and it must **not** change between a failed attempt and the
   * retry — that is the whole point. A fresh key is minted only after a send
   * that actually created a run.
   */
  const idempotencyKey = useRef<string>(crypto.randomUUID());

  /**
   * A thread that was created before its first question failed to post.
   *
   * Reused by the next attempt: without it, every retry would create another
   * empty conversation, and the sidebar would fill with "New chat" rows nobody
   * asked for.
   */
  const pending = useRef<string | null>(null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const [chosen, registered] = await Promise.all([
          api.activeDataSource(orgId),
          api.dataSources(orgId),
        ]);
        if (!alive) return;
        setActive(chosen);
        setSources(registered);
      } catch (cause) {
        if (alive) {
          setError(cause instanceof ApiError ? cause.message : "This page could not be loaded.");
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [api, orgId]);

  const send = useCallback(async () => {
    const content = question.trim();
    if (content.length === 0) return;

    setSending(true);
    setError(null);
    try {
      const conversationId =
        pending.current ?? (await api.createConversation(orgId, {})).id;
      pending.current = conversationId;
      await api.ask(orgId, conversationId, content, idempotencyKey.current);
      // Only past this point is the question definitely somebody else's problem,
      // so only here are the key and the held thread retired.
      idempotencyKey.current = crypto.randomUUID();
      pending.current = null;
      router.push(`/orgs/${orgId}/conversations/${conversationId}`);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "That question could not be sent.");
      setSending(false);
    }
  }, [api, orgId, question, router]);

  /**
   * The database a question asked right now would actually reach, or null.
   *
   * **This mirrors `agent/scheduler.resolve_data_source`, and the mirroring is
   * deliberate rather than accidental duplication.** The server decides; this
   * only decides what to *say*. Gating purely on the Admin's choice looked
   * right and told a lie: an organization with exactly one registered database
   * and no explicit choice would have been shown *"nothing to ask about"* while
   * the platform would have resolved that database and answered perfectly well.
   * A screen that refuses on the product's behalf, in words the product would
   * contradict, is worse than no screen.
   *
   * So: the choice wins; otherwise a single registered source is what a run
   * would resolve; otherwise there is genuinely nothing to ask, and the two
   * reasons for that are different and are said differently below.
   */
  const answersFrom =
    active?.data_source_name ?? (sources?.length === 1 ? (sources[0]?.name ?? null) : null);

  // `null` while loading, so nothing claims a database is missing before the
  // answer has arrived.
  const loaded = active !== null && sources !== null;
  const ready = answersFrom !== null;
  const several = (sources?.length ?? 0) > 1;
  const isAdmin = role === "admin";

  // Focus once there is something to ask about. Not on mount: the field is
  // disabled until the database is known, and focusing a disabled control does
  // nothing at all.
  useEffect(() => {
    if (ready) box.current?.focus();
  }, [ready]);

  return (
    <div className={styles.home}>
      <div className={styles.middle}>
        <h1 className={styles.greeting}>What would you like to know?</h1>
        {loaded &&
          (ready ? (
            <p className={styles.source}>
              Answered from <strong>{answersFrom}</strong>
            </p>
          ) : several ? (
            // True, and the reason is the one WP7.2c refuses on: several
            // databases and nothing saying which. Naming the fix, not the fault.
            <p className={styles.source}>
              {isAdmin
                ? "This organization has more than one database and none is chosen, so a question has nowhere to go. Choose one in Settings → Data sources."
                : "This organization has more than one database and none is chosen, so a question has nowhere to go. An Admin can choose one."}
            </p>
          ) : (
            <p className={styles.source}>
              {isAdmin
                ? "No database is registered yet, so there is nothing to ask about. Add one in Settings → Data sources."
                : "No database is registered yet, so there is nothing to ask about. An Admin can add one."}
            </p>
          ))}

        <div className={styles.composer}>
          <textarea
            ref={box}
            className={styles.input}
            aria-label="Your question"
            placeholder="Ask anything about your data…"
            rows={3}
            maxLength={4000}
            value={question}
            disabled={sending || !ready}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              // Enter sends, Shift+Enter makes a new line — what a chat does.
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void send();
              }
            }}
          />
          <div className={styles.composerActions}>
            <Button
              variant="primary"
              disabled={sending || !ready || question.trim().length === 0}
              onClick={() => void send()}
            >
              {sending ? "Sending…" : "Send"}
            </Button>
          </div>
        </div>

        {error && <p className={styles.error}>{error}</p>}
      </div>
    </div>
  );
}
