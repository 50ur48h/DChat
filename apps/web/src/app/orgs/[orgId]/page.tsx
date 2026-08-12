"use client";

import Link from "next/link";
import { use } from "react";

import { Members } from "@/components/screens/members";
import { SignIn } from "@/components/screens/sign-in";
import { Button } from "@/components/ui/button";
import { Page, PageHeader } from "@/components/ui/page";
import { useSession } from "@/lib/auth/session";

export default function OrgPage({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = use(params);
  const session = useSession();

  return (
    <Page>
      <PageHeader
        title="Organization"
        subtitle="Who belongs here, and what they may do."
        action={
          <Link href="/">
            <Button>Back</Button>
          </Link>
        }
      />
      {session.who ? <Members orgId={orgId} /> : <SignIn />}
    </Page>
  );
}
