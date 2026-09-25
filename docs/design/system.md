# Ink: the 2nd Mind design system

**Status: approved (Sprint 2.5, 25 Sep 2026).** This file is the contract for every
screen. Tokens live in `frontend/src/styles/tokens.css`, primitives in `frontend/src/ui/`, and
the living reference is the `/design` route. If this file and the code disagree, fix one of them
in the same PR.

Ink is dark only, monochrome, flat and chat-first. The conversation is the product; the agent's
work is shown inside it, step by step, as it happens.

---

## 1. Principles

1. **The conversation is the product.** Every other view is a layer over it: a sheet, a
   popover, a page reached from it. Nothing competes with the composer for attention.
2. **Show the work.** Every agent step appears as it happens, in plain words first. The
   technical detail is one click down, never on the surface and never hidden.
3. **Monochrome with meaning.** Greys carry hierarchy. Colour appears only when it means
   something: saved, waiting for you, refused. If removing the colour loses no information,
   remove it.
4. **Flat and tactile.** Surfaces are separated by tone and hairlines, not shadows. The only
   shine is the chrome gradient, and it is rationed.
5. **Nothing jumps.** Every state change is animated from where it is. Nothing appears or
   leaves without a transition, text never reflows under the reader, and motion never fakes
   work.
6. **Where you'd expect it.** Brand and workspace top left. Identity and quota top right. The
   composer at the bottom. Detail opens to the right (or up, on a phone).
7. **Show only what exists.** No "coming soon", no disabled nav for future features. The nav
   grows when the feature ships.

---

## 2. Colour

Dark only in Phase 1. Neutrals carry a slight cool bias (hue ≈ 240), so the greys read as
metal rather than mud. Contrast ratios are WCAG 2.1, measured against each surface.

### Surfaces and lines

| Token | Hex | Use |
|---|---|---|
| `canvas` | `#08080A` | App background. Never pure `#000`. |
| `surface` | `#101013` | Raised: composer, step detail, cards, top bar on scroll |
| `surface-2` | `#17171B` | Hover, inputs, chips, avatar fill |
| `surface-3` | `#1F1F24` | Overlays: popovers, sheets, toasts, pressed states |
| `line` | `#26262C` | Hairlines and dividers (decorative, 1px) |
| `line-strong` | `#3A3A42` | Secondary button outline, ring track, the trail's rail |
| `line-control` | `#66666F` | Input and toggle borders (≥ 3:1 on canvas and surface, WCAG 1.4.11) |

### Text

| Token | Hex | Min contrast | Use |
|---|---|---|---|
| `fg` | `#F4F4F6` | 14.9 : 1 | Primary text, answers, active step labels |
| `fg-2` | `#B4B4BC` | 7.9 : 1 | Secondary text, user message meta, finished step labels |
| `fg-3` | `#8A8A94` | 4.8 : 1 | Meta, captions, timings, placeholders |
| `fg-4` | `#56565F` | 2.3 : 1 | Disabled and decorative only. **Never for text that must be read.** |
| `inverse` | `#F4F4F6` | | Primary button fill |
| `on-inverse` | `#08080A` | 18.2 : 1 | Text and icons on `inverse` |

### Meaning (the only hues)

| Token | Hex | Min contrast | Means | Tint (fills) |
|---|---|---|---|---|
| `ok` | `#86D9AE` | 9.8 : 1 | Saved, added, allowed, fulfilled | `ok` at 10% |
| `warn` | `#E8C170` | 9.6 : 1 | Held for you, needs a decision, quota < 25% | `warn` at 10% |
| `bad` | `#F08A80` | 6.8 : 1 | Refused, removed, failed, quota < 10% | `bad` at 10% |

- Meaning colours appear as **dots, 1px rules, icons, op glyphs and short labels**. Never as a
  large fill. Tints are capped at 10% alpha.
- Superseded or removed content is `fg-3` with a strikethrough, not red. Red is for things
  that went wrong or were refused.
- There is no brand hue. White is the accent.

