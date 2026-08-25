"use client";

/**
 * The organization's front door is the chat (WP13.1b).
 *
 * This used to be the members screen with an **Ask** button on it, which is the
 * shape the whole work package is undoing: the product is a chat product, and
 * chat is where an organization opens. Members moved to Settings, with the rest
 * of the admin screens.
 *
 * A redirect rather than a deleted route, because this URL is in people's
 * history and in older docs — and because `/orgs/{id}` is the obvious thing to
 * type.
 */

import { useRouter } from "next/navigation";
import { use, useEffect } from "react";

export default function OrgPage({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = use(params);
  const router = useRouter();

  useEffect(() => {
    router.replace(`/orgs/${orgId}/conversations`);
  }, [router, orgId]);

  return null;
}
