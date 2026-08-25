"use client";

/**
 * Every screen inside an organization sits in the chat shell (WP13.1b).
 *
 * A layout rather than a wrapper each page opts into, so a screen added later
 * cannot forget to be inside it — and so navigating between chats re-renders the
 * conversation without tearing down the rail beside it.
 */

import { use } from "react";

import { AppShell } from "@/components/shell/app-shell";

export default function OrgLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ orgId: string }>;
}) {
  const { orgId } = use(params);
  return <AppShell orgId={orgId}>{children}</AppShell>;
}
