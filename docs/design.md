# Design system — data-agent

Binding for all UI, the way `architecture.md` is binding for the system. If a
screen needs something not defined here, add it here first, in the same PR.

## The feel

Simple, modern, soft. Light backgrounds, generous space, rounded cards that lift
off the page with a soft shadow rather than a border. One friendly primary
colour; soft pastel accents carry meaning, never decoration. Closer to a modern
fintech app than to an enterprise console.

**Deliberately not:** dense grey tables, hairline borders everywhere, information
crammed to the edges, or a UI that signals "internal tool".

Three rules that decide most arguments:

1. **Space is the layout.** Reach for spacing before borders, and for a border
   only when spacing cannot express the grouping.
2. **Colour means something.** Grey is the default; a hue is a claim that this
   thing has a state or a category. Never colour for interest.
3. **One primary action per view.** Everything else is quieter.

## Tokens

Defined once in `apps/web/src/app/globals.css` as CSS custom properties. Use the
token, never a raw hex value — a colour that appears in a component file is a bug.

### Colour

| Token | Light | Purpose |
|---|---|---|
| `--bg` | `#f7f8fa` | page background — never pure white, so cards can lift off it |
| `--surface` | `#ffffff` | cards, inputs, menus |
| `--surface-sunken` | `#f1f3f7` | wells, table headers, empty states |
| `--fg` | `#101828` | primary text |
| `--fg-muted` | `#667085` | secondary text, labels |
| `--fg-subtle` | `#98a2b3` | placeholders, disabled |
| `--border` | `#eaecf0` | hairlines, used sparingly |
| `--primary` | `#5b5bd6` | the one friendly primary — indigo/violet |
| `--primary-hover` | `#4a4ac4` | |
| `--primary-soft` | `#eeeefc` | primary-tinted fills |
| `--focus-ring` | `#c7c7f5` | 3px outline on focus-visible |

**Pastel accents** — for categories, roles and status. Each is a `-soft`
background with a readable `-strong` foreground; never use `-soft` for text.

| Token pair | Hue | Used for |
|---|---|---|
| `--accent-mint-soft` / `-strong` | green | success, healthy, admin |
| `--accent-sky-soft` / `-strong` | blue | informational, contributor |
| `--accent-lilac-soft` / `-strong` | violet | neutral category, reader |
| `--accent-peach-soft` / `-strong` | amber | warning, pending |
| `--accent-rose-soft` / `-strong` | red | error, denied, destructive |

Semantic aliases (`--ok`, `--warn`, `--danger`) point at the `-strong` values so
status code never names a hue directly.

**Chart series** — a separate, fixed-order categorical ramp, `--chart-cat-1`
through `--chart-cat-8`. It exists because rule 2 cuts both ways: the pastels
above *mean* something — success, information, warning, error — so painting a
category with them makes a rose bar read as a failure. A hue that means two
things means neither.

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

Four rules, none of them taste:

1. **Fixed order, never cycled.** Slot 8 is the cap the server enforces
   (`charts.MAX_SERIES`); a ninth series would repeat slot 1, and two series
   wearing one colour is a chart that lies. Past eight the honest answer is a
   filter, a facet, or the table.
2. **Colour follows the entity, not its rank.** A filter that removes a series
   must not repaint the survivors.
3. **A chart with one series uses `--primary`**, not slot 1 — the categorical
   range applies only where colour carries a split. Colour for a lone series
   would be decoration, which rule 2 above forbids.
4. **Validated as a set, not chosen.** Both columns pass lightness band, chroma
   floor, colour-vision separation on adjacent pairs and normal-vision
   separation, against the card surface in their own mode. Light slots 3, 4 and
   5 fall below 3:1 contrast on white; the legend and the always-available
   result table are what relieve that, so a chart must never carry identity by
   colour alone. Re-run the check before changing any value here.

The dark column is the same eight hues stepped for the dark surface, not a
second palette. Slot 1 is a shade below `--primary` there, because the dark
primary sits just outside the lightness band the set was validated in.

### Spacing

A 4px scale: `--space-1` 4px · `-2` 8px · `-3` 12px · `-4` 16px · `-5` 24px ·
`-6` 32px · `-7` 48px · `-8` 64px. Nothing between steps.

Card padding is `--space-5`; card-to-card gap `--space-4`; section gap
`--space-7`. Page gutter `--space-5`, growing to `--space-7` above 768px.

### Radius and shadow

`--radius-sm` 8px (inputs, badges) · `--radius-md` 12px (buttons) ·
`--radius-lg` 16px (cards) · `--radius-full` 999px (pills, avatars).

