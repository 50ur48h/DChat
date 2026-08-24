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

/**
 * Where this bundle is running, as far as the *build* knew. `NEXT_PUBLIC_*` is
 * inlined at build time, so this is a property of the image, not of the request.
 */
function buildEnvironment(): string {
  return process.env.NEXT_PUBLIC_ENV ?? "prod";
}

/** Dev sign-in is only ever legitimate where a dev issuer could exist. */
export function devSignInIsPermitted(): boolean {
  const where = buildEnvironment();
  return where === "local" || where === "ci";
}

/**
 * **Defaults to `entra`, and that direction is the whole point.**
 *
 * This used to read `=== "entra" ? "entra" : "dev"`, so *any* value that was not
 * exactly `entra` — including the overwhelmingly common case of the variable not
 * being set at all — produced dev mode. A deploy that passed no build args
 * therefore shipped a public page reading "Development mode: the API mints a
 * token for whatever name you enter", with a name box, on the open internet.
 * The API was in `entra` mode and would have refused those tokens, so nothing
 * was actually mintable — but a page that offers it is not something to serve,
 * and "the other side refuses it" is the argument that eventually turns out to
 * be wrong.
 *
 * `config.py` had this right from Phase 2 and says so in as many words: *"the
 * default is 'entra' so that the weaker mode is always something someone
 * chose"*. The two halves of the product now agree.
 *
 * **And a default is not enough on its own.** Dev mode is additionally refused
 * unless the build says it is local or CI, so an explicit
 * `NEXT_PUBLIC_AUTH_MODE=dev` cannot reach a deployed environment by accident
 * either — the same belt-and-braces the API uses, where `AUTH_MODE=dev` is
 * refused in a prod build *and* the dev issuer is physically deleted from the
 * image.
 */
export function authConfig(): AuthConfig {
  const requested = process.env.NEXT_PUBLIC_AUTH_MODE;
  const mode: AuthMode = requested === "dev" && devSignInIsPermitted() ? "dev" : "entra";
  return {
    mode,
    authority: process.env.NEXT_PUBLIC_ENTRA_AUTHORITY ?? "",
    clientId: process.env.NEXT_PUBLIC_ENTRA_CLIENT_ID ?? "",
    apiScope: process.env.NEXT_PUBLIC_API_SCOPE ?? "",
  };
}

/**
 * Was dev sign-in asked for and refused? The screen says so rather than silently
 * showing an Entra button to somebody who expected a name box — a refusal that
 * cannot be seen is indistinguishable from a misconfiguration.
 */
export function devSignInWasRefused(): boolean {
  return process.env.NEXT_PUBLIC_AUTH_MODE === "dev" && !devSignInIsPermitted();
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
