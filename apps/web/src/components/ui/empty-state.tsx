/**
 * A place nobody has filled yet — not a page that failed (docs/design.md).
 *
 * One muted sentence was what these used to be, and it reads as a failure. A
 * mark, a title saying what belongs here, a line of why, and **exactly one
 * action**.
 *
 * **The action slot is also where a Reader is told who can act instead.** Giving
 * them a button the API would refuse teaches people the product is broken rather
 * than that they lack permission (B-008), so the caller passes either the
 * control or the sentence — never a disabled control, which looks operable and
 * is not.
 */

import type { ReactNode } from "react";

import styles from "./empty-state.module.css";

export function EmptyState({
  icon,
  title,
  children,
  action,
}: {
  /** A quiet glyph. Decorative — the title carries the meaning. */
  icon?: ReactNode;
  title: string;
  /** Why this is empty and what would fill it. */
  children: ReactNode;
  /** The one thing to do, or a line naming who can do it. */
  action?: ReactNode;
}) {
  return (
    <div className={styles.empty}>
      {icon && (
        <span className={styles.mark} aria-hidden="true">
          {icon}
        </span>
      )}
      <div className={styles.words}>
        <p className={styles.title}>{title}</p>
        <p className={styles.body}>{children}</p>
      </div>
      {action}
    </div>
  );
}