`--shadow-sm` for resting cards, `--shadow-md` on hover/raised, `--shadow-none`
when a card sits inside another surface. Shadows are soft and low-contrast:
large blur, small offset, never a hard drop.

### Typography

System font stack — no webfont, because downloading one at build time makes
`docker build` depend on a third-party CDN (settled in WP0.3).

| Token | Size / line-height / weight | Use |
|---|---|---|
| `--text-display` | 32 / 40 / 600 | page title, one per view |
| `--text-title` | 20 / 28 / 600 | card and section titles |
| `--text-body` | 15 / 24 / 400 | default |
| `--text-small` | 13 / 20 / 400 | secondary detail |
| `--text-label` | 12 / 16 / 600, `0.04em`, uppercase | field labels, card eyebrows |

Long-form text caps at ~68 characters. Numbers in tables use
`font-variant-numeric: tabular-nums` so columns align.

## Components

Primitives live in `apps/web/src/components/ui/`, one file each, styled with a
co-located CSS Module. They are the only place a token is consumed for layout
chrome; feature components compose them.

- **Card** — `--surface`, `--radius-lg`, `--shadow-sm`, padding `--space-5`. No
  border by default. `tone="sunken"` for nested surfaces.
- **Button** — `primary` (filled), `secondary` (surface + hairline), `ghost`
  (transparent), `danger` (rose). Height 40px, radius `--radius-md`, padding
  `0 --space-4`, weight 600. Disabled drops to 50% opacity and keeps its size.
- **Input** — height 40px, `--surface`, 1px `--border`, radius `--radius-sm`.
  Focus swaps the border to `--primary` and adds a 3px `--focus-ring`. A label
  is `--text-label`; an error message is `--danger` at `--text-small`.
- **Badge** — pill, `-soft` background with `-strong` text, `--text-small`,
  padding `--space-1 --space-3`. Roles and statuses use it and nothing else.
- **PageHeader** — display title, optional muted subtitle, optional right slot
  for the single primary action.

### The shell (WP13.1b)

Every screen inside an organization renders inside `components/shell/`.

- **AppShell** — a flex row: the rail, then the page. The page gets
  `min-width: 0` so wide content (a result table, a SQL block) scrolls inside its
  own container instead of pushing the rail off screen. Below 768px the two stack
  and the rail gives its height back.
- **Sidebar** — `--surface-sunken`, 260px, sticky full height, collapsing to a
  60px icon rail. The one hairline in the design that earns its place is its
  right edge, because the two surfaces are close in value. New chat at the top,
  chats in the middle, the person and Settings at the bottom. Row controls
  (rename, archive) appear on `:hover` **and** `:focus-within` — hover alone
  would make them unreachable without a pointer.
  - The collapsed/expanded choice persists per browser via `lib/persisted.ts`.
- **Destructive wording is the true word.** The control that puts a chat away
  says **Archive**, and its confirmation says the chat can be brought back,
  because that is what happens (D-039). A trash icon that quietly archived would
  be a control whose word does not match its action — the same defect as a badge
  reading *answered* on a refusal. There is no delete in this product; if one is
  ever added it needs a DECISIONS entry, not an icon.
- **Unbuilt sections are shown as unbuilt.** A section that is planned and not
  built carries a `Coming soon` badge and prose, and **no controls at all** — not
  a disabled select, not a dead toggle. A control that looks operable and does
  nothing is a promise the product does not keep.

## Accessibility, non-negotiable

- Every interactive element has a visible `:focus-visible` ring; never
  `outline: none` without a replacement.
- Body and muted text meet WCAG AA on their backgrounds (`--fg-muted` on
  `--surface` is 4.6:1). Pastel `-soft` fills are backgrounds only.
- Colour never carries meaning alone — a status badge has a word in it.
- Targets are at least 40×40px.
- `prefers-reduced-motion` disables transitions.

## Dark mode

**Light is the default, and the operating system does not get a vote** (D-046).
Tokens are redefined under `[data-theme="dark"]` and under nothing else — there
is no `prefers-color-scheme` rule anywhere in `globals.css`, deliberately, so
there is exactly one route into the dark values and no combination of media
query and attribute that could disagree.

Surfaces lift instead of darkening (`--bg` `#0e1014`, `--surface` `#171a21`), and
pastels lose saturation rather than gaining it. Both roots also set
`color-scheme`, so the browser paints its own furniture — scrollbars, form
controls, the canvas behind the page — to match.

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
is not.
