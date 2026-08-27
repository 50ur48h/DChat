import { describe, expect, it } from "vitest";

import { STEP_SENTENCES, sentence } from "./trace";

/**
 * Every step reads as a sentence, including when the payload is empty.
 *
 * **Written because the first version of this vocabulary got it wrong.**
 * `knowledge_consulted` built its detail by joining two optional parts and
 * appending a full stop — so an event carrying neither rendered a bare `"."` to
 * a real person. The module's own docstring states the rule it broke: a payload
 * shape that changed should make the trace *quieter*, not wrong.
 *
 * The empty payload is the case worth sweeping, because it is the one no fixture
 * produces and no browser run exercises: `agent_events.payload` defaults to
 * `'{}'` in the schema, and an older server, a truncated write or a future event
 * type all arrive here as an object with nothing in it.
 *
 * Everything below indexes the record through `Object.entries` or `build()`,
 * never through `STEP_SENTENCES[type]` directly — `noUncheckedIndexedAccess` is
 * on, and a bare index is possibly-undefined.
 */

/** Punctuation and space only — what a broken template leaves behind. */
const ONLY_PUNCTUATION = /^[\s.,;:—–-]*$/;

const ENTRIES = Object.entries(STEP_SENTENCES);

/** One builder by name, or a failure that says which name was missing. */
function build(type: string, payload: Record<string, unknown> = {}) {
  const make = STEP_SENTENCES[type];
  if (!make) throw new Error(`STEP_SENTENCES has no entry for ${type}`);
  return make(payload);
}

describe("every step's sentence survives an empty payload", () => {
  it("covers more than a handful of event types, so the sweep means something", () => {
    // Guards the sweep itself: an import that silently resolved to `{}` would
    // make every test below pass by describing nothing.
    expect(ENTRIES.length).toBeGreaterThan(15);
  });

  it.each(ENTRIES)("%s says something, and never only punctuation", (_type, make) => {
    const { lead, rest } = make({});

    expect(lead.trim().length).toBeGreaterThan(0);
    // `rest` is allowed to be empty — that is the honest outcome when an event
    // carries nothing worth adding. What it may never be is *almost* empty.
    if (rest !== "") {
      expect(rest).not.toMatch(ONLY_PUNCTUATION);
    }
  });

  it.each(ENTRIES)("%s never leaks a missing value into the prose", (_type, make) => {
    const { lead, rest } = make({});
    const whole = `${lead} ${rest}`;

    expect(whole).not.toContain("undefined");
    expect(whole).not.toContain("null");
    expect(whole).not.toContain("NaN");
    expect(whole).not.toContain("[object Object]");
  });
});

describe("the sentences use what the events carry", () => {
  it("counts in words, and never says '1 rows'", () => {
    expect(build("query_executed", { row_count: 1, duration_ms: 4 }).rest).toContain(
      "1 row came back",
    );
    expect(build("query_executed", { row_count: 2, duration_ms: 4 }).rest).toContain(
      "2 rows came back",
    );
  });

  it("says why a join was checked, not just that it was", () => {
    // The event carries `answerable`; the sentence carries the consequence,
    // which is the part a person cannot infer from a machine name.
    expect(build("capability_checked", { answerable: true }).rest).toContain("they can");

    const refused = build("capability_checked", { answerable: false, unreachable: ["a and b"] });
    expect(refused.rest).toContain("cannot");
    expect(refused.rest).toContain("invent rows");
  });

  it("reads a run's totals, which nothing rendered before", () => {
    const step = build("run_finished", { totals: { queries: 1, llm_calls: 4 } });

    expect(step.rest).toContain("1 query");
    expect(step.rest).toContain("4 model calls");
  });

  it("keeps a step number of zero rather than dropping it as falsy", () => {
    // `num` returns a number and `0` is falsy in TypeScript; a truthiness test
    // here would silently lose a legitimate step.
    expect(build("step_started", { iteration: 0 }).rest).toContain("step 0");
    expect(build("step_started").rest).toBe("");
  });
});

describe("an event this build has never heard of", () => {
  it("shows its raw name rather than being hidden", () => {
    // The module's standing rule: a trace that silently omits a step is worse
    // than an ugly one. This is the fallback for an event from a *newer* server.
    const step = sentence({
      seq: 1,
      type: "something_new",
      payload: {},
    } as unknown as Parameters<typeof sentence>[0]);

    expect(step.lead).toBe("something_new");
    expect(step.rest).toBe("");
  });
});

