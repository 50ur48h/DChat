/**
 * Something is happening and there is nothing to count (docs/design.md).
 *
 * For a one-shot request that returns once — a catalog refresh, a column
 * profile, a connection test. A word saying what is happening, optionally an
 * indeterminate bar, and then the caller replaces the whole thing with **the
 * real result sentence**.
 *
 * **It must never be given steps.** An operation that reports no steps can only
 * be given invented ones, and inventing them in the client is exactly what D-048
 * refuses. If the work does report its steps, it wants the working state
 * instead.
 *
 * The word is a live region so the change is announced; the bar is decorative
 * and hidden, because it conveys nothing the word does not.
 */

import styles from "./pending.module.css";

export function Pending({
  children,
  bar = false,
  spinner = true,
}: {
  /** What is happening, in words. "Reading the schema…" */
  children: string;
  /** An indeterminate bar, for an operation worth showing at full width. */
  bar?: boolean;
  spinner?: boolean;
}) {
  return (
    <div className={styles.pending}>
      <p className={styles.row}>
        {spinner && <span className={styles.spinner} aria-hidden="true" />}
        <span aria-live="polite" className={styles.word}>
          {children}
        </span>
      </p>
      {bar && <span className={styles.bar} aria-hidden="true" />}
    </div>
  );
}
