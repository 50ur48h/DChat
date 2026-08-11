"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useSession } from "@/lib/auth/session";

import styles from "./sign-in.module.css";

export function SignIn() {
  const session = useSession();
  const [subject, setSubject] = useState("alice");

  if (session.problems.length > 0) {
    return (
      <Card title="Sign-in is not configured">
        <p className={styles.problems}>
          Set {session.problems.join(", ")} and rebuild the web app. Entra sign-in cannot start
          without them.
        </p>
      </Card>
    );
  }

  if (session.mode === "entra") {
    return (
      <Card title="Sign in" subtitle="You will be redirected to your organization's sign-in page.">
        <Button variant="primary" onClick={() => void session.signIn()} disabled={session.busy}>
          {session.busy ? "Starting…" : "Sign in"}
        </Button>
        {session.error && <p className={styles.error}>{session.error}</p>}
      </Card>
    );
  }

  return (
    <Card
      title="Sign in"
      subtitle="Development mode: the API mints a token for whatever name you enter."
    >
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          void session.signIn(subject);
        }}
      >
        <Input
          label="Who are you"
          value={subject}
          onChange={(event) => setSubject(event.target.value)}
          placeholder="alice"
          autoComplete="off"
        />
        <Button variant="primary" type="submit" disabled={session.busy || !subject.trim()}>
          {session.busy ? "Signing in…" : "Continue"}
        </Button>
        {session.error && <p className={styles.error}>{session.error}</p>}
      </form>
    </Card>
  );
}
