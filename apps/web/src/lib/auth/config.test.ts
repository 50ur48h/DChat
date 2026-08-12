import { beforeEach, describe, expect, it, vi } from "vitest";

import { authConfig, missingEntraSettings } from "./config";

describe("authConfig", () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
  });

  it("defaults to dev mode when nothing is configured", () => {
    expect(authConfig().mode).toBe("dev");
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
    expect(missingEntraSettings(authConfig())).toEqual([]);
  });
});
