# Design system — data-agent

Binding for all UI, the way `architecture.md` is binding for the system. If a
screen needs something not defined here, add it here first, in the same PR.

**Rewritten 2026-08-25 (D-047).** The previous direction — cool greys, borderless
cards floating on `#f7f8fa`, an indigo primary — was reviewed against two static
mockups and replaced. What follows is the direction that was chosen. The
accessibility rules and the chart ramp survived the change unaltered, and the
sections that say so say why.

## The feel

Warm paper, layered depth, and an answer that is the calmest thing on the screen.
Nothing is pure white or neutral grey: the page is warm off-white, surfaces sit
on it in tints of the same warmth, and depth comes from a hairline *and* a wide
faint shadow rather than from a shadow alone. One terracotta accent. A serif for
the machine's own words and a sans for every piece of chrome around them.

Closer to a considered reading application than to a dashboard.

**Deliberately not:** cool grey, dense tables, borders everywhere, or an
interface whose chrome is louder than its content.

Four rules that decide most arguments:

1. **Space is the layout.** Reach for spacing before borders, and for a border
   only when spacing cannot express the grouping.
2. **Colour means something.** Warm grey is the default; a hue is a claim that
   this thing has a state or a category. Never colour for interest.
3. **One primary action per view.** Everything else is quieter.
4. **The content outranks the chrome.** An answer's attribution, its evidence
   controls and its trace must all be visually lighter than the sentence they
   belong to. If a label competes with the thing it labels, the label is wrong.

## Tokens

Defined once in `apps/web/src/app/globals.css` as CSS custom properties. Use the
token, never a raw hex value — a colour that appears in a component file is a bug.

### Colour

| Token | Light | Purpose |
|---|---|---|
| `--paper` | `#faf9f5` | page background — warm off-white |
| `--rail` | `#f2f0e9` | the sidebar, a shade deeper than the page |
| `--surface` | `#ffffff` | cards, panels, inputs, menus |
| `--inset` | `#f6f4ee` | panel headers, wells, table headers, nested fills |
| `--ink` | `#23211d` | primary text |
| `--ink-muted` | `#6b6760` | secondary text, labels |
| `--ink-subtle` | `#6f6a61` | captions, placeholders, the quietest chrome |
| `--line` | `#e6e2d8` | hairlines |
| `--line-strong` | `#d8d3c6` | the one border that should be noticed (the composer) |

`--ink-subtle` is only a shade lighter than `--ink-muted` on purpose. It is a
*role* rather than a step down in contrast: both meet AA, because this system has
no tier of text that is allowed to be hard to read.

**The accent.** One hue, two jobs, and they are not interchangeable.

| Token | Light | Purpose |
|---|---|---|
| `--clay` | `#b55231` | fills that carry white text — the primary button, the agent's mark |
| `--clay-hover` | `#9d4529` | the same, hovered |
| `--clay-deep` | `#9a4526` | the accent as **text** on paper — quiet buttons, links |
| `--clay-soft` | `#f7ece6` | tinted fills: the question bubble |
| `--clay-line` | `#efdfd5` | the border on a `--clay-soft` fill |
| `--on-clay` | `#ffffff` | text on `--clay` |
| `--focus-ring` | `#e0b6a3` | 3px outline on `:focus-visible` |

**`--clay` is `#b55231` and not the brighter `#c15f3c` for one measurable
reason**: white on `#c15f3c` is 4.23:1, which fails AA for the 14px text on the
Send button. `#b55231` is 4.98:1 and is visually the same terracotta. A brighter
accent may be used for a fill that carries no text — it must not become
`--clay`.

**Accents.** Still five pairs, re-tuned warm. Each is a `-soft` background with
a readable `-strong` foreground; never use `-soft` for text.

| Token pair | Hue | Used for |
|---|---|---|
| `--accent-mint-soft` / `-strong` | green | answered, healthy, verified, high confidence, admin |
| `--accent-sky-soft` / `-strong` | blue | informational, contributor |
| `--accent-lilac-soft` / `-strong` | violet | neutral category, reader |
| `--accent-peach-soft` / `-strong` | amber | a caveat, pending, a partial answer |
| `--accent-rose-soft` / `-strong` | red | error, denied, destructive |

