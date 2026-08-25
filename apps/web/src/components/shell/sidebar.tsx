"use client";

/**
 * The chat sidebar: new chat, your threads, and the way into settings.
 *
 * **Every role sees this, and it holds no admin controls.** Architecture 6.2
 * grants "ask questions / view own conversations & traces" to Reader,
 * Contributor and Admin alike, and the list shows only your own — a colleague's
 * thread is a 404 even to an Admin (B-037). Nothing here is gated on role
 * because nothing here is privileged; the admin pages live behind Settings and
 * gate themselves.
 *
 * **"Archive" says archive** (D-039, and the owner's instruction on 2026-08-25).
 * Nothing is destroyed: the runs, their events, their findings and their query
 * executions stay where they are, which is what makes a trace worth having. A
 * trash icon that quietly archived would be the same class of lie as a badge
 * reading *answered* on a refusal (B-133) — the word and the action disagreeing,
 * with the word winning. So the control is a word, it is the true word, and the
 * confirmation says the thread can be brought back.
 *
 * **Collapsed or expanded survives a reload**, in `localStorage`, per browser.
 * It is a per-viewer convenience and nothing else depends on it, so a browser
 * that refuses to remember simply starts expanded.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";

import { Button } from "@/components/ui/button";
import { ApiError, createApi, type Conversation } from "@/lib/api-client";
import { useSession } from "@/lib/auth/session";
import { personLabel, type Named } from "@/lib/identity";
import { createPersisted } from "@/lib/persisted";

import styles from "./sidebar.module.css";

const COLLAPSED_KEY = "dataagent.sidebar-collapsed";

/** A thread with no title yet is shown by what it is, not by a blank. */
function threadLabel(conversation: Conversation): string {
  const title = conversation.title?.trim();
  return title && title.length > 0 ? title : "New chat";
}

// Stored as a string because `createPersisted` deals in string enumerations —
// see `persisted.ts` for why a parsed value would break `getSnapshot`.
function isFlag(value: string): value is "0" | "1" {
  return value === "0" || value === "1";
}

const collapsedStore = createPersisted<"0" | "1">(COLLAPSED_KEY, "0", isFlag);

