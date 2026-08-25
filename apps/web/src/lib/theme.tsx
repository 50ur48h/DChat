"use client";

/**
 * Light or dark, chosen by a person and remembered in this browser (D-046).
 *
 * **Light is the default, and the operating system does not get a vote.** The
 * tokens used to switch on `prefers-color-scheme`, which meant anyone on a dark
 * desktop had never seen the design `docs/design.md` actually describes. Dark is
 * still fully supported; it is now something you choose.
 *
 * Three pieces have to agree, and they are deliberately small:
 *
 *   1. `globals.css` defines dark under `[data-theme="dark"]` and nothing else.
 *   2. A script in `layout.tsx` sets that attribute **before first paint**, so a
 *      person who chose dark never sees a white flash on the way in.
 *   3. This module is the only thing that writes the attribute afterwards, and
 *      the only thing that writes storage.
 *
 * The stored value is read through `useSyncExternalStore` rather than an effect,
 * so the server snapshot and the first client render agree — see `persisted.ts`
 * for why that is the primitive rather than `useState`.
 */

import { useCallback, useEffect, useMemo, useSyncExternalStore } from "react";

import { createPersisted } from "./persisted";

export type Theme = "light" | "dark";

/** Shared with the pre-paint script in `layout.tsx`, which cannot import this. */
export const THEME_STORAGE_KEY = "dataagent.theme";

function isTheme(value: string): value is Theme {
  return value === "light" || value === "dark";
}

const store = createPersisted<Theme>(THEME_STORAGE_KEY, "light", isTheme);

function apply(theme: Theme): void {
  const root = document.documentElement;
  // Light is the absence of the attribute, so there is one representation of it
  // rather than two that could drift.
  if (theme === "dark") root.setAttribute("data-theme", "dark");
  else root.removeAttribute("data-theme");
}

export interface ThemeChoice {
  theme: Theme;
  setTheme: (next: Theme) => void;
  toggle: () => void;
}

export function useTheme(): ThemeChoice {
  const theme = useSyncExternalStore(store.subscribe, store.get, store.getServer);

  const setTheme = useCallback((next: Theme) => {
    store.set(next);
  }, []);

  /**
   * Push the choice at the DOM, which is the one thing React does not own here.
   *
   * This is what an effect is actually for — updating an external system with
   * the latest state — and it is not the `setState`-in-an-effect the lint rule
   * rejects. Keying it on `theme` rather than doing it inside `setTheme` also
   * covers the case a direct call would miss: **another tab** changing the
   * choice arrives through the `storage` event, with no local call to hang the
   * DOM write off. Idempotent, so several components using this hook is fine.
   */
  useEffect(() => {
    apply(theme);
  }, [theme]);

  return useMemo(
    () => ({ theme, setTheme, toggle: () => setTheme(theme === "dark" ? "light" : "dark") }),
    [theme, setTheme],
  );
}