Semantic aliases (`--ok`, `--warn`, `--danger`) point at the `-strong` values so
status code never names a hue directly.

**Five rather than two, and the reason is the admin screens.** A thread only ever
uses mint and peach, and it would be tempting to shrink the set to those. Roles
and data-source statuses on the members, sources and definitions screens are
*categories* that must stay distinguishable, and the chart ramp cannot serve them
— its hues mean "series 1, series 2", which is a different claim. Every
`-strong` is AA on its own `-soft` and on `--paper`, in both modes.

A refusal is **not** an error and gets no red: `could not answer` is a correct
outcome and is rendered in `--ink-muted` with the word doing the work (D-044).
Red is reserved for a run that *failed* and for destructive confirmation.

**Chart series** — unchanged, and unchanged deliberately.

The eight-slot categorical ramp and its four rules are exactly as they were. It
was validated against the **card surface**, and `--surface` is still `#ffffff` in
light — so the validation still holds and re-running it would be theatre. Slots,
values, dark column and rules all stand:

| Slot | Hue | Light | Dark |
|---|---|---|---|
| 1 | indigo | `#5b5bd6` | `#7a7ae6` |
| 2 | orange | `#eb6834` | `#d95926` |
| 3 | aqua | `#1baf7a` | `#199e70` |
| 4 | yellow | `#eda100` | `#c98500` |
| 5 | magenta | `#e87ba4` | `#d55181` |
| 6 | green | `#008300` | `#008300` |
| 7 | blue | `#2a78d6` | `#3987e5` |
| 8 | red | `#e34948` | `#e66767` |

1. **Fixed order, never cycled.** Slot 8 is the cap the server enforces
   (`charts.MAX_SERIES`); a ninth series would repeat slot 1, and two series
   wearing one colour is a chart that lies. Past eight the honest answer is a
   filter, a facet, or the table.
2. **Colour follows the entity, not its rank.** A filter that removes a series
   must not repaint the survivors.
3. **A chart with one series uses `--clay`**, not slot 1 — the categorical range
   applies only where colour carries a split.
4. **Validated as a set, not chosen.** Both columns pass lightness band, chroma
   floor, colour-vision separation on adjacent pairs and normal-vision
   separation, against the card surface in their own mode. Light slots 3, 4 and 5
   fall below 3:1 contrast on white; the legend and the always-available result
   table are what relieve that, so a chart must never carry identity by colour
   alone. Re-run the check before changing any value here.

**One known adjacency, accepted.** Rule 3 now points at `--clay` (`#b55231`),
which sits near slot 2's orange. They cannot appear in the same chart — rule 3
applies only when there is no split, and slot 2 only exists when there is — so
this is a resemblance between two charts rather than a collision inside one.
Noted so nobody re-derives it as a bug.

### Spacing

A 4px scale, longer than the previous one because generous spacing is most of
what makes this direction work:

`--s1` 4 · `--s2` 8 · `--s3` 12 · `--s4` 16 · `--s5` 20 · `--s6` 24 ·
`--s7` 32 · `--s8` 40 · `--s9` 48 · `--s10` 64 · `--s11` 80 · `--s12` 96

Nothing between steps. Defaults: panel padding `--s5`–`--s7`; between turns in a
thread `--s11`; between the parts of one answer `--s7`; page gutter `--s6`,
growing to `--s7` above 900px.

### Radius and shadow

`--radius-sm` 8px (badges, code) · `--radius-md` 12px (buttons, rows) · `--radius-lg` 18px
(panels) · `--radius-xl` 22px (the composer, the question bubble, an opened evidence
panel) · `--radius-full` 999px.

**Depth is layered, not dropped.** Every raised surface carries a 1px `--line`
*and* a shadow. The border does the near work and the shadow does the far work;
a shadow alone reads as floating, which is the previous direction's look.

`--lift` resting · `--lift-md` hovered, or a surface that should feel closer ·
`--lift-lg` the composer in focus and an opened evidence panel.

### Typography

System stacks only — no webfont, because downloading one at build time makes
`docker build` depend on a third-party CDN (settled in WP0.3, and re-confirmed by
the owner on 2026-08-25 for the serif below).