describe("the period check is legible, including when it could not run", () => {
  // **B-157, D-059.** The owner's requirement: a run where the check abstained
  // must be distinguishable from one where it ran and passed. Three statuses,
  // three different sentences — and the abstention carries its reason, because
  // "could not be checked" with no "why" is the same silence wearing a label.

  it("says both periods when the answer is outside what the catalogue records", () => {
    const step = build("answer_composed", {
      limitations: 1,
      coverage: {
        status: "outside",
        reason: "",
        answered: "2023-01 to 2024-12",
        available: "2025-01 to 2025-12",
      },
    });

    expect(step.rest).toContain("2023-01 to 2024-12");
    expect(step.rest).toContain("2025-01 to 2025-12");
  });

  it("says the answer sat inside the period when it did", () => {
    const step = build("answer_composed", {
      limitations: 0,
      coverage: {
        status: "contained",
        reason: "",
        answered: "2025-12",
        available: "2025-01 to 2025-12",
      },
    });

    expect(step.rest).toContain("inside the period");
  });

  it("says why it could not look, rather than saying nothing", () => {
    const step = build("answer_composed", {
      limitations: 0,
      coverage: {
        status: "abstained",
        reason: "the result was cut off at the row limit",
        answered: null,
        available: null,
      },
    });

    expect(step.rest).toContain("could not be checked");
    expect(step.rest).toContain("row limit");
  });

  it("is silent when the payload carries no coverage at all", () => {
    // `coverage: null` is what an older server sends, and what a run that never
    // reached the composer leaves behind. It must not render as though a check
    // happened — `nested()` returning null rather than `{}` is what guarantees
    // that, and this is the assertion that holds it.
    expect(build("answer_composed", { limitations: 0, coverage: null }).rest).not.toContain(
      "period",
    );
    expect(build("answer_composed", { limitations: 0 }).rest).not.toContain("period");
  });
});

describe("the trace spends what the events already carry", () => {
  // WP13.21. Every field read below has been in the payload since long before
  // the sentence said it out loud — "what it considered" and "what it ruled
  // out" were being discarded at render time. No new emit-time field is read.

  it("says how much of each table the model actually saw", () => {
    // **B-160.** "25 tables" said nothing about detail, and it described the
    // search's result rather than the prompt's.
    const step = build("context_selected", {
      tables: ["public.fact_sale", "public.dim_outlet", "public.dim_calendar"],
      tables_in_full: 1,
      tables_in_outline: 2,
      tables_dropped: 0,
    });

    expect(step.rest).toContain("1 in full and 2 in outline");
  });

  it("says when a table matched and would not fit", () => {
    const step = build("context_selected", {
      tables: ["public.fact_sale"],
      tables_in_full: 1,
      tables_in_outline: 0,
      tables_dropped: 3,
    });

    expect(step.rest).toContain("3 tables matched but would not fit");
  });

  it("says when the thread chose the tables rather than the question", () => {
    const step = build("context_selected", {
      tables: ["public.fact_sale"],
      tables_found_via: "thread",
    });

    expect(step.rest).toContain("named no table of its own");
  });

  it("says how many tables were found by meaning rather than by wording", () => {
    const step = build("context_selected", {
      tables: ["public.fact_sale", "public.dim_outlet"],
      tables_found_by: { "public.fact_sale": "vector", "public.dim_outlet": "lexical" },
    });

    expect(step.rest).toContain("1 of them was found by meaning");
  });

  it("tells no definitions matched apart from having no definitions", () => {
    // **B-087's finding, said out loud.** An empty list beside a non-zero count
    // is the whole point.
    const none = build("context_selected", {
      tables: ["public.fact_sale"],
      definitions_applied: [],
      definitions_available: 18,
    });
    const some = build("context_selected", {
      tables: ["public.fact_sale"],
      definitions_applied: ["net revenue"],
      definitions_available: 18,
    });

    expect(none.rest).toContain("None of your 18 definitions matched");
    expect(some.rest).toContain("1 of your 18 definitions applied");
  });

  it("mentions restricted columns and earlier turns when there are any", () => {
    const step = build("context_selected", {
      tables: ["public.fact_sale"],
      restrictions: 2,
      history_turns: 3,
    });

    expect(step.rest).toContain("2 columns are restricted");
    expect(step.rest).toContain("3 earlier turns");
  });

  it("stays quiet about every count that is zero", () => {
    // A sentence for each absent thing is how a reader learns to skip the panel.
    const step = build("context_selected", {
      tables: ["public.fact_sale"],
      tables_dropped: 0,
      restrictions: 0,
      history_turns: 0,
      definitions_available: 0,
    });

    expect(step.rest).not.toContain("would not fit");
    expect(step.rest).not.toContain("restricted");
    expect(step.rest).not.toContain("earlier turn");
    expect(step.rest).not.toContain("definitions");
  });

  it("names what the capability check ruled out, not only that it ran", () => {
    const step = build("capability_checked", {
      answerable: true,
      unreachable: ["fact_sale ↔ fact_purchase"],
    });

    expect(step.rest).toContain("Ruled out joining");
    expect(step.rest).toContain("fact_sale ↔ fact_purchase");
  });

  it("says which pairs can only be compared, not joined", () => {
    const step = build("capability_checked", {
      answerable: true,
      comparable: ["fact_sale ↔ fact_purchase via dim_business"],
    });

    expect(step.rest).toContain("compared side by side");
  });

  it("tells an unprofiled source apart from a checked one", () => {
    // B-157/D-059: the key is present even when null, and the two must not read
    // the same.
    const known = build("capability_checked", {
      answerable: true,
      available_period: "2025-01 to 2025-12",
    });
    const unknown = build("capability_checked", { answerable: true, available_period: null });
    const absent = build("capability_checked", { answerable: true });

    expect(known.rest).toContain("2025-01 to 2025-12");
    expect(unknown.rest).toContain("has been profiled");
    expect(absent.rest).not.toContain("profiled");
  });
});
