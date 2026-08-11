"use client";

import { Suspense } from "react";

import { AcceptInvite } from "@/components/screens/accept-invite";
import { SignIn } from "@/components/screens/sign-in";
import { Page, PageHeader } from "@/components/ui/page";
import { useSession } from "@/lib/auth/session";

function Body() {
  const session = useSession();
  // Sign in first: an invitation adds *you*, so we have to know who you are.
  return session.who ? <AcceptInvite /> : <SignIn />;
}

export default function AcceptInvitationPage() {
  return (
    <Page>
      <PageHeader
        title="Join an organization"
        subtitle="Invitations are single-use and expire after seven days."
      />
      <Suspense fallback={null}>
        <Body />
      </Suspense>
    </Page>
  );
}
