"use client";

/**
 * Whether the rail is collapsed — shared by the rail and the page beside it.
 *
 * **It has to be shared now that the rail is `position: fixed`.** A fixed rail
 * is out of the document flow, so the page no longer sits beside it
 * automatically: something has to reserve the width, and that something is a
 * margin on the main element. Two components therefore need the same answer,
 * and a copy in each would be two answers that drift.
 *
 * Read through `useSyncExternalStore` for the reason `persisted.ts` explains:
 * the server has no storage, so the snapshot it renders and the first client
 * render have to agree or hydration complains.
 */

import { useSyncExternalStore } from "react";

import { createPersisted } from "@/lib/persisted";

export const COLLAPSED_KEY = "dataagent.sidebar-collapsed";

/** Stored as a string, because `createPersisted` deals in string enumerations. */
function isFlag(value: string): value is "0" | "1" {
  return value === "0" || value === "1";
}

const store = createPersisted<"0" | "1">(COLLAPSED_KEY, "0", isFlag);

export function useSidebarCollapsed(): [boolean, () => void] {
  const collapsed = useSyncExternalStore(store.subscribe, store.get, store.getServer) === "1";
  return [collapsed, () => store.set(collapsed ? "0" : "1")];
}
