"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge, ROLE_TONES } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { Row, Stack } from "@/components/ui/page";
import { createApi, type Invitation, type Member } from "@/lib/api-client";
import { useSession } from "@/lib/auth/session";

import styles from "./members.module.css";

const ROLES = ["reader", "contributor", "admin"] as const;

export function Members({ orgId }: { orgId: string }) {
  const session = useSession();
  const api = useMemo(() => createApi(session.getToken), [session.getToken]);

  const [members, setMembers] = useState<Member[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<string>("reader");
  const [issued, setIssued] = useState<Invitation | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setMembers(await api.members(orgId));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load members");
    }
  }, [api, orgId]);

  useEffect(() => {
    // Guarded so a slow response cannot write into an unmounted screen.
    let active = true;
    void (async () => {
      try {
        const next = await api.members(orgId);
        if (active) setMembers(next);
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "Could not load");
      }
    })();
    return () => {
      active = false;
    };
  }, [api, orgId]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      await load();
    } catch (cause) {
      // The API's own words: "Your role does not permit this action" and
      // "This is the only Admin…" are more use than a status code.
      setError(cause instanceof Error ? cause.message : "That did not work");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Stack>
      <Card title="Members">
        {members === null && !error && <p className={styles.muted}>Loading…</p>}
        {members && (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Person</th>
                <th>Role</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {members.map((member) => (
                <tr key={member.user_id}>
                  <td>{member.email}</td>
                  <td>
                    <Badge tone={ROLE_TONES[member.role] ?? "neutral"}>{member.role}</Badge>
                  </td>
                  <td>
                    <div className={styles.actions}>
                      <Select
                        label="Change role"
                        options={ROLES}
                        value={member.role}
                        disabled={busy}
                        onChange={(event) =>
                          void run(() => api.changeRole(orgId, member.user_id, event.target.value))
                        }
                      />
                      <Button
                        variant="danger"
                        disabled={busy}
                        onClick={() => void run(() => api.removeMember(orgId, member.user_id))}
                      >
                        Remove
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {error && <p className={styles.error}>{error}</p>}
      </Card>

      <Card title="Invite someone" subtitle="Admins only. The link is shown once.">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void run(async () => {
              setIssued(await api.invite(orgId, email.trim(), role));
              setEmail("");
            });
          }}
        >
          <Row>
            <Input
              label="Email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="bob@example.com"
            />
            <Select
              label="Role"
              options={ROLES}
              value={role}
              onChange={(event) => setRole(event.target.value)}
            />
            <Button variant="primary" type="submit" disabled={busy || !email.trim()}>
              {busy ? "Inviting…" : "Send invite"}
            </Button>
          </Row>
        </form>

        {issued && (
          <div className={styles.token}>
            Invitation for {issued.email} as {issued.role}. Copy this token now — it is stored only
            as a hash and cannot be shown again.
            <code>{issued.token}</code>
          </div>
        )}
      </Card>
    </Stack>
  );
}
