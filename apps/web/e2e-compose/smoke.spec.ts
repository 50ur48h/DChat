/**
 * The walk, in a browser, against the whole stack (plan WP11.2b).
 *
 * One test, not seven, because this is a **path** rather than a set of
 * properties: nothing here can be checked without everything before it having
 * happened. Splitting it would either re-register a database six times or share
 * state between tests Playwright is entitled to run in any order. The
 * `test.step` names are what a failure reports, so a red run says *which* part
 * of the stack came apart before anyone opens a trace.
 *
 * **Everything is real except the model.** The plan's gate wording is both why
 * this file can be trusted and why it must not be over-read: the smoke proves
 * that signing in, registering, discovering, asking, answering, storing and
 * tracing all connect; it can never show that a question was understood. So
 * there is one assertion here no stub could satisfy — the row count comes back
 * from the seeded database — and one only the stub can: the answer's wording.
 * The second is not decoration. It fails loudly if this stack was brought up
 * with a real key, which would otherwise spend the owner's money and turn a
 * model's variability into a red build.
 *
 * **It navigates by clicking, the way a person does.** An earlier version jumped
 * to each screen with `page.goto`, which is a full page load — and a full load
 * throws the session away and re-mints it, so every screen spent a moment
 * showing a signed-in visitor a sign-in form (**B-104**). Clicking is both more
 * faithful and steadier; the one deliberate full reload is the last step, where
 * the point *is* that nothing was being held in the page.
 *
 * **Idempotent on purpose.** `ops/scripts/web_smoke.sh` throws its stack away by
 * default, but a developer debugging a failure keeps it (`SMOKE_KEEP=1`) and
 * re-runs. So each step asks what already exists before it creates anything.
 */

import { expect, test, type Page } from "@playwright/test";

/** Who signs in. A dedicated subject, so a developer's own organization and its
 *  demo fixtures are neither read nor written by this walk. */
const SUBJECT = "smoke";
const ORG = "Smoke";

/**
 * How the **API** reaches the seeded database — a compose service name, not
 * `localhost`. The container and the browser are on different networks, and
 * getting this wrong is the most common mistake in the quickstart as well.
 */
const SOURCE = {
  name: "Pizza demo",
  host: process.env.SMOKE_DB_HOST ?? "seed-pizza-pg",
  port: process.env.SMOKE_DB_PORT ?? "5432",
  database: process.env.SMOKE_DB_NAME ?? "pizza",
  username: process.env.SMOKE_DB_USER ?? "pizza_readonly",
  password: process.env.SMOKE_DB_PASSWORD ?? "",
};

/**
 * The gate's count question. The scripted model does not read it — it answers
 * `SELECT count(*) FROM orders` whatever it is asked — so this is here to be
 * the question a person would type, not to be understood.
 */
const QUESTION = "how many orders were placed in July 2026?";

/** What `llm/scripted.py` composes, verbatim. */
const SCRIPTED_ANSWER = "The scripted model answered from a fixed script.";

/**
 * `ops/seed/truths.json` → `row_counts.orders`, held honest by `make check.truths`.
 *
 * **The one number here no stub could have produced.** The scripted model
 * supplies the SQL; the DAL validates it, resolves `orders` against this
 * organization's own catalog, runs it against the seeded database, and this is
 * what came back. If the fixture's size ever changes this assertion is supposed
 * to fail: it is the assertion that the query really ran.
 */
const TOTAL_ORDERS = "71798";

/**
 * Wait until the page is running, not merely served.
 *
 * **Necessary, and not a workaround.** The compose stack runs `next dev`, which
 * compiles a route's client bundle on its first request — so the server's HTML
 * can be on screen for many seconds before any React is attached to it. Acting
 * on that HTML fails in the most misleading way available: `fill` lands in a DOM
 * nothing is listening to, the click reaches a button with no handler,
 * hydration then resets the field, and the report reads "signing in does not
 * work" over a screenshot of an untouched form. That cost the first two runs of
 * this file.
 *
 * The signal is the app's own: `<ApiHealth>` says "Checking…" until a client
 * effect has called `/healthz` and come back. So this is also the smoke's step
 * zero — **the browser can reach the API** — which is the first thing that has
 * to be true and the first thing a wrong `NEXT_PUBLIC_API_URL` breaks.
 */
