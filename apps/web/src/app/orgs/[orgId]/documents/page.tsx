"use client";

import Link from "next/link";
import { use } from "react";

import { Documents } from "@/components/screens/documents";
import { SignIn } from "@/components/screens/sign-in";
import { Button } from "@/components/ui/button";
import { Page, PageHeader } from "@/components/ui/page";
import { useSession } from "@/lib/auth/session";
import { useOrgRole } from "@/lib/use-org-role";

export default function DocumentsPage({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = use(params);
  const session = useSession();
  const { role } = useOrgRole(orgId);

  return (
    <Page>
      <PageHeader
        title="Documents"
        subtitle="What this organization has written down — the definitions and policies the agent reads before it queries."
        action={
          <Link href={`/orgs/${orgId}`}>
            <Button>Back</Button>
          </Link>
        }
      />
      {session.who ? <Documents orgId={orgId} role={role} /> : <SignIn />}
    </Page>
  );
}
