"use client";

/**
 * Everything that is not chat (WP13.1b).
 *
 * The admin screens moved here rather than onto the org's front page, because
 * the front page is now the chat. Nothing about who may do what changed: each
 * destination gates itself, `useOrgRole` fails closed, and the API refuses and
 * audits regardless of what this renders (B-008).
 *
 * **The unbuilt sections are shown as unbuilt.** System instructions, tone and
 * preferences are named with a "Coming soon" badge and no controls at all —
 * deliberately not a disabled select or a dead toggle. A control that looks
 * operable and does nothing is a promise the product does not keep, which is the
 * same defect as a badge reading *answered* on a refusal (B-133): the interface
 * asserting something the system cannot back. An honest gap is cheaper than a
 * convincing lie, and these are coming next.
 */

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ArchivedChats } from "@/components/screens/archived-chats";
import { Badge, ROLE_TONES } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Row, Stack } from "@/components/ui/page";
import { createApi, type ActiveDataSource, type Me } from "@/lib/api-client";
import { useSession } from "@/lib/auth/session";
import { emailIsUnknown, personLabel } from "@/lib/identity";
import { useTheme } from "@/lib/theme";
import { useOrgRole } from "@/lib/use-org-role";

import styles from "./settings.module.css";

/** A section that exists in the plan and not yet in the product. */
function ComingSoon({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card
      title={title}
      action={
        <Badge tone="peach">Coming soon</Badge>
      }
    >
      <p className={styles.muted}>{children}</p>
    </Card>
  );
}

export function Settings({ orgId }: { orgId: string }) {
  const session = useSession();
  const api = useMemo(() => createApi(session.getToken), [session.getToken]);
  const { role } = useOrgRole(orgId);
  const { theme, setTheme } = useTheme();

  const [me, setMe] = useState<Me | null>(null);
  const [active, setActive] = useState<ActiveDataSource | null>(null);
  const [error, setError] = useState<string | null>(null);

  const isAdmin = role === "admin";
  const canWriteDefinitions = isAdmin || role === "contributor";

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const [who, chosen] = await Promise.all([api.me(), api.activeDataSource(orgId)]);
        if (!alive) return;
        setMe(who);
        setActive(chosen);
      } catch (cause) {
        if (alive) setError(cause instanceof Error ? cause.message : "Settings could not load.");
      }
    })();
    return () => {
      alive = false;
    };
  }, [api, orgId]);

  const membership = me?.memberships.find((entry) => entry.org_id === orgId) ?? null;

  const chooseTheme = useCallback(
    (next: "light" | "dark") => () => setTheme(next),
    [setTheme],
  );

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
          <dl className={styles.facts}>
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
          <p className={styles.muted}>Loading…</p>
        )}
        {error && <p className={styles.error}>{error}</p>}
      </Card>

      <Card title="Organization" subtitle="What this organization is, and what it answers from.">
        <dl className={styles.facts}>
          <dt>Name</dt>
          <dd>{membership?.org_name ?? "—"}</dd>
          <dt>Your role</dt>
          <dd>
            {membership ? (
              <Badge tone={ROLE_TONES[membership.role] ?? "neutral"}>{membership.role}</Badge>
            ) : (
              "—"
            )}
          </dd>
          <dt>Answers from</dt>
          <dd>
            {active === null
              ? "—"
              : (active.data_source_name ??
                (isAdmin
                  ? "No database chosen yet — choose one under Data sources."
                  : "No database chosen yet. An Admin can choose one."))}
          </dd>
        </dl>
        <Row>
          {/* Every destination gates itself; these links decide what to render,
              not what is allowed. A Reader sees the two that are theirs. */}
          <Link href={`/orgs/${orgId}/settings/members`}>
            <Button>Members</Button>
          </Link>
          {isAdmin && (
            <Link href={`/orgs/${orgId}/data-sources`}>
              <Button>Data sources</Button>
            </Link>
          )}
          {canWriteDefinitions && (
            <Link href={`/orgs/${orgId}/documents`}>
              <Button>Documents</Button>
            </Link>
          )}
        </Row>
      </Card>

      <Card title="Appearance" subtitle="Light is the default. Your choice is kept in this browser.">
        <Row>
          <Button
            variant={theme === "light" ? "primary" : "secondary"}
            onClick={chooseTheme("light")}
            aria-pressed={theme === "light"}
          >
            Light
          </Button>
          <Button
            variant={theme === "dark" ? "primary" : "secondary"}
            onClick={chooseTheme("dark")}
            aria-pressed={theme === "dark"}
          >
            Dark
          </Button>
        </Row>
      </Card>

      <ArchivedChats orgId={orgId} />

      <ComingSoon title="System instructions">
        Standing guidance every question in this organization is answered with — house
        definitions, what to avoid, which figures matter. Not built yet, so nothing here is
        being sent to the model.
      </ComingSoon>

      <ComingSoon title="Tone">
        How an answer is written: brief or thorough, plain or technical. Not built yet — answers
        are composed the one way today.
      </ComingSoon>

      <ComingSoon title="Preferences">
        Per-person settings such as a default currency and date format. Not built yet.
      </ComingSoon>
    </Stack>
  );
}