### Chrome

`--gradient-chrome: linear-gradient(135deg, #FFFFFF 0%, #9A9AA3 38%, #F4F4F6 52%, #6E6E78 100%)`

A brushed-metal gradient, the one flourish. Allowed on exactly four things: the brand mark,
the quota ring's arc, the primary button's hover sheen, and the "live" shimmer on a running
step. Adding a fifth needs a change to this file.

### Texture

The canvas has two fixed, non-interactive layers: a top-centre glow
(`radial-gradient` of white at 3.5%) and film grain (SVG noise at 3%). Nothing else gets
texture. Both are removed in forced-colors mode.

---

## 3. Typography: three voices

Each face has one job, and the job is who is speaking.

| Role | Face | Speaks for | Loaded as |
|---|---|---|---|
| **Voice** | Instrument Serif (regular, italic) | The product, at moments that matter: greetings, empty states, page titles, the big number in a popover | `@fontsource/instrument-serif` |
| **Interface** | Instrument Sans (variable: wght 400–700, wdth 75–100) | Everything you read and press: answers, messages, labels, buttons | `@fontsource-variable/instrument-sans` |
| **Machine** | Geist Mono (variable: wght 400–600) | Anything the system produced for inspection: ids, scores, ms, tokens, cost, rule ids, prompt versions, tool arguments | `@fontsource-variable/geist-mono` |

Fonts are **self-hosted** through `@fontsource`. No request to a third-party font host.
Fallbacks: `ui-serif, Georgia, serif` / `ui-sans-serif, system-ui, sans-serif` /
`ui-monospace, SFMono-Regular, Menlo, monospace`.

**Mono means machine.** If a human would say it, it is not mono. If the system computed it
for you to inspect, it is.

### Scale

| Token | Face | Size / line | Tracking | Weight | Use |
|---|---|---|---|---|---|
| `display` | Voice | 44 / 48 | −0.02em | 400 | Empty-state greeting, landing |
| `title-lg` | Voice | 28 / 34 | −0.01em | 400 | Page and sheet titles |
| `title` | Interface | 17 / 24 | −0.01em | 600 | Section heads, card titles |
| `prompt` | Interface | 20 / 30 | −0.01em | 500 | The user's message in a turn |
| `answer` | Interface | 17 / 28 | 0 | 400 | The assistant's reply. Max 64ch. |
| `body` | Interface | 15 / 24 | 0 | 400 | UI default |
| `label` | Interface | 14 / 20 | 0 | 500 | Buttons, step labels, chips |
| `overline` | Interface | 11 / 16 | +0.12em | 600 | Uppercase section labels (`MEMORY DIFF`) |
| `mono` | Machine | 13 / 20 | 0 | 400 | Technical detail |
| `mono-sm` | Machine | 12 / 16 | 0 | 400 | Receipts, timings, chips in the trail |

- Minimum size is 12px, except `overline` (11px, uppercase, 600) which is never the only
  carrier of meaning.
- Numbers that line up use `tabular-nums`. Headings use `text-wrap: balance`, answers use
  `text-wrap: pretty`.
- Sentence case everywhere except `overline`.

---

## 4. Space, radius, surfaces

**Space** is a 4px grid. Allowed steps: 4, 8, 12, 16, 20, 24, 32, 40, 56, 72. Turns are
separated by 56. A step row is 36 tall. Page gutter is 16 (phone) / 24 (tablet) / 32 (desktop).

**Radius**

| Token | Value | Use |
|---|---|---|
| `radius-xs` | 6px | Tags, kbd, inline code, citation chips |
| `radius-sm` | 10px | Inputs, small cards, menu items |
| `radius-md` | 14px | Step detail, cards, popovers |
| `radius-lg` | 20px | Composer, sheets, the app frame on large screens |
| `radius-full` | 999px | Buttons, chips, avatar, status dots |

A radius nested inside padding is the outer radius minus the padding.

