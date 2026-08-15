/**
 * Browser tests for the screens the unit tests cannot honestly cover.
 *
 * Added in WP7.3b rather than in Phase 11 (plan WP11.2), because **B-044**
 * showed that a jsdom test can pass against code a browser visibly breaks on:
 * the bug was a race between a `setState` and the `await` after it, and a
 * stubbed `fetch` that resolves in a microtask reorders exactly the two things
 * under test. The Playwright smoke Phase 11 plans is the wider one over every
 * screen; this is the narrow one that holds the gate's own property.
 *
 * Deliberately hermetic. The dev server points at a stub API on a fixed local
 * port, so these run in CI with no compose stack, no database and no key — and
 * a developer's running containers on 3000/8000 are untouched.
 *
 * Serial on purpose: the stub binds one fixed port, because `NEXT_PUBLIC_API_URL`
 * is inlined when the server starts and cannot vary per worker.
 */

import { defineConfig, devices } from "@playwright/test";

import { STUB_PORT } from "./e2e/stub-api";

const PORT = 3100;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "list" : [["list"]],
  // `localhost`, not `127.0.0.1`: Next dev blocks cross-origin requests to
  // dev-only assets, trusting the hostname it was started with. From
  // 127.0.0.1 every client chunk comes back 403 and the page never hydrates,
  // which looks exactly like the app being broken. Matching the origin is
  // better than widening `allowedDevOrigins` in the shipped config.
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  // A real browser against a real server: generous enough that a slow machine
  // does not read as a product failure.
  expect: { timeout: 20_000 },
  // Built once and served, not `next dev`. Turbopack compiles a route on first
  // request, and on Windows that occasionally takes longer than any sensible
  // assertion timeout — which shows up as a page that "never signs in" and has
  // nothing to do with the code under test. A production bundle is also what a
  // user actually gets, so the smoke is more faithful for the same money.
  webServer: {
    command: `pnpm exec next build && pnpm exec next start --port ${PORT}`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 300_000,
    env: {
      // Development sign-in, so a real browser holds a real session without an
      // identity provider (`lib/auth/dev-tokens.ts`).
      NEXT_PUBLIC_AUTH_MODE: "dev",
      NEXT_PUBLIC_API_URL: `http://127.0.0.1:${STUB_PORT}`,
    },
  },
});
