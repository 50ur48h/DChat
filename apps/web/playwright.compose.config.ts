/**
 * The Phase 11 gate's smoke: a browser driving the **real** product (plan WP11.2b).
 *
 * The sibling `playwright.config.ts` is hermetic — a built page against a stub
 * HTTP server, no database, no key. This one is its opposite and exists for the
 * half that one cannot reach: a real API, a real platform database with row-level
 * security, a real DAL validating real SQL, and a real customer database with
 * 71,798 seeded orders in it. Nothing here is stubbed **except the model**, and
 * `ops/scripts/web_smoke.sh` is what guarantees that — it brings the stack up with
 * `LLM_PROVIDERS=scripted`, so this file never has to hope.
 *
 * **What a green run here proves, and what it does not.** It proves the stack
 * wires up end to end: sign-in mints a token the API accepts, an organization and
 * a data source are created through the product, the catalog is discovered and
 * profiled, a question starts a run, the run's SQL reaches the customer database
 * and comes back, and the answer card and its trace render what happened. It
 * proves **nothing** about whether a question was understood — the model is a
 * script. Plan WP11.2's gate wording says exactly this, and the chart criterion
 * is met by a live walk against a real model instead. A gate signed off on a
 * canned answer would be B-087's failure at the level of the gate itself.
 *
 * No `webServer`: the server is compose, and it is already up by the time this
 * runs. Pointed by `SMOKE_BASE_URL`, which the script sets to the smoke stack's
 * own port so a developer's stack on :3000 is neither used nor disturbed.
 */

import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e-compose",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  // **No retries, deliberately**, where the hermetic suite takes one. A retry
  // there re-runs a pure function of the code; a retry here re-runs a walk that
  // has already written rows, and a second attempt that passes because the first
  // one registered the data source would report the stack as working when the
  // path through it is broken. The walk is idempotent so a developer can re-run
  // it by hand, not so CI can hide a failure inside it.
  retries: 0,
  reporter: "list",
  use: {
    baseURL: process.env.SMOKE_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    video: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  // Generous, and every second of it is real work rather than slack. `next dev`
  // in the compose image compiles each route on its first request; discovering
  // and profiling a schema opens a connection to another database and reads it;
  // and an agent run — even with a scripted model — validates SQL, executes it,
  // stores an artifact and writes a dozen durable events. A tight timeout here
  // would report "the product is broken" every time a runner was busy.
  expect: { timeout: 30_000 },
  timeout: 5 * 60_000,
});