**Surfaces.** Flat: no drop shadows for hierarchy. Raised surfaces get a 1px `line` border and
a top light edge (`inset 0 1px 0 rgb(255 255 255 / 0.05)`). Only overlays (popover, sheet,
toast) get `--shadow-overlay: 0 24px 64px -16px rgb(0 0 0 / 0.7)`, because they genuinely sit
above the page.

---

## 5. Motion

Nothing moves with a jerk. Motion explains where something came from and where it went.

| Token | Value | Use |
|---|---|---|
| `dur-1` | 120ms | Press, colour and border changes |
| `dur-2` | 200ms | Hover lift, fades, tooltips, chip changes |
| `dur-3` | 320ms | Expand and collapse, step enter, rail draw, send |
| `dur-4` | 480ms | Sheets, drawers, layout moves |
| `dur-5` | 900ms | Meters and count-ups |
| `ease-out` | `cubic-bezier(0.22, 1, 0.36, 1)` | Anything entering or growing (default) |
| `ease-in-out` | `cubic-bezier(0.65, 0, 0.35, 1)` | Anything moving from A to B |
| `ease-in` | `cubic-bezier(0.55, 0, 1, 0.45)` | Anything leaving, at 0.75× its entrance duration |
| `stagger` | 40ms | Between siblings, at most 6 staggered |

**Rules**

1. Animate only `transform`, `opacity`, `filter`, `clip-path` and `grid-template-rows` (for
   height). Never `top`, `left`, `width`, `height` or `margin`. Never `transition: all`.
2. Everything that appears has an entrance; everything that leaves has an exit
   (`AnimatePresence`). Layout shifts are animated (`layout`) or prevented by reserving space.
3. Transitions are interruptible: hovering off mid-animation reverses from where it is.
4. No bounce or overshoot on chrome. One exception: the send button's press.
5. **Honest motion.** Animations never pretend work is happening. A step is "running" only
   while the server says so. Fast steps are held on screen for at least 240ms so they can be
   read, but the total delay this adds to a turn is capped at 400ms; after that, remaining
   steps resolve together on the stagger.
6. Streaming text fades in by chunk (160ms), never types letter by letter. The view follows
   new content only while the reader is at the bottom; scrolling up stops the follow and
   shows a "Jump to latest" chip.
7. Loops (shimmer, pulse) exist only on something that is actually running, and stop the
   moment it ends.
8. **Reduced motion** (`prefers-reduced-motion: reduce`): transforms become 120ms opacity
   fades, loops stop (a running step shows a static "…" instead of the shimmer), meters jump to
   their value, and the rail appears already drawn.

Library: `motion` (`motion/react`) for presence, layout and springs. CSS transitions for
hover and press. The presets live in `frontend/src/ui/motion.ts`; components never write
their own curves or durations.

---

## 6. Icons

Lucide (`lucide-react`), 1.5px stroke, at 16px (inline, trail) or 20px (buttons, bar).
Outline only; the only filled shapes are status dots. Icons are `currentColor` and take the
text colour of their row. An icon-only control always has an `aria-label` and a tooltip.

---

## 7. Layout

```
┌────────────────────────────────────────────────────────────────────────┐
│ ◐ 2nd Mind   My memory ▾                  Claude Sonnet 5 1× ▾  (◯ RP) │  top bar, 56px
│                                                                          │
│                 ┌── conversation column, max 720px ──┐                   │
│                 │ YOU · 10:42                         │                   │
│                 │ Watched Severance last night…       │  prompt           │
│                 │  ● Understood: something to save    │  the trail        │
│                 │  ● Found 2 things to remember       │                   │
│                 │  ● Saved 1 · updated 2              │                   │
│                 │ Noted. Severance is marked as…      │  answer, 64ch max │
│                 │ 2.14 s · 3,482 tok · $0.0061  ↶ ⧉   │  receipt          │
│                 └─────────────────────────────────────┘                   │
│                 ╭─────────────────────────────────────╮                   │
│                 │ Tell me anything…               (↑) │  composer         │
│                 ╰─────────────────────────────────────╯                   │
└────────────────────────────────────────────────────────────────────────┘
```

