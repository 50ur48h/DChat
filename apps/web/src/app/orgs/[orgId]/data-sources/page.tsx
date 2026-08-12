"use client";

import Link from "next/link";
import { use } from "react";

import { DataSources } from "@/components/screens/data-sources";
import { SignIn } from "@/components/screens/sign-in";
import { Button } from "@/components/ui/button";
import { Page, PageHeader } from "@/components/ui/page";
import { useSession } from "@/lib/auth/session";
import { useOrgRole } from "@/lib/use-org-role";

export default function DataSourcesPage({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = use(params);
  const session = useSession();
  const { role } = useOrgRole(orgId);

  return (
    <Page>
      <PageHeader
        title="Data sources"
        subtitle="The databases this organization asks questions about."
        action={
          <Link href={`/orgs/${orgId}`}>
            <Button>Back</Button>
          </Link>
        }
      />
      {session.who ? <DataSources orgId={orgId} role={role} /> : <SignIn />}
    </Page>
  );
}
