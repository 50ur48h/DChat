"use client";

import { Profile } from "@/components/screens/profile";
import { SignIn } from "@/components/screens/sign-in";
import { ApiHealth } from "@/components/api-health";
import { Page, PageHeader, Stack } from "@/components/ui/page";
import { useSession } from "@/lib/auth/session";

export default function Home() {
  const session = useSession();

  return (
    <Page>
      <PageHeader
        title="data-agent"
        subtitle="Ask questions of your own databases, and see the evidence behind every answer."
      />
      <Stack>{session.who ? <Profile /> : <SignIn />}</Stack>
      <ApiHealth />
    </Page>
  );
}
