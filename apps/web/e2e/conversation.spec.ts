/**
 * The Phase 7 gate, in a real browser (**B-044**).
 *
 * This file exists because a jsdom test was not enough and said so loudly: the
 * first regression test written for B-044 **passed against the broken code**,
 * and the second only failed once its stubbed `fetch` was given artificial
 * latency. Both are guesses about how React's commit interleaves with a network
 * round trip. This runs the real page in real Chromium against a real HTTP
 * server, so nothing has to be guessed.
 *
 * The property under test is the one the gate failed on twice:
 *
 *   **After a single question, with no further interaction, the answer text is
 *   on the screen.**
 *
 * No second message, no click, no refresh. `POST …/messages` answers 202 with a
 * run id and no answer, so the screen has to notice the run finishing and
 * re-read the thread by itself. When it did not, every reply rendered one
 * message behind and an answer arrived as a confidence badge with no words.
 */

import { expect, test } from "@playwright/test";

import { ANSWER, QUESTION, ids, startStubApi, type StubApi } from "./stub-api";

let api: StubApi;

test.beforeAll(async () => {
  api = await startStubApi();
});

test.beforeEach(() => {
  api.reset();
});

test.afterAll(async () => {
  await api.close();
});

/**
 * Sign in the way `lib/auth/dev-tokens.ts` does: remember a subject, and let
 * `SessionProvider` mint a token for it on the next load.
 *
 * The origin has to exist before `localStorage` can be written to — an init
 * script running against `about:blank` writes somewhere nobody reads, which is
 * how the first version of this helper silently left every test at the sign-in
 * screen.
 */
async function signIn(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.evaluate(() => window.localStorage.setItem("dataagent.dev-subject", "tester"));
}

async function openConversation(page: import("@playwright/test").Page) {
  await signIn(page);
  await page.goto(`/orgs/${ids.org}/conversations/${ids.conversation}`);
  await expect(page.getByLabel("Ask a question")).toBeVisible();
}

async function ask(page: import("@playwright/test").Page) {
  await page.getByLabel("Ask a question").fill(QUESTION);
  await page.getByRole("button", { name: "Send" }).click();
}

test("the answer appears after one question, with no further interaction", async ({ page }) => {
  await openConversation(page);
  await ask(page);

  // The question is accepted immediately and the run is watched.
  await expect(page.getByText("working")).toBeVisible();

  // And then — with nothing else touched — the answer arrives.
  await expect(page.getByText(ANSWER)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("answered")).toBeVisible();
});

test("the citation opens into the query behind the answer", async ({ page }) => {
  await openConversation(page);
  await ask(page);
  await expect(page.getByText(ANSWER)).toBeVisible({ timeout: 15_000 });

  await page.getByRole("button", { name: /Show the query behind this/ }).click();

  await expect(page.getByText(/SELECT COUNT\(\*\) AS "order_count"/)).toBeVisible();
  await expect(page.getByRole("cell", { name: "3718" })).toBeVisible();
});

test("the answer is shown once, not twice", async ({ page }) => {
  await openConversation(page);
  await ask(page);
  await expect(page.getByText(ANSWER)).toBeVisible({ timeout: 15_000 });

  // In single-shot the finding statement *is* the answer, so the card must not
  // restate what the thread already says.
  await expect(page.getByText(ANSWER)).toHaveCount(1);
});

test("polling stops once the run has finished", async ({ page }) => {
  await openConversation(page);
  await ask(page);
  await expect(page.getByText(ANSWER)).toBeVisible({ timeout: 15_000 });

  const settled = api.calls.filter((call) => call.includes("/runs/")).length;
  await page.waitForTimeout(3_000);

  // A page left open must not ask forever.
  expect(api.calls.filter((call) => call.includes("/runs/")).length).toBe(settled);
});
