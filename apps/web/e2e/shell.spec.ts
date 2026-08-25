/**
 * The chat shell, in a real browser (WP13.1b).
 *
 * `conversation.spec.ts` beside this holds the Phase 7 property — an answer
 * arrives with no further interaction. This holds the property WP13.1b adds:
 * **you can open the app and start talking, and the frame around that behaves.**
 *
 * The unit tests cover each piece against a stubbed `fetch`. What they cannot
 * cover is the thing B-044 was: a screen that works in jsdom and does not work
 * in a browser. The composer-to-answer path here crosses a route change, which
 * jsdom has no opinion about at all.
 */

import { expect, test, type Page } from "@playwright/test";

import { ANSWER, ids, startStubApi, type StubApi } from "./stub-api";

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

async function signIn(page: Page) {
  await page.goto("/");
  await page.evaluate(() => window.localStorage.setItem("dataagent.dev-subject", "tester"));
}

test("a member lands in chat, not on an organization page", async ({ page }) => {
  await signIn(page);
  await page.goto("/");

  // The front door redirects into the first organization's chat. Before this
  // work package it stopped at a profile with an Ask button on it.
  await expect(page).toHaveURL(new RegExp(`/orgs/${ids.org}/conversations$`));
  // `.first()`, because a client-side redirect briefly has both the page it is
  // leaving and the one it is arriving at in the DOM, and strict mode treats
  // that transient pair as an error rather than retrying past it.
  await expect(
    page.getByRole("heading", { name: "What would you like to know?" }).first(),
  ).toBeVisible();
});

test("asking from the empty chat creates the thread and answers", async ({ page }) => {
  /**
   * **The reachability proof for the new home, end to end.** One composer, one
   * send, and the answer appears — across a route change the unit tests cannot
   * exercise. If the create/ask/navigate sequence breaks anywhere, this is red.
   */
  await signIn(page);
  await page.goto(`/orgs/${ids.org}/conversations`);

  const box = page.getByLabel("Your question");
  await expect(box).toBeEnabled();
  await box.fill("How many orders were placed in July 2026?");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page).toHaveURL(new RegExp(`/conversations/${ids.conversation}$`));
  await expect(page.getByText(ANSWER)).toBeVisible({ timeout: 20_000 });
});

test("the sidebar collapses, expands, and remembers across a reload", async ({ page }) => {
  await signIn(page);
  await page.goto(`/orgs/${ids.org}/conversations`);

  // The chat list is what the collapsed rail gives up — asserted on rather than
  // on "New chat", which the collapsed rail keeps as an icon with that very
  // `aria-label`, so a name-based locator matches in both states.
  const thread = page.getByRole("link", { name: "How many orders were placed in July 2026?" });
  await expect(thread).toBeVisible();

  await page.getByRole("button", { name: "Collapse the sidebar" }).click();
  await expect(thread).toBeHidden();

  await page.reload();

  // The point of the test: a real reload, not a remount. This is where a
  // `useState`-plus-effect version would flash expanded before correcting
  // itself, and where a hydration mismatch would show up.
  await expect(page.getByRole("button", { name: "Expand the sidebar" })).toBeVisible();
  await page.getByRole("button", { name: "Expand the sidebar" }).click();
  await expect(thread).toBeVisible();
});

test("dark is chosen, not inherited, and survives a reload", async ({ page }) => {
  /**
   * **The property D-046 exists for.** The browser here reports
   * `prefers-color-scheme: dark`, and the page must still be light until a
   * person says otherwise — that is the whole decision, and a media query left
   * in `globals.css` would fail exactly this assertion.
   */
  await page.emulateMedia({ colorScheme: "dark" });
  await signIn(page);
  await page.goto(`/orgs/${ids.org}/settings`);

  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  await expect(page.locator("html")).not.toHaveAttribute("data-theme", "dark");

  await page.getByRole("button", { name: "Dark" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

  await page.reload();
  // Applied by the pre-paint script rather than by React, which is the only way
  // a person who chose dark avoids a white flash on every load.
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});

test("settings names what is not built, and offers no controls for it", async ({ page }) => {
  await signIn(page);
  await page.goto(`/orgs/${ids.org}/settings`);

  for (const unbuilt of ["System instructions", "Tone", "Preferences"]) {
    await expect(page.getByRole("heading", { name: unbuilt })).toBeVisible();
  }
  await expect(page.getByText("Coming soon")).toHaveCount(3);
  // Honest gap, not a convincing lie: nothing there is pressable.
  await expect(page.getByRole("combobox")).toHaveCount(0);
});

test("the admin screens are reachable from settings, not from the chat", async ({ page }) => {
  await signIn(page);
  await page.goto(`/orgs/${ids.org}/conversations`);

  // The chat carries no admin navigation at all.
  await expect(page.getByRole("link", { name: "Data sources" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Members" })).toHaveCount(0);

  await page.getByRole("link", { name: /Settings/ }).click();
  await expect(page.getByRole("link", { name: "Data sources" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Members" })).toBeVisible();
});

test("the old organization URL still reaches the product", async ({ page }) => {
  // It is in people's history and in older docs, so it redirects rather than 404s.
  await signIn(page);
  await page.goto(`/orgs/${ids.org}`);

  await expect(page).toHaveURL(new RegExp(`/orgs/${ids.org}/conversations$`));
});
