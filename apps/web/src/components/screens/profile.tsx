"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge, ROLE_TONES } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Row, Stack } from "@/components/ui/page";
import { createApi, type Me } from "@/lib/api-client";
import { useSession } from "@/lib/auth/session";
import { emailIsUnknown, personLabel } from "@/lib/identity";

import styles from "./profile.module.css";

export function Profile() {
  const session = useSession();
  const api = useMemo(() => createApi(session.getToken), [session.getToken]);

  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [orgName, setOrgName] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setMe(await api.me());
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load your profile");
    }
  }, [api]);

  useEffect(() => {
    // Guarded so a slow response cannot write into an unmounted screen.
    let active = true;
    void (async () => {
      try {
        const next = await api.me();
        if (active) setMe(next);
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "Could not load");
      }
    })();
    return () => {
      active = false;
    };
  }, [api]);

  const create = async () => {
    setBusy(true);
    try {
      await api.createOrg(orgName.trim());
      setOrgName("");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not create the organization");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Stack>
      <Card
        title="You"
        action={
          <Button onClick={() => void session.signOut()} variant="ghost">
            Sign out
          </Button>
        }
      >
        {me ? (
          <dl className={styles.identity}>
            <dt>Signed in as</dt>
            <dd>
              {personLabel(me, me.subject)}
              {emailIsUnknown(me) && (
                <span className={styles.note}>
                  Your sign-in did not include an email address, so we do not have one.
                </span>
              )}
            </dd>
            <dt>Subject</dt>
            <dd>
              <code>{me.subject}</code>
            </dd>
          </dl>
        ) : (
          <p className={styles.empty}>Loading…</p>
        )}
        {error && <p className={styles.error}>{error}</p>}
      </Card>

      <Card
        title="Your organizations"
        subtitle={
          me && me.memberships.length === 0
            ? "You do not belong to one yet. Create one, or ask an admin to invite you."
            : undefined
        }
      >
        {me && me.memberships.length > 0 ? (
          <div className={styles.orgs}>
            {me.memberships.map((membership) => (
              <div className={styles.org} key={membership.org_id}>
                <span className={styles.orgName}>{membership.org_name}</span>
                <Row>
                  <Badge tone={ROLE_TONES[membership.role] ?? "neutral"}>{membership.role}</Badge>
                  <Link href={`/orgs/${membership.org_id}`}>
                    <Button>Members</Button>
                  </Link>
                </Row>
              </div>
            ))}
          </div>
        ) : (
          me && <p className={styles.empty}>Nothing here yet.</p>
        )}
      </Card>

      <Card title="Create an organization" subtitle="You become its first Admin.">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void create();
          }}
        >
          <Row>
            <Input
              label="Name"
              value={orgName}
              onChange={(event) => setOrgName(event.target.value)}
              placeholder="Acme"
            />
            <Button variant="primary" type="submit" disabled={busy || !orgName.trim()}>
              {busy ? "Creating…" : "Create"}
            </Button>
          </Row>
        </form>
      </Card>
    </Stack>
  );
}
