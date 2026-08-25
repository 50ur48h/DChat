"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge, ROLE_TONES } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { Row, Stack } from "@/components/ui/page";
import { SkeletonList } from "@/components/ui/skeleton";
import {
  createApi,
  type ArmedRecoveryGrant,
  type Invitation,
  type Member,
  type RecoveryGrant,
} from "@/lib/api-client";
import { useSession } from "@/lib/auth/session";
import { personLabel } from "@/lib/identity";

import styles from "./members.module.css";

const ROLES = ["reader", "contributor", "admin"] as const;

function IssuedLink({ invitation }: { invitation: Invitation }) {
  // Computed during render, not in an effect: this panel only ever appears
  // after someone clicks Send invite, so window is always there by then.
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  const link = `${origin}/invitations/accept?token=${encodeURIComponent(invitation.token)}`;

  return (
    <div className={styles.token}>
      Invitation for {invitation.email} as {invitation.role}. Send them this link — the token is
      stored only as a hash, so it cannot be shown again.
      <code>{link}</code>
      <Row>
        <Button onClick={() => void navigator.clipboard.writeText(link)}>Copy link</Button>
      </Row>
    </div>
  );
}

export function Members({ orgId, role: myRole }: { orgId: string; role: string | null }) {
  const session = useSession();
  const api = useMemo(() => createApi(session.getToken), [session.getToken]);

  // B-008: a Reader was shown Change role, Remove and Send invite, earned a 403
  // from each, and learned that the product was broken rather than that they
  // lacked permission. Unknown role is treated as not-an-admin, so this fails
  // closed while `/v1/me` is still in flight.
  const isAdmin = myRole === "admin";

  const [members, setMembers] = useState<Member[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<string>("reader");
  const [issued, setIssued] = useState<Invitation | null>(null);
  const [busy, setBusy] = useState(false);
  //: **B-017.** Null while loading, so an empty panel is never mistaken for
  //: "this organization has no way back" — which is the one thing this feature
  //: exists to stop being true silently.
  const [grants, setGrants] = useState<RecoveryGrant[] | null>(null);
  const [grantLabel, setGrantLabel] = useState("");
  //: Shown exactly once, in the response to arming. There is no second chance
  //: to see it and the panel says so.
  const [armed, setArmed] = useState<ArmedRecoveryGrant | null>(null);

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

  // **B-017.** Loaded only for an Admin, because only an Admin may list them —
  // asking as a Reader would earn a 403 and put an error on a screen that is
  // working correctly, which is the shape B-008 was filed for.
  useEffect(() => {
    if (!isAdmin) return;
    let active = true;
    void (async () => {
      try {
        const next = await api.recoveryGrants(orgId);
        if (active) setGrants(next);
      } catch {
        // Left null: the members list is the point of this screen, and a
        // recovery panel that failed to load should not claim the page is broken.
        if (active) setGrants([]);
      }
    })();
    return () => {
      active = false;
    };
  }, [api, orgId, isAdmin]);

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
      <Card
        title="Members"
        subtitle={isAdmin ? undefined : "Only an Admin can change roles or invite people."}
      >
        {members === null && !error && (
          <SkeletonList rows={3} avatar label="Loading members" />
        )}
        {members && (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Person</th>
                <th>Role</th>
                {isAdmin && <th aria-label="Actions" />}
              </tr>
            </thead>
            <tbody>
              {members.map((member) => (
                <tr key={member.user_id}>
                  {/* A member whose token carried no email claim still has to be
                      identifiable, so fall back to their name and then to the
                      subject the product authorizes against (B-009). */}
                  <td>{personLabel(member, member.user_id)}</td>
                  <td>
                    <Badge tone={ROLE_TONES[member.role] ?? "neutral"}>{member.role}</Badge>
                  </td>
                  {isAdmin && (
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
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {error && <p className={styles.error}>{error}</p>}
      </Card>

      {isAdmin && (
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

          {issued && <IssuedLink invitation={issued} />}
        </Card>
      )}

      {isAdmin && (
        <Card
          title="If nobody can sign in"
          subtitle="Admins only. A way back into this organization, armed in advance."
        >
          {/* **B-017.** Roles change through an Admin-only route, so an
              organization whose Admins all lose their identities cannot invite,
              promote or register anything — and until this existed the only
              repair was editing the database. The grant is a bearer credential:
              whoever holds the token can make themselves an Admin here. */}
          <p className={styles.muted}>
            Arming a grant gives you a token to keep somewhere outside this product — a password
            manager, not an inbox. If every Admin here ever loses access, whoever holds it can
            claim Admin of this organization. It is shown once, can be used once, and you can
            revoke it at any time.
          </p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void run(async () => {
                setArmed(await api.armRecoveryGrant(orgId, grantLabel.trim()));
                setGrantLabel("");
                setGrants(await api.recoveryGrants(orgId));
              });
            }}
          >
            <Row>
              <Input
                label="What it is for"
                value={grantLabel}
                onChange={(event) => setGrantLabel(event.target.value)}
                placeholder="Ops password manager"
              />
              <Button variant="primary" type="submit" disabled={busy}>
                {busy ? "Arming…" : "Arm a recovery grant"}
              </Button>
            </Row>
          </form>

          {armed && (
            <div className={styles.token}>
              Recovery token for “{armed.label}”, valid until{" "}
              {new Date(armed.expires_at).toLocaleDateString()}. Copy it now — only its hash is
              stored, so it cannot be shown again.
              <code>{armed.token}</code>
              <Row>
                <Button onClick={() => void navigator.clipboard.writeText(armed.token)}>
                  Copy token
                </Button>
              </Row>
            </div>
          )}

          {grants === null ? (
            <SkeletonList rows={2} label="Loading invitations" />
          ) : grants.length === 0 ? (
            <p className={styles.muted}>
              Nothing armed. This organization has no way back if its Admins lose access.
            </p>
          ) : (
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>What it is for</th>
                  <th>State</th>
                  <th>Valid until</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {grants.map((grant) => (
                  <tr key={grant.id}>
                    <td>{grant.label}</td>
                    <td>
                      <Badge tone={grant.state === "armed" ? "mint" : "neutral"}>
                        {grant.state}
                      </Badge>
                    </td>
                    <td>{new Date(grant.expires_at).toLocaleDateString()}</td>
                    <td>
                      {grant.state === "armed" && (
                        <Button
                          variant="danger"
                          disabled={busy}
                          onClick={() =>
                            void run(async () => {
                              await api.revokeRecoveryGrant(orgId, grant.id);
                              setGrants(await api.recoveryGrants(orgId));
                            })
                          }
                        >
                          Revoke
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}
    </Stack>
  );
}