export function Sidebar({
  orgId,
  orgName,
  person,
}: {
  orgId: string;
  orgName: string | null;
  person: Named | null;
}) {
  const session = useSession();
  const pathname = usePathname();
  const api = useMemo(() => createApi(session.getToken), [session.getToken]);

  // `null` is "not loaded yet", which is a different claim from "you have none".
  const [conversations, setConversations] = useState<Conversation[] | null>(null);
  // Read through the store rather than an effect, so the server render and the
  // first client render agree and there is no hydration mismatch to suppress.
  const collapsed =
    useSyncExternalStore(collapsedStore.subscribe, collapsedStore.get, collapsedStore.getServer) ===
    "1";
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const threads = await api.conversations(orgId, { archived: false });
    setConversations(threads);
  }, [api, orgId]);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const threads = await api.conversations(orgId, { archived: false });
        if (alive) setConversations(threads);
      } catch (cause) {
        if (alive) {
          setError(cause instanceof ApiError ? cause.message : "Your chats could not be loaded.");
        }
      }
    })();
    return () => {
      alive = false;
    };
    // `pathname` is a dependency on purpose: starting a new chat navigates, and
    // the sidebar has to show the thread that was just created without a reload.
  }, [api, orgId, pathname]);

  // Per-viewer convenience only: a browser that refuses to remember simply
  // starts expanded every time, and nothing else depends on it.
  const toggleCollapsed = useCallback(() => {
    collapsedStore.set(collapsed ? "0" : "1");
  }, [collapsed]);

  const rename = useCallback(
    async (conversationId: string) => {
      const title = draft.trim();
      if (title.length === 0) {
        setRenaming(null);
        return;
      }
      setBusy(conversationId);
      setError(null);
      try {
        await api.renameConversation(orgId, conversationId, title);
        setRenaming(null);
        await load();
      } catch (cause) {
        setError(cause instanceof ApiError ? cause.message : "That chat could not be renamed.");
      } finally {
        setBusy(null);
      }
    },
    [api, orgId, draft, load],
  );

  const archive = useCallback(
    async (conversationId: string) => {
      setBusy(conversationId);
      setError(null);
      try {
        await api.archiveConversation(orgId, conversationId, true);
        setConfirming(null);
        await load();
      } catch (cause) {
        setError(cause instanceof ApiError ? cause.message : "That chat could not be archived.");
      } finally {
        setBusy(null);
      }
    },
    [api, orgId, load],
  );

  if (collapsed) {
    return (
      <nav className={styles.railCollapsed} aria-label="Chats">
        <button
          type="button"
          className={styles.iconButton}
          onClick={toggleCollapsed}
          aria-label="Expand the sidebar"
          aria-expanded={false}
        >
          <span aria-hidden="true">»</span>
        </button>
        <Link
          href={`/orgs/${orgId}/conversations`}
          className={styles.iconButton}
          aria-label="New chat"
        >
          <span aria-hidden="true">+</span>
        </Link>
      </nav>
    );
  }

  return (
    <nav className={styles.rail} aria-label="Chats">
      <div className={styles.head}>
        <span className={styles.org}>{orgName ?? "Your organization"}</span>
        <button
          type="button"
          className={styles.iconButton}
          onClick={toggleCollapsed}
          aria-label="Collapse the sidebar"
          aria-expanded
        >
          <span aria-hidden="true">«</span>
        </button>
      </div>

      <Link href={`/orgs/${orgId}/conversations`} className={styles.newChat}>
        <span aria-hidden="true">+</span> New chat
      </Link>

      <div className={styles.list}>
        {conversations === null && !error && <p className={styles.muted}>Loading…</p>}
        {conversations?.length === 0 && (
          <p className={styles.muted}>No chats yet. Ask something to start one.</p>
        )}
        {error && <p className={styles.error}>{error}</p>}

        {conversations?.map((conversation) => {
          const href = `/orgs/${orgId}/conversations/${conversation.id}`;
          const current = pathname === href;

          if (renaming === conversation.id) {
            return (
              <div className={styles.item} key={conversation.id}>
                <input
                  className={styles.renameInput}
                  aria-label="Chat title"
                  value={draft}
                  maxLength={300}
                  autoFocus
                  disabled={busy === conversation.id}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void rename(conversation.id);
                    if (event.key === "Escape") setRenaming(null);
                  }}
                  onBlur={() => void rename(conversation.id)}
                />
              </div>
            );
          }

          if (confirming === conversation.id) {
            return (
              <div className={styles.confirm} key={conversation.id}>
                {/* The word matches the action, and says the reverse exists.
                    Nothing here deletes anything (D-039). */}
                <p className={styles.confirmText}>
                  Archive “{threadLabel(conversation)}”? Its answers and traces are kept, and
                  you can bring it back.
                </p>
                <div className={styles.confirmActions}>
                  <Button
                    variant="primary"
                    disabled={busy === conversation.id}
                    onClick={() => void archive(conversation.id)}
                  >
                    Archive
                  </Button>
                  <Button disabled={busy === conversation.id} onClick={() => setConfirming(null)}>
                    Cancel
                  </Button>
                </div>
              </div>
            );
          }

          return (
            <div
              className={current ? `${styles.item} ${styles.itemCurrent}` : styles.item}
              key={conversation.id}
            >
              <Link href={href} className={styles.itemLink} aria-current={current ? "page" : undefined}>
                {threadLabel(conversation)}
              </Link>
              <div className={styles.itemActions}>
                <button
                  type="button"
                  className={styles.itemAction}
                  onClick={() => {
                    setDraft(conversation.title ?? "");
                    setRenaming(conversation.id);
                  }}
                  aria-label={`Rename ${threadLabel(conversation)}`}
                >
                  Rename
                </button>
                <button
                  type="button"
                  className={styles.itemAction}
                  onClick={() => setConfirming(conversation.id)}
                  aria-label={`Archive ${threadLabel(conversation)}`}
                >
                  Archive
                </button>
              </div>
            </div>
          );
        })}
      </div>

      <div className={styles.foot}>
        <Link href={`/orgs/${orgId}/settings`} className={styles.footLink}>
          <span className={styles.person}>
            {person ? personLabel(person, session.who ?? "Signed in") : (session.who ?? "Signed in")}
          </span>
          <span className={styles.footHint}>Settings</span>
        </Link>
      </div>
    </nav>
  );
}
