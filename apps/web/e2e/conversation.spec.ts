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

import { ANSWER, CHART_SPEC, QUESTION, ids, startStubApi, type StubApi } from "./stub-api";

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
  // `exact`: the shell adds ancestors that also contain this word (WP13.1b).
  await expect(page.getByText("answered", { exact: true })).toBeVisible();
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

test("the trace shows how the answer was worked out", async ({ page }) => {
  await openConversation(page);
  await ask(page);
  await expect(page.getByText(ANSWER)).toBeVisible({ timeout: 15_000 });

  // Collapsed once the run is over — the answer is the point then — but still
  // there, because "how did you get that" is the question this product exists
  // to be able to answer.
  const toggle = page.getByRole("button", { name: /how this was worked out/ });
  await expect(toggle).toBeVisible();
  await toggle.click();

  await expect(page.getByText("Read the catalog")).toBeVisible();
  await expect(page.getByText("Wrote a query")).toBeVisible();
  await expect(page.getByText("Got results")).toBeVisible();
  // The machine name never reaches a person.
  await expect(page.getByText("query_executed")).toHaveCount(0);
});

test("a refresh mid-run replays the whole trace", async ({ page }) => {
  /**
   * The M8 gate's own criterion. The trace is not held in the page: every step
   * is a durable row, and a reload asks for them again. Nothing a reader has
   * seen can be lost by reloading, and nothing they missed stays missed.
   */
  await openConversation(page);
  await ask(page);
  await expect(page.getByText(ANSWER)).toBeVisible({ timeout: 15_000 });

  await page.reload();

  await expect(page.getByText(ANSWER)).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: /how this was worked out/ }).click();
  await expect(page.getByText("Read the catalog")).toBeVisible();
  await expect(page.getByText("Finished")).toBeVisible();
});


test("the chart is drawn in the browser, inside the answer", async ({ page }) => {
  /**
   * **The half of WP11.1 that only a real browser can prove.** jsdom has no
   * canvas, so every unit test here mocks `vega-embed` and asserts the card
   * *offers* a chart — which says nothing about whether one appears. This runs
   * the real renderer against a real spec.
   *
   * Inside the answer card, not beside it, is B-048's requirement: a chart
   * beside the answer is a picture next to some prose; a chart inside it is
   * part of the claim.
   */
  await openConversation(page);
  await ask(page);
  await expect(page.getByText(ANSWER)).toBeVisible({ timeout: 15_000 });

  const chart = page.getByTestId("chart");
  await expect(chart).toBeVisible({ timeout: 15_000 });
  // Vega renders these marks as SVG. Asserting on the marks rather than on the
  // container is the difference between "the page reserved room for a chart"
  // and "a chart was drawn" — the first version of this test looked for a
  // canvas, passed nothing, and would have shipped an empty box.
  await expect(chart.locator("svg.marks")).toBeVisible({ timeout: 15_000 });
  await expect(chart.locator("svg .mark-line, svg .mark-group")).not.toHaveCount(0);

  // **And it is inside the run's own card, not in a panel of its own** — B-048.
  // Anchored to the citation rather than to the answer sentence: the sentence
  // lives in the message bubble above, because the card suppresses it once the
  // thread has caught up (the Phase 7 gate found this card showing a citation
  // and no words). So "the same card as the evidence" is the honest form of
  // "inside the answer".
  const card = page.getByTestId("chart").locator("xpath=ancestor::section[1]");
  await expect(card.getByRole("button", { name: /Show the query behind this/ })).toBeVisible();
});

test("the chart spec opens, the way the query does", async ({ page }) => {
  /** B-048: a chart nobody can trace back to what produced it is decoration
   * that looks like evidence. */
  await openConversation(page);
  await ask(page);
  await expect(page.getByText(ANSWER)).toBeVisible({ timeout: 15_000 });

  await page.getByRole("button", { name: "Chart spec" }).click();

  await expect(page.getByText(`"mark": "${CHART_SPEC.mark}"`)).toBeVisible();
  // The values travel inline, and a reader can see that for themselves.
  await expect(page.getByText(/"order_count": 4125/)).toBeVisible();
});


test("drawing the chart reaches nothing outside the page", async ({ page }) => {
  /**
   * **The security claim of the whole chart design, in a real browser.**
   *
   * A spec is rendered in the reader's own browser, so an address inside one is
   * a request that browser makes — with a customer's aggregates in hand. The
   * server closes this by construction: it assembles the document itself from a
   * closed vocabulary and the result's own column names, so there is no field a
   * URL can arrive in. This is that promise, checked rather than asserted.
   */
  const offPage: string[] = [];
  const origin = new URL(page.url() || "http://localhost").origin;
  page.on("request", (request) => {
    const target = new URL(request.url());
    const own = target.host.includes("localhost") || target.host.includes("127.0.0.1");
    if (!own) offPage.push(request.url());
  });

  await openConversation(page);
  await ask(page);
  await expect(page.getByTestId("chart").locator("svg.marks")).toBeVisible({ timeout: 15_000 });

  expect(offPage, `the page fetched something off ${origin}: ${offPage.join(", ")}`).toEqual([]);
});