- **Top bar** (56px): transparent over the canvas; gains `surface` at 80% with a 16px backdrop
  blur and a bottom hairline once content scrolls under it. Left: brand mark, wordmark,
  workspace switcher. Right: the model picker (the model, its quota weight such as `2.5×`, and
  a `FAKE` tag when the fake provider stands in; an icon replaces the name below `sm`), then
  the avatar with its quota ring. With no picker configured, the provider-mode tag shows
  instead (only when not `live`). Nav tabs (Upcoming, Memory, Settings) appear left of the avatar in the
  sprint that ships each one.
- **Conversation column**: centred, max 720px. Answers cap at 64ch inside it.
- **Composer**: floats 16px above the bottom edge, same width as the column, over a fade from
  transparent to `canvas`.
- **Inspector** (the full glass box for one turn): a 460px sheet from the right. At ≥ 1440px
  it docks and the conversation re-centres in the remaining space. Below 768px it is a bottom
  sheet at 90% height.
- **Breakpoints**: `sm` 640, `md` 768, `lg` 1024, `xl` 1280, `2xl` 1440. Works from 360px up
  (NFR-8.1). The page never scrolls sideways; wide tables scroll in their own container.

---

## 8. The Trail: how the agent's work is shown

The Trail replaces the side-panel glass box as the primary view of what the agent did
(FR-9). It sits between the user's message and the reply.

### Anatomy of a step row (36px)

`[node] [icon] Friendly label · result chips ........................ 118 ms [›]`

- **Node** on a 1px vertical rail (`line-strong`). The rail draws down to the next node as
  each step starts (`scaleY`, `dur-3`).
- **Label**: present tense while running ("Checking what I already know"), past-tense result
  when done ("Updates 2 memories you had").
- **Chips**: up to three `mono-sm` facts (`save · 0.94`, `+1 ~2`).
- **Timing**: `mono-sm`, `fg-3`. Counts up live while running, freezes when done.

### States

| State | Node | Label | Extra |
|---|---|---|---|
| queued | not shown | not shown | Steps appear only when they start |
| running | 8px ring, pulsing | `fg`, chrome shimmer sweeps across it | Timer counts up |
| done | 8px filled `fg-2` dot | `fg-2`, past tense | |
| held | `warn` dot | "Held 1 change for your OK" | Confirm / Reject inline, auto-expanded |
| refused | `bad` dot | "Refused: that looks like a password" | Auto-expanded |
| failed | `bad` ring with × | "Couldn't finish this step" | Error code in the technical layer |

### Two layers

Clicking a row (or Enter/Space on it) expands it (`grid-template-rows` 0fr → 1fr, `dur-3`).

1. **What happened**: one or two plain sentences in the product's voice. "I read 'last night'
   as Thursday 24 September, because it's Friday today in Asia/Kolkata."
2. **Under the hood**: the technical detail in Machine type. Key/value rows, small tables,
   the model and prompt version, tokens and ms, policy rule ids, scores. Never system prompts
   or raw model output (FR-9.3).

### Lifecycle

- **Live**: rows enter one by one as the server reports them (`step.started`).
- **Finished, latest turn**: the Trail stays open.
- **Finished, older turns**: when a new turn starts, the previous Trail folds into one
  summary row, "7 steps · 2.1 s · saved 1, updated 2", which expands back.
- **Reload**: the Trail renders from stored events only (FR-9.2). A reloaded turn looks
  exactly like a finished live one.

### Step catalogue

Every step the backend can emit has one entry in `frontend/src/trail/steps.ts`, typed as
`Record<AgentStep, StepSpec>`. A new backend step without an entry fails the type check.