- `--font-sans` — `ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, …`
- `--font-serif` — `"Iowan Old Style", "Palatino Linotype", Palatino, Georgia, …`
- `--font-mono` — `ui-monospace, SFMono-Regular, Menlo, Consolas, …`

**The serif has exactly one job: the words the agent itself produced.** An
answer's prose is set in `--font-serif`; everything else on the screen — labels,
buttons, badges, table cells, the question you typed, the evidence, the trace —
is `--font-sans`. The split is what separates *what the machine said* from the
interface around it, and it stops being useful the moment it is decorative.
A serif heading, a serif label or a serif empty-state is a bug.

**The serif is a system stack and therefore varies.** Windows resolves it to
Palatino Linotype, macOS to Iowan Old Style. That is accepted: a webfont is the
alternative and it costs a build-time network dependency. Revisit only with
evidence from a real screen, and as a DECISIONS entry.

**These are a specification, not CSS variables.** The previous version of this
table listed them as `--text-*` tokens, and no such token has ever existed in
`globals.css` — components have always written the numbers. Corrected here rather
than inventing seven tokens nothing would consume.

| Role | Size / line-height / weight | Use |
|---|---|---|
| answer | 19 / 32 / 400, `--font-serif` | an answer's own prose |
| display | 28 / 36 / 600 | page title, one per view |
| title | 18 / 26 / 600 | card and section titles |
| body | 15 / 24 / 400 | default |
| small | 13 / 20 / 400 | secondary detail |
| caption | 12 / 18 / 400 | attribution, the quietest chrome |
| label | 11 / 16 / 700, `0.07em`, uppercase | field labels, panel eyebrows |

Numbers in tables use `font-variant-numeric: tabular-nums`.

**On measure.** A thread's column width *is* its reading measure, because the
composer, the answer and the evidence all share one width (see **The thread**
below). At the answer size the column tops out around 936px before a line passes
~95 characters. That is long by convention and acceptable *here*, where an answer
is a sentence or two; it would be the wrong call for a screen that renders
paragraphs. A screen that does should cap its prose and accept the ragged edge.

## Components

Primitives live in `apps/web/src/components/ui/`, one file each, styled with a
co-located CSS Module. They are the only place a token is consumed for layout
chrome; feature components compose them.

- **Card** — `--surface`, `--radius-lg`, 1px `--line`, `--lift`, padding `--s5`.
  A border **and** a shadow; see *Radius and shadow*. `tone="sunken"` swaps the
  fill to `--inset` and drops the shadow for a surface nested inside another.
- **Button** — `primary` (clay fill, white text), `secondary` (surface + `--line`),
  `ghost` (transparent), `danger` (red). Height 40px, radius `--radius-md`, weight 600.
  Disabled drops to 50% opacity and keeps its size.
- **Input** — height 40px, `--surface`, 1px `--line`, radius `--radius-sm`. Focus
  swaps the border to `--clay` and adds the 3px `--focus-ring`.
- **Badge** — pill, a `-soft` fill with its `-strong` text, at the small size.
  **Used sparingly.** A badge is for a state a reader must not miss, or for a
  category on an admin screen. It is not for attribution — see below.
- **PageHeader** — display title, optional muted subtitle, optional right slot.
  **Not used inside a thread**; see *The thread*.

### The shell

Every screen inside an organization renders inside `components/shell/`.

- **AppShell** — the rail, then the page. The page gets `min-width: 0` so wide
  content scrolls inside its own container instead of pushing the rail off
  screen. Below 768px the two stack.
- **Sidebar** — `--rail`, 272px, **fixed and full height**. It is not part of the
  page's scroll: `position: fixed`, `100dvh`, a flex column whose head, New chat
  and footer are `flex: 0 0 auto`, and whose chat list is the only region with
  `overflow-y: auto`. Nothing the page does can scroll the rail away.
  - Row controls (rename, archive) appear on `:hover` **and** `:focus-within`.
  - The current chat is marked by weight, elevation **and** a 3px clay rule —
    never by colour alone.
  - The collapsed/expanded choice persists per browser via `lib/persisted.ts`.
