"use client";

/**
 * One thread (WP13.2).
 *
 * **No page title and no Back button.** Both were leftovers from when a
 * conversation was a standalone page: in a chat product the thread *is* the
 * panel and the rail is the way back, so a header reading "Conversation" above
 * the conversation is furniture that competes with the answer. Signing in is the
 * shell's job.
 */

import { use } from "react";

import { ConversationThread } from "@/components/screens/conversation";

export default function ConversationPage({
  params,
}: {
  params: Promise<{ orgId: string; conversationId: string }>;
}) {
  const { orgId, conversationId } = use(params);
  return <ConversationThread orgId={orgId} conversationId={conversationId} />;
}
