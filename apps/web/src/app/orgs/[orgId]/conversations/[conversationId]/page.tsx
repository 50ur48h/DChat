"use client";

import Link from "next/link";
import { use } from "react";

import { ConversationThread } from "@/components/screens/conversation";
import { SignIn } from "@/components/screens/sign-in";
import { Button } from "@/components/ui/button";
import { Page, PageHeader } from "@/components/ui/page";
import { useSession } from "@/lib/auth/session";

export default function ConversationPage({
  params,
}: {
  params: Promise<{ orgId: string; conversationId: string }>;
}) {
  const { orgId, conversationId } = use(params);
  const session = useSession();

  return (
    <Page>
      <PageHeader
        title="Conversation"
        subtitle="Every answer here names the query behind it."
        action={
          <Link href={`/orgs/${orgId}/conversations`}>
            <Button>Back</Button>
          </Link>
        }
      />
      {session.who ? (
        <ConversationThread orgId={orgId} conversationId={conversationId} />
      ) : (
        <SignIn />
      )}
    </Page>
  );
}
