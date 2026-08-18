"use client";

import Link from "next/link";
import { use } from "react";

import { Definitions } from "@/components/screens/definitions";
import { SignIn } from "@/components/screens/sign-in";
import { Button } from "@/components/ui/button";
import { Page, PageHeader } from "@/components/ui/page";
import { useSession } from "@/lib/auth/session";
import { useOrgRole } from "@/lib/use-org-role";

export default function DefinitionsPage({
  params,
}: {
  params: Promise<{ orgId: string; dataSourceId: string }>;
}) {
  const { orgId, dataSourceId } = use(params);
  const session = useSession();
  const { role } = useOrgRole(orgId);

  return (
    <Page>
      <PageHeader
        title="Definitions"
        subtitle="What a metric means in this database — and which of those meanings the platform enforces on the queries it writes."
        action={
          <Link href={`/orgs/${orgId}/data-sources`}>
            <Button>Back</Button>
          </Link>
        }
      />
      {session.who ? (
        <Definitions orgId={orgId} dataSourceId={dataSourceId} role={role} />
      ) : (
        <SignIn />
      )}
    </Page>
  );
}
