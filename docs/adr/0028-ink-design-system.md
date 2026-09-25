# ADR-0028: Ink design system

- **Status:** accepted
- **Date:** 2026-09-25

## Context

After S2 the UI read like a debug page: a chat on the left and a dense glass box open beside it
(PRD §7.9 as first written). S3 adds retrieval, the heaviest panel the glass box will ever
show, and every later sprint adds screens. Without one set of rules each screen picks its own
greys, spacing and motion, and the signature feature (showing the agent's work) stays a side
panel nobody reads while the reply streams.

## Decision

The rulebook is `docs/design/system.md` ("Ink"). What it fixes:

- **Dark only** for Phase 1. Monochrome; colour only for ok / warn / bad.
- **One tokens file**, `frontend/src/styles/tokens.css`, exposed through Tailwind v4 `@theme`
  with Tailwind's default colours, radii, shadows and fonts reset, so palette classes such as
  `bg-slate-100` compile to nothing. `npm run check:design` (in `make check`) rejects hex
  literals, palette names, arbitrary values, raw durations and `transition-all` outside the
  tokens file, checks every text/surface contrast pair, and holds the bundle budget.
- **Three self-hosted faces** through `@fontsource`: Instrument Serif (voice), Instrument Sans
  (interface), Geist Mono (machine). Latin and latin-ext only; no font CDN.
- **`motion`** (`motion/react`) for presence, layout and springs, with presets in
  `src/ui/motion.ts`; CSS transitions for hover and press. `MotionConfig reducedMotion="user"`.
- **Lucide** (`lucide-react`) icons, outline, 1.5px.
- **Primitives** in `src/ui/`; ESLint allows raw `<button>`, `<input>`, `<textarea>` and
  `<select>` only there. `/design` renders every token and primitive in every state.
- **The glass box moves inline as the Trail**: each turn shows its agent steps live, in plain
  words, each expanding into the technical detail. An **Inspector** sheet shows the whole glass
  box (the five FR-9 panels) for one turn. PRD §7.9 and §10 are reworded to match; FR-9.1 to
  9.6 keep their meaning.

New dependencies, and nothing else: `motion`, `lucide-react`, `@fontsource/instrument-serif`,
`@fontsource-variable/instrument-sans`, `@fontsource-variable/geist-mono`, and (dev)
`@axe-core/playwright`.

## Alternatives considered

- **A component library (Radix, shadcn/ui, MUI):** faster start, but brings its own look and a
  large surface to override; our primitives are few and small, and owning them keeps the
  bundle under budget.
- **Keep the side panel, restyled:** cheaper, but the work stays out of sight while the reply
  streams, which is the moment it is most interesting.
- **A light theme as well:** doubles the contrast work and the screenshots for no Phase 1 need.
- **Google Fonts:** a third-party request on every load and a privacy note to write.

## Consequences

- Later sprints add UI by composing primitives and tokens; `check:design` and ESLint catch drift.
- The Trail needs the server to report steps (ADR-0029); a new backend step needs a
  `steps.ts` entry or the type check fails.
- The rulebook and the code must change together; `/design` is where a reviewer sees both.
- Revisit if a light theme or a second brand is needed, or if the bundle budget can't hold.
