"use client";

/**
 * The front door (WP13.1b).
 *
 * **A member with an organization lands in chat**, not on a profile page with an
 * Ask button on it. That is the whole direction of this work package: you open
 * the app and start talking to your data.
 *
 * Three states, and the second is the one worth being careful about:
 *
 *   1. Signed out → the sign-in card.
 *   2. Signed in, **no organization yet** → the bootstrap profile, unchanged.
 *      This is a real state, not an edge case: it is every first login, and it
 *      is the only screen from which an organization can be created. Redirecting
 *      it anywhere would strand a new person on a chat that has nothing to talk
 *      to.
 *   3. Signed in with organizations → straight into the first one's chat.
 *
 * **Redirecting happens only when there is exactly one organization**, and that
 * restraint is the whole of the multi-org story for now. The shell has no
 * organization switcher, so redirecting a member of two into the first would
 * make the second reachable only by typing a URL — a screen you can leave but
 * cannot return to. With several, the list below is shown and each entry links
 * into its own chat. A switcher in the sidebar is **B-141**.
 */

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Profile } from "@/components/screens/profile";
import { SignIn } from "@/components/screens/sign-in";
import { ApiHealth } from "@/components/api-health";
import { Page, PageHeader, Stack } from "@/components/ui/page";
import { createApi } from "@/lib/api-client";
import { useSession } from "@/lib/auth/session";

export default function Home() {
  const session = useSession();
  const router = useRouter();
  const api = useMemo(() => createApi(session.getToken), [session.getToken]);

  // `null` is "we have not asked yet". Rendering the bootstrap screen while the
  // answer is unknown would flash "create an organization" at people who have
  // one — the empty-state-as-loading-state mistake, one screen over.
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (!session.who) return;
    let alive = true;
    void (async () => {
      try {
        const me = await api.me();
        const only = me.memberships.length === 1 ? me.memberships[0] : undefined;
        if (alive && only) {
          router.replace(`/orgs/${only.org_id}/conversations`);
          return;
        }
      } catch {
        // Fall through to the profile, which reports its own failure and still
        // offers the one action available here.
      }
      if (alive) setChecked(true);
    })();
    return () => {
      alive = false;
    };
  }, [api, router, session.who]);

  if (session.who && !checked) return null;

  return (
    <Page>
      <PageHeader
        title="data-agent"
        subtitle="Ask questions of your own databases, and see the evidence behind every answer."
      />
      <Stack>{session.who ? <Profile /> : <SignIn />}</Stack>
      <ApiHealth />
    </Page>
  );
}
