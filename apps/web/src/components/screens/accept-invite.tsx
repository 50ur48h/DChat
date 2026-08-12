"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { Badge, ROLE_TONES } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Row } from "@/components/ui/page";
import { createApi } from "@/lib/api-client";
import { useSession } from "@/lib/auth/session";

import styles from "./accept-invite.module.css";

interface Joined {
  orgId: string;
  orgName: string;
  role: string;
}

export function AcceptInvite() {
  const session = useSession();
  const api = useMemo(() => createApi(session.getToken), [session.getToken]);
  const params = useSearchParams();

  // Seeded from the link on first render rather than in an effect: the value
  // is known immediately, and setting it afterwards would re-render for nothing.
  const [token, setToken] = useState(() => params.get("token") ?? "");
  const [joined, setJoined] = useState<Joined | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const accept = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.acceptInvitation(token.trim());
      setJoined({ orgId: result.org_id, orgName: result.org_name, role: result.role });
    } catch (cause) {
      // The API answers every bad token identically on purpose — unknown,
      // expired and already-used are indistinguishable, or this becomes an
      // oracle for guessing tokens. Pass its wording straight through.
      setError(cause instanceof Error ? cause.message : "That invitation could not be accepted");
    } finally {
      setBusy(false);
    }
  };

  if (joined) {
    return (
      <Card title="You're in" subtitle={`You joined ${joined.orgName}.`}>
        <Row>
          <Badge tone={ROLE_TONES[joined.role] ?? "neutral"}>{joined.role}</Badge>
          <Link href={`/orgs/${joined.orgId}`}>
            <Button variant="primary">Go to {joined.orgName}</Button>
          </Link>
          <Link href="/">
            <Button>Home</Button>
          </Link>
        </Row>
      </Card>
    );
  }

  return (
    <Card
      title="Accept an invitation"
      subtitle="Paste the invitation token you were sent, or follow the link from your invite."
    >
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          void accept();
        }}
      >
        <Input
          label="Invitation token"
          className={styles.token}
          value={token}
          onChange={(event) => setToken(event.target.value)}
          placeholder="Paste your token"
          autoComplete="off"
        />
        <Row>
          <Button variant="primary" type="submit" disabled={busy || !token.trim()}>
            {busy ? "Joining…" : "Join"}
          </Button>
        </Row>
        {error && <p className={styles.error}>{error}</p>}
        <p className={styles.hint}>
          Signed in as {session.who}. An invitation is tied to an organization, not to an email
          address, so it will add whoever redeems it.
        </p>
      </form>
    </Card>
  );
}