| Step | Icon | While running | When done (example) | Detail comes from |
|---|---|---|---|---|
| `understand` | `scan-text` | Reading your message | Understood: something to save | `intent` |
| `extract` | `list-tree` | Picking out what to remember | Found 2 things to remember | `decision.classifications` |
| `dates` | `calendar-clock` | Working out dates | "last night" → Thu 24 Sep | `decision.time_resolutions` |
| `entities` | `users` | Recognising people, places and things | Nisha → your sister | `decision.entity_resolutions` |
| `reconcile` | `git-compare` | Checking what I already know | Updates 2 memories you had | `decision.reconciliations` |
| `enrich` | `tags` | Making it findable later | Added 6 search keys | keys written, `model_call` enrich / embed |
| `guard` | `shield-check` | Checking it's safe to keep | 3 changes allowed | `tool_call.policy`, `policy` |
| `save` | `database` | Saving | Saved 1 · updated 2 | `memory_diff` |
| `answer` | `message-square-text` | Writing the reply | Replied in 0.6 s | `model_call` answer |
| `undo` | `undo-2` | Reverting that turn | Reverted 2 memories | `memory_diff` (undo_of) |
| `confirm` | `check-check` | Applying the change you approved | Applied 1 change | `memory_diff` |
| `plan` *(S3)* | `route` | Working out what you're asking | 1 question · latest | `retrieval` plan |
| `search` *(S3)* | `search` | Searching your memory | 14 found in 3 ways | `retrieval.candidates` |
| `rank` *(S3)* | `list-ordered` | Picking what's relevant | Kept the top 3 | rerank scores |
| `triggers` *(S3)* | `bell` | Checking reminders tied to this | 1 reminder fired | trigger diff entries |
| `fetch` *(S4)* | `globe` | Reading the link | Read 1,240 words | `tool_call` web.fetch |

Only steps that ran are shown. A chit-chat turn is two rows: understand, answer.

### Where the five glass-box panels went

| FR-9 panel | Inline (the Trail) | Inspector |
|---|---|---|
| 1 Decision | `understand`, `extract`, `dates`, `entities`, `reconcile` | Decision panel |
| 2 Memory diff | `save` (also `undo`, `confirm`) | Memory diff panel |
| 3 Retrieval | `plan`, `search`, `rank` (S3) | Retrieval panel |
| 4 Tool calls | `guard` (and `fetch`) | Tool calls panel |
| 5 Timing & cost | The receipt row under the answer | Timing & cost with a waterfall |

---

## 9. Components

Every feature composes these from `frontend/src/ui/`. A feature that needs something new adds
it to `ui/` and to `/design` first.

| Component | Variants and rules |
|---|---|
| `Button` | `primary` (inverse fill, black label, chrome sheen on hover), `secondary` (1px `line-strong`), `ghost`, `danger` (`bad` label, `bad` tint on hover). Pill. Heights 32 / 40 / 48. Hover lifts 1px (`dur-2`); press scales to 0.97 (`dur-1`). One `primary` per view. |
| `IconButton` | Circle, 32 / 40. `aria-label` and a tooltip are required. |
| `SendButton` | 36px circle, inverse. On send the arrow lifts out and a new one rises in (`dur-3`, slight overshoot). While sending it shows a spinning chrome arc. |
| `Chip` | Pill, `surface-2`, `label` or `mono-sm`. Optional leading status dot. |
| `Tag` | `radius-xs`, `overline` type: layers (`CORE`, `QUICK`, `ARCHIVE`), tiers. |
| `Disclosure` | The expand/collapse primitive behind step rows and panels. Animated height via grid rows. |
| `Select` | A 32px pill (label, then a chevron) that opens a grouped listbox in a popover, 320px wide. Groups have an `overline` head; options are 44px rows: a check for the selected one, the label, an optional note in `fg-3` and a trailing Machine-type fact. Disabled options sit at 40% and can't be picked. ↑ ↓ Home End move, Enter or Space picks, Esc or Tab closes, and focus returns to the pill. |
| `Popover`, `Tooltip` | `surface-3`, `radius-md`, overlay shadow. Enter: 4px rise + fade (`dur-2`). Tooltip delay 400ms. |
| `Sheet` | Inspector and mobile drawers. Enter: 24px slide + fade (`dur-4`). Focus-trapped, Esc closes. |
| `Toast` | Bottom centre above the composer. One at a time, 4s, with an action where one exists ("Undo"). |
| `QuotaRing` | 40px ring around the 32px avatar. Arc = **remaining** quota in chrome; `warn` below 25%, `bad` below 10%. Animates to each new value (`dur-5`); the spent amount floats up from it and fades ("−2.9k"). Click opens the quota popover. |
| `Kbd` | `radius-xs`, `mono-sm`, 1px `line-strong`. |
| `Skeleton` | `surface-2` block with a slow sheen. Only for content that is loading, never for steps. |
| `CountUp` | Numbers that change animate between values (`dur-5`, tabular). |
| `DiffEntry` | Op glyph in Machine type: `+` ok, `~` fg, `−` bad, `⏸` warn (held), `∅` fg-3 (not written). Superseded rows strike through. |

