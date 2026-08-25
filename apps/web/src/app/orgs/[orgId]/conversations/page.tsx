"use client";

/**
 * The app's home: an empty chat (WP13.1b).
 *
 * No `Page`/`PageHeader` chrome and no Back button — the shell's sidebar is the
 * navigation now, and a title bar reading "Ask" above a composer that already
 * says what it is would be furniture. Signing in is handled by the shell.
 */

import { use } from "react";

import { ChatHome } from "@/components/screens/chat-home";

export default function ConversationsPage({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = use(params);
  return <ChatHome orgId={orgId} />;
}