- **Identity block** — bottom-left: a 32px avatar and the person's address in a
  `grid-template-columns: 32px minmax(0, 1fr)` with the address truncating. A
  grid, not a flex row with a margin, because the two must not be able to overlap
  at any width or any address length.

### The thread

- **One column.** The context strip, the question, the answer, the evidence and
  the composer are all the same width — every one a direct child of the same
  container. A left gutter for an avatar is what broke this before: it inset the
  prose while the composer stayed full width, and the mismatch was visible.
- **No page title and no Back button inside a thread.** The thread *is* the
  panel and the rail is the way back. A `PageHeader` here is a leftover from when
  a conversation was a standalone page.
- **Question** — a `--clay-soft` bubble with a `--clay-line` border, aligned
  right, max 78% of the column.
- **Answer** — an attribution line, the prose, then whatever qualifies it.

### Attribution, and the rule it exists to enforce

The line above an answer is a **caption**: the caption size, `--ink-subtle`, a
single row, an 18px agent mark. It carries the run's ending and its grounding.

**Nothing in it may be badge-sized.** This is rule 4 made concrete: an
`answered` pill in `--ok-soft` above a 19px sentence draws the eye to the label
instead of the answer. The state is set in `--ok` text with a 5px dot beside
it — the **word** carries the meaning and the dot is a second cue, so a refusal
still reads as a refusal with colour ignored.

### The working, and how it is revealed

Everything that explains an answer — the method, the query, the rows it returned,
the steps taken — lives in `<details>` disclosures in a quiet strip at the foot
of the answer. `<details>`, so it works with no JavaScript and keyboards get it
free. Closed, they are two 40px text actions in a row; opened, the one you opened
becomes a full-width panel beneath (`flex: 1 0 100%`).

The strip is **faint until the response is hovered**, the way message actions
behave in Claude and ChatGPT. Four rules make that a de-emphasis rather than a
gate, and all four are required:

1. `opacity`, never `display: none` or `visibility: hidden` — the control keeps
   its place in the tab order and its voice in a screen reader while it is faint.
2. `:focus-within` on the response reveals it, so a keyboard user sees it before
   they reach it.
3. `@media (hover: none)` shows it always, because on touch there is no hover and
   this strip is the only route in.
4. `[open]` on the element itself — not on an ancestor via `:has()` — so an
   opened panel can never sit beneath an invisible summary.

Because rule 3 makes this the only touch affordance, the summaries are **40px
tall** like any other target. Quiet is the fill, not the size.

**A caveat is not working-out and is never folded away.** A limitation changes
what the answer *means*; a reader who opens nothing must still see it. Hiding the
qualification while showing the claim is the defect this product exists to avoid
(B-133, D-044). Caveats render in the open, in `--warn-soft`, above the strip.

**The accepted cost**, recorded so it is not rediscovered as a surprise: a
first-time reader may not notice that evidence exists. That is the price of a
focused answer, and the cheap retreat if it proves wrong is a resting opacity
around 40% rather than 0.

### The working state

While a run is going, the thread shows what the agent is **actually doing**, from
the events it is already streaming (`agent_events`, architecture 10.3). It
collapses to a single line when the run settles and stays expandable.

**The rule that matters most: nothing here may be invented.** Every row is one
durable event, in the order it was written, with the wording `trace.tsx` already
maps type names to. There is no scripted sequence, no minimum display time, no
step that appears because a designer expected it to. A progress display that runs
ahead of the work is the most convincing lie an interface can tell, and this
product's whole claim is that its account of itself is checkable. If the events
stop, the display stops.

**Shape.**

- A **header button** — a small mark, a status word, a chevron. While working,
  the word shimmers (`Thinking`, or the phase word); once settled it is
  `Thought for N seconds`, where N is real: the run's `started_at` to
  `finished_at`, falling back to the first and last event timestamps. **Never a
  rounded-up guess** — if neither is known, the word is `Thought` with no number
  rather than a made-up one.
- An **expandable trace** beneath it, a hairline running down the left, rows
  fading up as they arrive.
- Each row is a step word and, where the event carries one, a detail — `4 tables:
  orders, stores…`, `1 row · 31 ms`. The detail comes from fields 10.3 promises,
  and anything absent renders as nothing rather than as `undefined`.
