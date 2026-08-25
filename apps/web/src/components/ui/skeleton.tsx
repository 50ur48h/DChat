/**
 * What a list looks like before its rows arrive (docs/design.md, *Waiting*).
 *
 * **It claims the shape and nothing else.** A skeleton says how much is coming
 * and stops the page jumping when it lands; it makes no statement about progress,
 * so unlike a spinner or a bar it cannot turn out to have been wrong.
 *
 * Two rules it exists to enforce:
 *
 *   1. **Prefer too few rows.** Three skeletons where one item arrives is a
 *      small lie about the shape of the answer, and the page still jumps —
 *      upwards, which is worse, because the reader has already started reading.
 *   2. **A skeleton is not an empty state.** `null` means *not asked yet* and
 *      `[]` means *there is nothing*; rendering "Nothing yet" during a request
 *      tells somebody something false about their own data.
 *
 * `aria-hidden`, with a single `role="status"` label above it: a screen reader
 * should hear "Loading members" once, not eight anonymous boxes.
 */

import styles from "./skeleton.module.css";

export function SkeletonList({
  rows = 3,
  avatar = false,
  label,
}: {
  /** Roughly what usually arrives. Round down — see rule 1. */
  rows?: number;
  /** A leading circle, for lists whose rows carry an avatar or a status dot. */
  avatar?: boolean;
  /** What is being waited for, e.g. "Loading your chats". Announced once. */
  label: string;
}) {
  return (
    <div className={styles.list}>
      <span role="status" className={styles.srOnly}>
        {label}
      </span>
      {Array.from({ length: rows }, (_, index) => (
        <div className={styles.row} key={index} aria-hidden="true">
          {avatar && <span className={styles.avatar} />}
          {/* Uneven widths, because real rows are uneven. A column of
              identical bars reads as a table that failed to load. */}
          <span className={index % 3 === 0 ? styles.lineLong : styles.lineShort} />
        </div>
      ))}
    </div>
  );
}
