"use client";

/**
 * The frame every org screen sits in: a chat rail on the left, the page beside it.
 *
 * **One `/v1/me` for the whole shell.** The sidebar needs the person's name and
 * the organization's; each screen inside needs neither. Fetching it here means a
 * navigation between chats does not re-ask, and there is one place that knows
 * whether the caller is a member of this organization at all.
 *
 * **Signed out, this renders nothing but the sign-in card.** A shell drawn
 * around a sign-in prompt would show an empty chat list and a settings link to a
 * person who has no session — controls that can only fail, which is B-008's
 * lesson one layer up from roles.
 */

import { useEffect, useMemo, useState, type ReactNode } from "react";

import { SignIn } from "@/components/screens/sign-in";
import { Page } from "@/components/ui/page";
import { createApi, type Me } from "@/lib/api-client";
import { useSession } from "@/lib/auth/session";

import { Sidebar } from "./sidebar";
import styles from "./app-shell.module.css";

export function AppShell({ orgId, children }: { orgId: string; children: ReactNode }) {
  const session = useSession();
  const api = useMemo(() => createApi(session.getToken), [session.getToken]);
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    if (!session.who) return;
    let alive = true;
    void (async () => {
      try {
        const next = await api.me();
        if (alive) setMe(next);
      } catch {
        // The shell degrades to unnamed rather than blocking the page: the
        // screens inside fetch their own data and report their own failures.
        if (alive) setMe(null);
      }
    })();
    return () => {
      alive = false;
    };
  }, [api, session.who]);

  if (!session.who) {
    return (
      <Page>
        <SignIn />
      </Page>
    );
  }

  const membership = me?.memberships.find((entry) => entry.org_id === orgId) ?? null;

  return (
    <div className={styles.shell}>
      <Sidebar
        orgId={orgId}
        orgName={membership?.org_name ?? null}
        person={me ? { email: me.email, name: me.name } : null}
      />
      <main className={styles.main}>{children}</main>
    </div>
  );
}