async function running(page: Page) {
  await expect(page.getByText("Healthy")).toBeVisible({ timeout: 90_000 });
}

/**
 * The organization this walk owns: created on the first run, reused after.
 *
 * **Both paths end in the chat, because that is where the product now opens**
 * (WP13.1b). A member who already has an organization is redirected there by the
 * front door and never sees a list; one who does not gets the bootstrap screen,
 * which is the only place an organization can be created and is therefore still
 * a real screen rather than a leftover.
 *
 * The walk stays inside the organization from here by clicking, never by a URL
 * this file assembled. An id built here would name a row nothing else knows
 * about, and every step after would 404 in a way that looked like a routing bug
 * rather than like a test at fault.
 */
async function organization(page: Page): Promise<void> {
  const chat = /\/orgs\/[0-9a-f-]{36}\/conversations$/;

  /**
   * **Wait for one of the two outcomes before deciding which happened.**
   *
   * The front door's redirect is a client-side `replace` that runs after the
   * page has loaded, so reading `page.url()` straight away sees `/` even for a
   * member who is about to land in the chat. The first version of this helper
   * did exactly that, and on the second run — when the organization already
   * existed — it went looking for a create-organization form on the chat screen
   * and sat there until the test timed out.
   */
  await expect(
    page
      .getByRole("heading", { name: "Create an organization" })
      .or(page.getByRole("heading", { name: "What would you like to know?" }).first()),
  ).toBeVisible({ timeout: 60_000 });

  if (chat.test(page.url())) return;

  const listed = page.getByText(ORG, { exact: true });
  if ((await listed.count()) === 0) {
    await page.getByLabel("Name", { exact: true }).fill(ORG);
    await page.getByRole("button", { name: "Create" }).click();
  }
  await expect(listed).toBeVisible();

  // `/orgs/{id}` is a redirect to the chat now; following it is what a person
  // clicking from the bootstrap screen actually does.
  await page.getByRole("button", { name: "Members" }).first().click();
  await page.waitForURL(chat, { timeout: 30_000 });
}

/** Into Settings, which is where every admin screen moved (WP13.1b). */
async function settings(page: Page): Promise<void> {
  await page.getByRole("link", { name: /Settings/ }).click();
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
}

/** Back to the chat, the way the sidebar offers it. */
async function newChat(page: Page): Promise<void> {
  await page.getByRole("link", { name: /New chat/ }).first().click();
  await page.waitForURL(/\/orgs\/[0-9a-f-]{36}\/conversations$/, { timeout: 30_000 });
}

