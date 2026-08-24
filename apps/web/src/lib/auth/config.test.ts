import { beforeEach, describe, expect, it, vi } from "vitest";

import { authConfig, devSignInWasRefused, missingEntraSettings } from "./config";

describe("authConfig", () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
  });

  it("defaults to entra when nothing is configured", () => {
    // **This test asserted the opposite and the opposite was the defect.** With
    // no configuration the mode was `dev`, so a deploy that passed no build args
    // shipped a public page offering to mint a token for any name typed into it.
    // `config.py` has defaulted the other way since Phase 2 for this reason: the
    // weaker mode must always be something someone chose.
    expect(authConfig().mode).toBe("entra");
  });

  it("honours dev mode where a dev issuer could exist", () => {
    vi.stubEnv("NEXT_PUBLIC_ENV", "local");
    vi.stubEnv("NEXT_PUBLIC_AUTH_MODE", "dev");

    expect(authConfig().mode).toBe("dev");
  });

  it("refuses dev mode in a deployed environment even when asked explicitly", () => {
    // The belt to the default's braces: an explicit NEXT_PUBLIC_AUTH_MODE=dev in
    // a dev/prod build is somebody's mistake, and it must not reach a browser.
    vi.stubEnv("NEXT_PUBLIC_ENV", "dev");
    vi.stubEnv("NEXT_PUBLIC_AUTH_MODE", "dev");

    expect(authConfig().mode).toBe("entra");
    expect(devSignInWasRefused()).toBe(true);
  });

  it("says nothing about a refusal that did not happen", () => {
    vi.stubEnv("NEXT_PUBLIC_ENV", "local");
    vi.stubEnv("NEXT_PUBLIC_AUTH_MODE", "dev");

    expect(devSignInWasRefused()).toBe(false);
  });

  it("names every missing Entra setting instead of failing at the redirect", () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_MODE", "entra");

    expect(missingEntraSettings(authConfig())).toEqual([
      "NEXT_PUBLIC_ENTRA_AUTHORITY",
      "NEXT_PUBLIC_ENTRA_CLIENT_ID",
      "NEXT_PUBLIC_API_SCOPE",
    ]);
  });

  it("is satisfied once all three are present", () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_MODE", "entra");
    vi.stubEnv("NEXT_PUBLIC_ENTRA_AUTHORITY", "https://x.ciamlogin.com/tenant");
    vi.stubEnv("NEXT_PUBLIC_ENTRA_CLIENT_ID", "client");
    vi.stubEnv("NEXT_PUBLIC_API_SCOPE", "api://x/access_as_user");

    expect(missingEntraSettings(authConfig())).toEqual([]);
  });

  it("dev mode never reports Entra problems", () => {
    vi.stubEnv("NEXT_PUBLIC_ENV", "local");
    vi.stubEnv("NEXT_PUBLIC_AUTH_MODE", "dev");

    expect(missingEntraSettings(authConfig())).toEqual([]);
  });
});
