import { describe, expect, it } from "vitest";

import { emailIsUnknown, personLabel } from "./identity";

describe("personLabel", () => {
  it("prefers the email when the provider sent one", () => {
    expect(personLabel({ email: "alice@example.com", name: "Alice" })).toBe("alice@example.com");
  });

  it("falls back to the name when no email claim arrived", () => {
    expect(personLabel({ email: null, name: "Alice" })).toBe("Alice");
  });

  it("falls back again to whatever the caller can always supply", () => {
    // The subject, in practice: unfriendly, but present and authoritative.
    expect(personLabel({ email: null, name: null }, "sub-123")).toBe("sub-123");
  });

  it("never invents an address", () => {
    expect(personLabel({ email: null, name: null })).not.toContain("@");
  });
});

describe("emailIsUnknown", () => {
  it("is true only when the email is genuinely absent", () => {
    expect(emailIsUnknown({ email: null, name: "Alice" })).toBe(true);
    expect(emailIsUnknown({ email: "alice@example.com", name: null })).toBe(false);
  });
});
