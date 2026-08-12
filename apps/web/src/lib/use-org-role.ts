"use client";

/**
 * Which role the signed-in person holds in one organization.
 *
 * The screens need it to avoid offering controls the API will refuse (B-008),
 * so the answer has to fail closed: until `/v1/me` says "admin", it is `null`
 * and nothing admin-only is shown. That costs an Admin a moment on a slow
 * connection, which is the cheaper mistake — the other way round shows buttons
 * that earn a 403 and teach people the product is broken.
 *
 * This decides what to *render*. It is not a permission check: the guard is on
 * the API, which refuses and audits regardless of what the browser believes.
 */

import { useEffect, useMemo, useState } from "react";

import { createApi } from "@/lib/api-client";
import { useSession } from "@/lib/auth/session";

export interface OrgRole {
  /** "admin" | "contributor" | "reader", or null while unknown. */
  role: string | null;
  loading: boolean;
}

export function useOrgRole(orgId: string): OrgRole {
  const session = useSession();
  const api = useMemo(() => createApi(session.getToken), [session.getToken]);

  const [role, setRole] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const me = await api.me();
        const membership = me.memberships.find((entry) => entry.org_id === orgId);
        if (active) setRole(membership?.role ?? null);
      } catch {
        // A failure here means the screen stays read-only. The list still
        // loads, and its own error message says what went wrong.
        if (active) setRole(null);
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [api, orgId]);

  return { role, loading };
}
