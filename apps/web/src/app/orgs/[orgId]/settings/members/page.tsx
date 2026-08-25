"use client";

/**
 * Who belongs to this organization (WP13.1b).
 *
 * Moved here from `/orgs/{orgId}`, which is now the chat. The screen itself is
 * unchanged and still gates its own controls on the caller's role (B-008).
 */

import Link from "next/link";
import { use } from "react";

import { Members } from "@/components/screens/members";
import { Button } from "@/components/ui/button";
import { Page, PageHeader } from "@/components/ui/page";
import { useOrgRole } from "@/lib/use-org-role";

export default function MembersPage({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = use(params);
  const { role } = useOrgRole(orgId);

  return (
    <Page>
      <PageHeader
        title="Members"
        subtitle="Who belongs here, and what they may do."
        action={
          <Link href={`/orgs/${orgId}/settings`}>
            <Button>Back to settings</Button>
          </Link>
        }
      />
      <Members orgId={orgId} role={role} />
    </Page>
  );
}
