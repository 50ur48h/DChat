/**
 * Runtime auth configuration.
 *
 * `NEXT_PUBLIC_*` values are inlined into the browser bundle at build time, so
 * only public identifiers belong here: a tenant id and a client id are public,
 * a client secret would be published. The SPA uses auth-code + PKCE and has no
 * secret at all (architecture Part 6.1).
 */

export type AuthMode = "dev" | "entra";

export interface AuthConfig {
  mode: AuthMode;
  /** Entra only: the authority MSAL signs in against. */
  authority: string;
  clientId: string;
  /** The scope that yields an access token our API will accept. */
  apiScope: string;
}

export function authConfig(): AuthConfig {
  const mode: AuthMode = process.env.NEXT_PUBLIC_AUTH_MODE === "entra" ? "entra" : "dev";
  return {
    mode,
    authority: process.env.NEXT_PUBLIC_ENTRA_AUTHORITY ?? "",
    clientId: process.env.NEXT_PUBLIC_ENTRA_CLIENT_ID ?? "",
    apiScope: process.env.NEXT_PUBLIC_API_SCOPE ?? "",
  };
}

/** Entra cannot work without all three; say so rather than failing at redirect. */
export function missingEntraSettings(config: AuthConfig): string[] {
  if (config.mode !== "entra") return [];
  return (
    [
      ["NEXT_PUBLIC_ENTRA_AUTHORITY", config.authority],
      ["NEXT_PUBLIC_ENTRA_CLIENT_ID", config.clientId],
      ["NEXT_PUBLIC_API_SCOPE", config.apiScope],
    ] as const
  )
    .filter(([, value]) => value.length === 0)
    .map(([name]) => name);
}
