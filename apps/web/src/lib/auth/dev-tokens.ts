/**
 * Development sign-in: ask the API's dev issuer for a token.
 *
 * The API only mounts `/dev/token` when it is running with `AUTH_MODE=dev`, and
 * its production image does not contain the module at all (WP2.1a). So this path
 * cannot be reached against a real deployment even if the web app is misbuilt.
 */

import { apiBaseUrl } from "@/lib/api-client";

const STORAGE_KEY = "dataagent.dev-subject";

export interface DevIdentity {
  subject: string;
  token: string;
}

export async function mintDevToken(subject: string): Promise<DevIdentity> {
  const url = new URL(`${apiBaseUrl()}/dev/token`);
  url.searchParams.set("sub", subject);
  url.searchParams.set("email", `${subject}@example.com`);

  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(
      "The API did not issue a development token. Is it running with AUTH_MODE=dev?",
    );
  }

  const body: unknown = await response.json();
  const token = (body as { access_token?: unknown }).access_token;
  if (typeof token !== "string") {
    throw new Error("The development token response was not in the expected shape");
  }
  return { subject, token };
}

export function rememberDevSubject(subject: string): void {
  window.localStorage.setItem(STORAGE_KEY, subject);
}

export function forgetDevSubject(): void {
  window.localStorage.removeItem(STORAGE_KEY);
}

export function rememberedDevSubject(): string | null {
  return window.localStorage.getItem(STORAGE_KEY);
}