- The **last row while working carries a spinner**; the ones above it carry a
  check. Once the run settles every row carries a check, because every one of
  them finished.

**Behaviour.** It opens itself while the run is live and collapses when the run
settles — watching is the point during, the answer is the point after — but an
explicit toggle wins from then on. Derived from `live`, never synced to it in an
effect.

**Motion is the one place this design uses it, and it is bounded.** A shimmer on
the status word, a fade-up per row, a grid-rows transition on the expander.
Everything is off under `prefers-reduced-motion`, and the trace must be
completely readable with all of it disabled — motion may say *this is happening
now*, and may never be the only thing that says it.

**Accessibility.** The header is a real `<button>` with `aria-expanded` and a
40px target. The status word lives in a `role="status"` region so a change is
announced without stealing focus. The spinner is decorative and `aria-hidden`;
the row's word is what carries the meaning.

**Where else this pattern belongs.** Only where there is real progress to report.
A long operation that returns in one shot — a catalog refresh, a connection
test — has nothing to stream, and dressing it in steps would be inventing them.
Those get an indeterminate shimmer and their real result. **Document ingestion is
the honest second home**: `status`, `chunk_count` and `embedded_count` are real
progressive state and describe themselves.

## Accessibility, non-negotiable

Unchanged in force, and the figures below are measured against the new palette
rather than carried over.

- Every interactive element has a visible `:focus-visible` ring; never
  `outline: none` without a replacement.
- Body and muted text meet WCAG AA. On `--paper`: `--ink` 15.3:1, `--ink-muted`
  5.3:1, `--ink-subtle` 5.1:1, `--clay-deep` 6.1:1, `--ok` 5.9:1, `--warn`
  5.1:1. White on `--clay` is 4.98:1. Every accent `-strong` is at least 4.7:1
  on its own `-soft` fill.
- `-soft` fills are backgrounds only. Never text.
- Colour never carries meaning alone — a status has a word, the current chat has
  weight and elevation, a chart has a legend and a table.
- Targets are at least 40×40px, including a control that is visually faint.
- `prefers-reduced-motion` disables transitions.

## Dark mode

**Light is the default, and the operating system does not get a vote** (D-046).
Tokens are redefined under `[data-theme="dark"]` and under nothing else — there
is no `prefers-color-scheme` rule anywhere in `globals.css`, deliberately, so
there is exactly one route into the dark values and no combination of media
query and attribute that could disagree.

Dark is the same warmth, inverted — not a cool grey theme with the colours
swapped. `--paper` `#171614`, `--rail` `#1d1b18`, `--surface` `#201e1b`,
`--inset` `#26231f`; `--ink` `#f2efe8`, `--ink-muted` `#a8a299`, `--ink-subtle`
`#948e84`; `--line` `#33302b`. The accent lightens rather than saturates:
`--clay` `#d98b66` with `--on-clay` `#171614`. Measured on `--paper`: `--ink`
15.8:1, `--ink-muted` 7.1:1, `--ink-subtle` 5.6:1, `--clay` 6.8:1, `--ok`
8.5:1, `--warn` 9.2:1.

Both roots set `color-scheme`, so the browser paints its own furniture —
scrollbars, form controls, the canvas behind the page — to match.

The attribute is written by `lib/theme.tsx` and, before first paint, by a small
inline script in `app/layout.tsx`. **That script is the one place this app
injects one**, and it is not decoration: the attribute has to be on `<html>`
before the browser paints, and the earliest React can run is after hydration —
several hundred milliseconds of white for someone who chose dark. It reads
storage, sets one attribute, and stops.

Adding a `prefers-color-scheme` rule back is a change to this section first.

## No component library

Hand-rolled primitives, deliberately. The set above is small enough that a
library would cost more in bundle size, API surface and version churn than it
saves, and B-004 already chose CSS Modules over Tailwind for this codebase.

**Adding any UI library — Radix, MUI, shadcn, Chakra — requires a DECISIONS entry
first.** Complex behaviour that genuinely warrants one (a combobox, a date
picker, a focus-trapped dialog) is the honest trigger; wanting a nicer button
is not. Note that the disclosure pattern above is a native `<details>` and needed
no library at all.
