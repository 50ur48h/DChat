"use client";

import { use } from "react";

import { Settings } from "@/components/screens/settings";
import { Page, PageHeader } from "@/components/ui/page";

export default function SettingsPage({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = use(params);

  return (
    <Page>
      <PageHeader title="Settings" subtitle="You, your organization, and how this looks." />
      <Settings orgId={orgId} />
    </Page>
  );
}
