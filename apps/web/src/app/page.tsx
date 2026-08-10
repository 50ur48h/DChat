import { ApiHealth } from "@/components/api-health";

import styles from "./page.module.css";

export default function Home() {
  return (
    <main className={styles.main}>
      <header className={styles.header}>
        <h1 className={styles.title}>data-agent</h1>
        <p className={styles.subtitle}>
          AI-native data analysis. Phase 0 — walking skeleton: this page exists to prove the browser
          can reach the API.
        </p>
      </header>

      <ApiHealth />
    </main>
  );
}