---

## 10. Words

- The agent speaks in the first person ("I saved…"). The interface speaks plainly and names
  things by what people recognise ("Your memory", not "workspace").
- Sentence case. No exclamation marks. No "Oops". Errors say what happened and what to do.
- Numbers: `3,482 tokens`, `$0.0061`, `2.14 s`, `320 ms`, `12 Sep`, `Thu 24 Sep`. Relative
  times under a day ("4 min ago"), dates after.
- A step's live label is what it is doing; its done label is what it found.

---

## 11. Accessibility

- WCAG 2.1 AA. Every text/surface pair in §2 is at or above 4.5 : 1 except `fg-4`, which
  never carries text that must be read. Control borders use `line-control` (≥ 3 : 1).
- Focus ring: 2px `fg` outline, 2px offset, on every focusable element, `:focus-visible` only.
- Hit targets ≥ 40px on desktop and ≥ 44px on touch.
- The Trail is a list; each row is a `button` with `aria-expanded`. A polite live region
  announces step results ("Saved 1, updated 2"), throttled. Tokens are not announced one by
  one; the finished reply is.
- Keyboard: `/` focuses the composer, `F6` toggles the inspector, `Esc` closes the top
  overlay, `↑ ↓` move between step rows, `Enter` expands.
- Reduced motion as in §5. Forced colours: texture and gradients are removed, dots get
  outlines.

---

## 12. How the system is enforced

1. **One tokens file.** `tokens.css` defines every colour, font, size, radius, duration and
   easing, and exposes them to Tailwind v4 through `@theme`. Tailwind's default palette,
   radii, shadows and fonts are reset (`--color-*: initial` and friends), so `bg-slate-100`
   doesn't exist.
2. **`npm run check:design`** (part of `make check`) fails on: hex or `rgb()` literals outside
   `tokens.css`; Tailwind palette names; arbitrary values (`[13px]`, `[#fff]`); raw durations
   or easings; `transition-all`; inline `style` colours.
3. **ESLint**: raw `<button>`, `<input>`, `<textarea>` and `<select>` are allowed only inside
   `src/ui/`. Features use the primitives.
4. **Typed step catalogue**: `AgentStep` is an enum in the OpenAPI schema;
   `Record<AgentStep, StepSpec>` makes a missing entry a compile error.
5. **`/design`** renders every token and every primitive in every state. Playwright visits it
   and runs axe, and a unit test checks the contrast of every text/surface pair from the
   tokens.
6. **`CLAUDE.md`** carries the short version of these rules and points here.

## 13. Adding UI: the checklist

- [ ] Built from `src/ui/` primitives and tokens only; `check:design` is clean.
- [ ] New primitive or variant? It's on `/design` with all its states.
- [ ] New agent step? It has a `steps.ts` entry: icon, running label, done label, both
      detail layers.
- [ ] Every appearing or leaving element has an entrance and an exit; nothing reflows under
      the reader.
- [ ] Works at 360px, with the keyboard, and with reduced motion.
- [ ] Technical values are in Machine type; prose never is.