test("the stack answers a question asked in a browser", async ({ page }) => {
  test.skip(
    !SOURCE.password,
    "SMOKE_DB_PASSWORD is unset. Run this through ops/scripts/web_smoke.sh, which brings up a stack whose model is scripted.",
  );

  // A browser console error is invisible in CI unless something prints it, and
  // it is often the only account of why a screen did not do what it was told.
  page.on("pageerror", (error) => console.log(`  page error: ${error.message}`));
  page.on("requestfailed", (request) => {
    // An aborted request is this app working correctly: every screen that
    // fetches on mount aborts on unmount, so a navigation cancels one by design.
    // Printing those would put a line that always appears next to lines that
    // only appear when something is wrong, which is how a log stops being read.
    if (request.failure()?.errorText.includes("ERR_ABORTED")) return;
    console.log(`  request failed: ${request.method()} ${request.url()}`);
  });

  let conversation = "";

  await test.step("sign in, and land in the chat", async () => {
    await page.goto("/");
    await running(page);

    await page.getByLabel("Who are you").fill(SUBJECT);
    await page.getByRole("button", { name: "Continue" }).click();

    await organization(page);

    // **The direction of WP13.1b, asserted rather than assumed.** Signing in
    // used to end on a profile with an Ask button; it now ends in a composer.
    await expect(
      page.getByRole("heading", { name: "What would you like to know?" }).first(),
    ).toBeVisible({ timeout: 30_000 });
  });

  await test.step("register the seeded database, from Settings", async () => {
    // The admin screens moved behind Settings when the chat became the home.
    await settings(page);
    await page.getByRole("link", { name: "Data sources" }).click();
    await expect(page.getByRole("heading", { name: "Register a database" })).toBeVisible();

    if ((await page.getByRole("heading", { name: SOURCE.name }).count()) === 0) {
      // `exact`, because "Username" contains "Name".
      await page.getByLabel("Name", { exact: true }).fill(SOURCE.name);
      await page.getByLabel("Host").fill(SOURCE.host);
      await page.getByLabel("Port").fill(SOURCE.port);
      await page.getByLabel("Database").fill(SOURCE.database);
      await page.getByLabel("Username").fill(SOURCE.username);
      await page.getByLabel("Password", { exact: true }).fill(SOURCE.password);
      await page.getByRole("button", { name: "Register" }).click();
    }
    await expect(page.getByRole("heading", { name: SOURCE.name })).toBeVisible();
  });

  await test.step("prove the credentials cannot write", async () => {
    // Not politeness and not skippable: until this has passed, refreshing the
    // catalog refuses with "this data source has not been proven read-only".
    // The quickstart omitted this step, and the walk that found the omission is
    // why it is a step of its own here too.
    await page.getByRole("button", { name: "Test connection" }).click();
    // The panel's own three facts, not the badge: "reachable" is not
    // "read-only", and `require` encrypts without verifying anything, so the
    // screen reports them separately and this asserts the one that gates the
    // next step. `exact` matters — "not proven" contains "proven".
    await expect(page.getByText("proven", { exact: true })).toBeVisible();
  });

  await test.step("read and profile the schema", async () => {
    // Both report what they found in the API's own words, so these assert the
    // shape of that sentence rather than a count: the fixture's table count is
    // not what this step is about, and pinning it here would make a change to
    // the seed fail in two places instead of the one that means it.
    await page.getByRole("button", { name: "Refresh catalog" }).click();
    await expect(page.getByText(/table\(s\)/)).toBeVisible();
    await page.getByRole("button", { name: "Profile columns" }).click();
    await expect(page.getByText(/Profiled \d+ column\(s\)/)).toBeVisible();
  });

  await test.step("choose the database this organization answers from", async () => {
    /**
     * **D-045's control, exercised where it matters.** An Admin chooses once;
     * no member ever picks a database again, which is why the step after this
     * one is a person typing a question and nothing else.
     */
    // Idempotent, like the registration step above and for the same reason: this
    // walk is run repeatedly against a stack that may already have been walked,
    // and a step that only works on a clean database is a step that fails for a
    // reason no reader will connect to the product.
    const badge = page.getByText("Answers questions");
    if ((await badge.count()) === 0) {
      await page.getByRole("button", { name: "Answer questions from this" }).click();
    }
    await expect(badge).toBeVisible();
  });

  await test.step("ask from the chat, which creates the thread", async () => {
    /**
     * **The walk this work package exists to make possible.** It used to be
     * four steps — Back, Ask, Start, and a link to open what Start had made —
     * with a database picker in the middle. It is now: type the question.
     *
     * The thread is created by sending, so this step is both "start a
     * conversation" and "ask", and there is no longer a moment where an empty
     * conversation exists on screen.
     */
    await settings(page);
    await newChat(page);

    const box = page.getByLabel("Your question");
    await expect(box).toBeEnabled({ timeout: 30_000 });
    await expect(page.getByText(SOURCE.name)).toBeVisible();

    await box.fill(QUESTION);
    await page.getByRole("button", { name: "Send" }).click();

    await page.waitForURL(/\/conversations\/[0-9a-f-]{36}$/, { timeout: 30_000 });
    conversation = page.url();
  });

  await test.step("wait for the answer card", async () => {
    // The run is accepted before it finishes, and the screen has to notice it
    // finish by itself — the Phase 7 gate's own property (B-044), here against a
    // real API rather than a stub of one. Generous, because this is where a real
    // agent run happens: catalog retrieval, a validated statement, an execution
    // against another database, a stored artifact and a dozen durable events.
    await expect(page.getByText(SCRIPTED_ANSWER)).toBeVisible({ timeout: 120_000 });
    // `exact`, because the shell wraps the card in further elements and a
    // substring match now resolves to the badge and its ancestors alike. The
    // badge is the assertion; a `.first()` here would have hidden a real
    // duplicate instead of naming the one element that matters.
    await expect(page.getByText("answered", { exact: true })).toBeVisible();
  });

  await test.step("open the query behind the answer", async () => {
    await page.getByRole("button", { name: /Show the query behind this/ }).click();

    await expect(page.getByText(/SELECT COUNT\(\*\)/i)).toBeVisible();
    // **The database answered.** Everything above this line could be satisfied
    // by a stack that never opened a socket to the customer's database.
    await expect(
      page.getByRole("cell", { name: TOTAL_ORDERS, exact: true }),
      `the seeded database should have returned ${TOTAL_ORDERS} orders (ops/seed/truths.json)`,
    ).toBeVisible();
  });

  await test.step("open the trace", async () => {
    await page.getByRole("button", { name: /how this was worked out/ }).click();

    await expect(page.getByText("Read the catalog")).toBeVisible();
    await expect(page.getByText("Wrote a query")).toBeVisible();
    await expect(page.getByText("Got results")).toBeVisible();
    await expect(page.getByText("Finished")).toBeVisible();
    // The machine name never reaches a person.
    await expect(page.getByText("query_executed")).toHaveCount(0);
  });

  await test.step("the first answer keeps its evidence when a second is asked", async () => {
    /**
     * **The gate walk's defect, in the place it was found** (**B-106**). The
     * screen held one run and rendered one card, so a second question took the
     * previous answer's chart, method line, limitations, findings, evidence
     * controls and trace off the page — every one of them a durable row with no
     * route back. A chart that survives only until the next message does not
     * meet *"the trend question renders a chart"*.
     *
     * Asserted on the **count**, because presence passed against the broken
     * screen: the newest answer always had its card.
     */
    await page.getByLabel("Ask a question").fill("and how many were cancelled?");
    await page.getByRole("button", { name: "Send" }).click();

    await expect(page.getByText(SCRIPTED_ANSWER)).toHaveCount(2, { timeout: 120_000 });
    // **`Show|Hide`, not `Show`.** The step above opened the first answer's
    // evidence and its trace, so those two controls now read *Hide*. Matching
    // only "Show" counted one of two and reported the product as broken when it
    // was not — which is the failure mode a smoke can least afford, because the
    // next person to see it red will believe it.
    await expect(page.getByRole("button", { name: /the query behind this/ })).toHaveCount(2, {
      timeout: 60_000,
    });
    await expect(page.getByRole("button", { name: /how this was worked out/ })).toHaveCount(2);
  });

  await test.step("a reload replays all of it from durable rows", async () => {
    // M8's criterion, and the cheapest possible check that the run was *stored*
    // rather than held in the page: none of the above is allowed to have been a
    // client-side illusion.
    //
    // **Two assertions, because two different things can be wrong here and only
    // one of them is the product's.** A full load throws the session away and
    // re-mints it, and until it comes back this screen shows a signed-in visitor
    // a sign-in form (**B-104**); `running` cannot help, since the health widget
    // is on the home page only. Separately, `next dev` serves this route's
    // client bundle on demand, and once in this file's history a chunk request
    // simply failed on a cold container — the page then sits at the sign-in card
    // forever, and asserting the answer first reports that as *the answer never
    // arrived*, which is the one thing it does not mean.
    //
    // So: reload until the page is actually running, bounded, and only then ask
    // about the answer. The retry is scoped to a named non-product failure — a
    // dev server that did not serve a file — and the assertion it guards is
    // still a single shot.
    await expect(async () => {
      await page.goto(conversation);
      await expect(page.getByRole("button", { name: "Continue" })).toHaveCount(0, {
        timeout: 30_000,
      });
    }).toPass({ timeout: 120_000 });

    // **Both** answers, and both sets of evidence: a reload rebuilds the whole
    // thread from durable rows, not just the last thing said.
    await expect(page.getByText(SCRIPTED_ANSWER)).toHaveCount(2, { timeout: 30_000 });
    await expect(page.getByRole("button", { name: /the query behind this/ })).toHaveCount(2);
  });
});
