"use client";

import Link from "next/link";
import { use } from "react";

import { CatalogBrowser } from "@/components/screens/catalog";
import { SignIn } from "@/components/screens/sign-in";
import { Button } from "@/components/ui/button";
import { Page, PageHeader } from "@/components/ui/page";
import { useSession } from "@/lib/auth/session";
import { useOrgRole } from "@/lib/use-org-role";

export default function CatalogPage({
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
        title="Catalog"
        subtitle="What this database holds, and what may be seen of it."
        action={
          <Link href={`/orgs/${orgId}/data-sources`}>
            <Button>Back</Button>
          </Link>
        }
      />
      {session.who ? (
        <CatalogBrowser orgId={orgId} dataSourceId={dataSourceId} role={role} />
      ) : (
        <SignIn />
      )}
    </Page>
  );
}
