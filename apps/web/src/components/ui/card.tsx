import type { ReactNode } from "react";

import styles from "./card.module.css";

interface CardProps {
  children: ReactNode;
  title?: string | undefined;
  subtitle?: string | undefined;
  action?: ReactNode | undefined;
  tone?: "raised" | "sunken" | undefined;
}

/** Surface, radius and a soft shadow — no border. See docs/design.md. */
export function Card({ children, title, subtitle, action, tone = "raised" }: CardProps) {
  return (
    <section className={`${styles.card} ${tone === "sunken" ? styles.sunken : ""}`}>
      {(title || action) && (
        <header className={styles.header}>
          <div>
            {title && <h2 className={styles.title}>{title}</h2>}
            {subtitle && <p className={styles.subtitle}>{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}
