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
 * The empty payload is the case worth sweeping, because it is the one no
 * fixture produces and no e2e run exercises: `agent_events.payload` defaults to
 * `'{}'` in the schema, and an older server, a truncated write or a future event
 * type all arrive here as an object with nothing in it.
 */

/** Punctuation and space only — what a broken template leaves behind. */
const ONLY_PUNCTUATION = /^[\s.,;:—–-]*$/;

describe("every step's sentence survives an empty payload", () => {
  const types = Object.keys(STEP_SENTENCES);

  it("covers more than a handful of event types, so the sweep means something", () => {
    // Guards the sweep itself: an import that silently resolved to `{}` would
    // make every test below pass by describing nothing.
    expect(types.length).toBeGreaterThan(15);
  });

  it.each(types)("%s says something, and never only punctuation", (type) => {
    const { lead, rest } = STEP_SENTENCES[type]({});

    expect(lead.trim().length).toBeGreaterThan(0);
    // `rest` is allowed to be empty — that is the honest outcome when an event
    // carries nothing worth adding. What it may never be is *almost* empty.
    if (rest !== "") {
      expect(rest).not.toMatch(ONLY_PUNCTUATION);
    }
  });

  it.each(types)("%s never leaks a missing value into the prose", (type) => {
    const { lead, rest } = STEP_SENTENCES[type]({});
    const whole = `${lead} ${rest}`;

    expect(whole).not.toContain("undefined");
    expect(whole).not.toContain("null");
    expect(whole).not.toContain("NaN");
    expect(whole).not.toContain("[object Object]");
  });
});

describe("the sentences use what the events carry", () => {
  it("counts in words, and never says '1 rows'", () => {
    const one = STEP_SENTENCES.query_executed({ row_count: 1, duration_ms: 4 });
    const many = STEP_SENTENCES.query_executed({ row_count: 2, duration_ms: 4 });

    expect(one.rest).toContain("1 row came back");
    expect(many.rest).toContain("2 rows came back");
  });

  it("says why a join was checked, not just that it was", () => {
    // The event carries `answerable`; the sentence carries the consequence,
    // which is the part a person cannot infer from a machine name.
    const ok = STEP_SENTENCES.capability_checked({ answerable: true });
    const no = STEP_SENTENCES.capability_checked({ answerable: false, unreachable: ["a and b"] });

    expect(ok.rest).toContain("they can");
    expect(no.rest).toContain("cannot");
    expect(no.rest).toContain("invent rows");
  });

  it("reads a run's totals, which nothing rendered before", () => {
    const step = STEP_SENTENCES.run_finished({ totals: { queries: 1, llm_calls: 4 } });

    expect(step.rest).toContain("1 query");
    expect(step.rest).toContain("4 model calls");
  });

  it("keeps a step number of zero rather than dropping it as falsy", () => {
    // `num` returns a number and `0` is falsy in TypeScript; a truthiness test
    // here would silently lose a legitimate step.
    expect(STEP_SENTENCES.step_started({ iteration: 0 }).rest).toContain("step 0");
    expect(STEP_SENTENCES.step_started({}).rest).toBe("");
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
