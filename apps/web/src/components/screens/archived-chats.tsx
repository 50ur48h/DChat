"use client";

/**
 * Chats that were put away, and the way to bring them back (WP13.1b).
 *
 * **This screen is what makes the sidebar's promise true.** Archiving says the
 * thread can be restored, and before this existed there was nowhere to restore
 * it from — the old conversations list held the "Show archived" toggle and this
 * work package replaced that list with a sidebar. Shipping the sidebar without
 * this would have left a sentence in the product that the product could not
 * honour, which is precisely the defect class the archive-not-delete wording was
 * chosen to avoid.
 *
 * Every role may be here: these are your own threads, and a colleague's is a 404
 * even to an Admin (B-037).
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { SkeletonList } from "@/components/ui/skeleton";
import { ApiError, createApi, type Conversation } from "@/lib/api-client";
import { useSession } from "@/lib/auth/session";

import styles from "./archived-chats.module.css";

function label(conversation: Conversation): string {
  const title = conversation.title?.trim();
  return title && title.length > 0 ? title : "Untitled chat";
}

export function ArchivedChats({ orgId }: { orgId: string }) {
  const session = useSession();
  const api = useMemo(() => createApi(session.getToken), [session.getToken]);

  const [archived, setArchived] = useState<Conversation[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setArchived(await api.conversations(orgId, { archived: true }));
  }, [api, orgId]);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const threads = await api.conversations(orgId, { archived: true });
        if (alive) setArchived(threads);
      } catch (cause) {
        if (alive) {
          setError(
            cause instanceof ApiError ? cause.message : "Archived chats could not be loaded.",
          );
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [api, orgId]);

  const restore = useCallback(
    async (conversationId: string) => {
      setBusy(conversationId);
      setError(null);
      try {
        await api.archiveConversation(orgId, conversationId, false);
        await load();
      } catch (cause) {
        setError(cause instanceof ApiError ? cause.message : "That chat could not be restored.");
      } finally {
        setBusy(null);
      }
    },
    [api, orgId, load],
  );

  return (
    <Card
      title="Archived chats"
      subtitle="Put away, not deleted — every answer and its trace is still here."
    >
      {archived === null && !error && <SkeletonList rows={2} label="Loading archived chats" />}
      {archived?.length === 0 && (
        <EmptyState
          icon={
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 7h18v3H3zM5 10v9h14v-9M10 14h4" />
            </svg>
          }
          title="Nothing archived"
          action={null}
        >
          Chats you put away appear here, with their answers and traces intact —
          and you can bring any of them back.
        </EmptyState>
      )}
      {error && <p className={styles.error}>{error}</p>}

      {archived && archived.length > 0 && (
        <ul className={styles.list}>
          {archived.map((conversation) => (
            <li className={styles.item} key={conversation.id}>
              <span className={styles.title}>{label(conversation)}</span>
              <Button disabled={busy === conversation.id} onClick={() => void restore(conversation.id)}>
                {busy === conversation.id ? "Restoring…" : "Restore"}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
