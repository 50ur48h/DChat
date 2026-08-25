import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Button } from "./button";
import { EmptyState } from "./empty-state";
import { Pending } from "./pending";
import { SkeletonList } from "./skeleton";

/**
 * The three waiting/empty primitives (D-049).
 *
 * What is asserted here is the **rules**, not the pixels: a skeleton announces
 * itself once rather than per row, an empty state carries exactly one action,
 * and a pending word is a live region. Those are the parts a future change can
 * break without anything looking wrong.
 */

describe("<SkeletonList />", () => {
  it("announces itself once, not once per row", () => {
    render(<SkeletonList rows={5} label="Loading members" />);

    // One label for the whole thing. Eight anonymous boxes read out in sequence
    // is worse than silence.
    expect(screen.getByRole("status")).toHaveTextContent("Loading members");
    expect(screen.getAllByRole("status")).toHaveLength(1);
  });

  it("hides the bars from assistive technology", () => {
    const { container } = render(<SkeletonList rows={3} label="Loading" />);

    // The bars carry no information the label does not.
    expect(container.querySelectorAll('[aria-hidden="true"]')).toHaveLength(3);
  });

  it("draws the number of rows it was asked for", () => {
    const { container } = render(<SkeletonList rows={2} label="Loading" />);
    expect(container.querySelectorAll('[aria-hidden="true"]')).toHaveLength(2);
  });
});

describe("<EmptyState />", () => {
  it("says what belongs here and offers the one action", () => {
    render(
      <EmptyState title="No databases registered" action={<Button>Register</Button>}>
        Register one and this organization can start asking questions of it.
      </EmptyState>,
    );

    expect(screen.getByText("No databases registered")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Register" })).toBeInTheDocument();
  });

  it("can carry a sentence instead of a control, for someone who may not act", () => {
    /**
     * B-008's rule at the primitive: a Reader is told **who** can act rather
     * than shown a control the API would refuse. Never a disabled button —
     * that looks operable and is not.
     */
    render(
      <EmptyState title="No databases registered" action={<span>An Admin can register one.</span>}>
        Once one is registered, this organization can ask questions of it.
      </EmptyState>,
    );

    expect(screen.getByText("An Admin can register one.")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("<Pending />", () => {
  it("puts the word in a live region so the change is announced", () => {
    render(<Pending>Reading the schema…</Pending>);

    const word = screen.getByText("Reading the schema…");
    expect(word).toHaveAttribute("aria-live", "polite");
  });

  it("hides the bar, which says nothing the word does not", () => {
    const { container } = render(<Pending bar>Profiling the columns…</Pending>);

    expect(screen.getByText("Profiling the columns…")).toBeInTheDocument();
    expect(container.querySelectorAll('[aria-hidden="true"]').length).toBeGreaterThan(0);
  });
});
