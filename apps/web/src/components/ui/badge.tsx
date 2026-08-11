import type { ReactNode } from "react";

import styles from "./badge.module.css";

export type Tone = "mint" | "sky" | "lilac" | "peach" | "rose" | "neutral";

/** Roles and statuses use this and nothing else. */
export function Badge({ tone = "neutral", children }: { tone?: Tone | undefined; children: ReactNode }) {
  return <span className={`${styles.badge} ${styles[tone]}`}>{children}</span>;
}

/** Colour never carries meaning alone, so the word is always present too. */
export const ROLE_TONES: Record<string, Tone> = {
  admin: "mint",
  contributor: "sky",
  reader: "lilac",
};
