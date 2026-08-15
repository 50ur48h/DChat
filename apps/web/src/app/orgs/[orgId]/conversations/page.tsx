"use client";

import Link from "next/link";
import { use } from "react";

import { Conversations } from "@/components/screens/conversations";
import { SignIn } from "@/components/screens/sign-in";
import { Button } from "@/components/ui/button";
import { Page, PageHeader } from "@/components/ui/page";
import { useSession } from "@/lib/auth/session";

export default function ConversationsPage({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = use(params);
  const session = useSession();

  return (
    <Page>
      <PageHeader
        title="Ask"
        subtitle="Ask a question of your data, and check the query behind the answer."
        action={
          <Link href={`/orgs/${orgId}`}>
            <Button>Back</Button>
          </Link>
        }
      />
      {session.who ? <Conversations orgId={orgId} /> : <SignIn />}
    </Page>
  );
}
