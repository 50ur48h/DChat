"use client";

import Link from "next/link";
import { use } from "react";

import { Members } from "@/components/screens/members";
import { SignIn } from "@/components/screens/sign-in";
import { Button } from "@/components/ui/button";
import { Page, PageHeader, Row } from "@/components/ui/page";
import { useSession } from "@/lib/auth/session";
import { useOrgRole } from "@/lib/use-org-role";

export default function OrgPage({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = use(params);
  const session = useSession();
  const { role } = useOrgRole(orgId);

  return (
    <Page>
      <PageHeader
        title="Organization"
        subtitle="Who belongs here, and what they may do."
        action={
          <Row>
            {/* The one primary action on this page: asking is what the product
                is for, and every role may do it (architecture 6.2). */}
            <Link href={`/orgs/${orgId}/conversations`}>
              <Button variant="primary">Ask</Button>
            </Link>
            <Link href={`/orgs/${orgId}/data-sources`}>
              <Button>Data sources</Button>
            </Link>
            <Link href={`/orgs/${orgId}/documents`}>
              <Button>Documents</Button>
            </Link>
            <Link href="/">
              <Button>Back</Button>
            </Link>
          </Row>
        }
      />
      {session.who ? <Members orgId={orgId} role={role} /> : <SignIn />}
    </Page>
  );
}
