/**
 * The motion kit (docs/design/system.md §5). Components never write their own curves or
 * durations: they take these presets. `MotionConfig reducedMotion="user"` (in main.tsx) turns
 * transforms into fades for people who ask for less motion; CSS covers the loops.
 */
import type { Transition, Variants } from 'motion/react'

/** Durations in seconds (motion) and milliseconds (timers). */
export const dur = { 1: 0.12, 2: 0.2, 3: 0.32, 4: 0.48, 5: 0.9, chunk: 0.16 } as const
export const ms = { 1: 120, 2: 200, 3: 320, 4: 480, 5: 900, chunk: 160 } as const

type Bezier = [number, number, number, number]
export const ease: Record<'out' | 'inOut' | 'in' | 'spring', Bezier> = {
  out: [0.22, 1, 0.36, 1],
  inOut: [0.65, 0, 0.35, 1],
  in: [0.55, 0, 1, 0.45],
  /** Overshoot: only the send button's press and landing (rule 4). */
  spring: [0.34, 1.56, 0.64, 1],
}

/** Between siblings; at most six are staggered. */
export const STAGGER_S = 0.04
export const STAGGER_MS = 40
export const MAX_STAGGERED = 6
export function staggerDelay(index: number): number {
  return Math.min(index, MAX_STAGGERED - 1) * STAGGER_S
}

/** Honest motion (rule 5): a fast step stays on screen this long, within a per-turn budget. */
export const STEP_DWELL_MS = 240
export const STEP_DWELL_BUDGET_MS = 400

/** Leaving is 0.75x the entrance. */
function exitOf(seconds: number): Transition {
  return { duration: seconds * 0.75, ease: ease.in }
}

export const t = {
  /** Meters under reduced motion jump to their value. */
  instant: { duration: 0 } satisfies Transition,
  press: { duration: dur[1], ease: ease.out } satisfies Transition,
  fade: { duration: dur[2], ease: ease.out } satisfies Transition,
  enter: { duration: dur[3], ease: ease.out } satisfies Transition,
  move: { duration: dur[4], ease: ease.inOut } satisfies Transition,
  sheet: { duration: dur[4], ease: ease.out } satisfies Transition,
  meter: { duration: dur[5], ease: ease.out } satisfies Transition,
  chunk: { duration: dur.chunk, ease: ease.out } satisfies Transition,
  land: { duration: dur[3], ease: ease.spring } satisfies Transition,
  /** The quota delta rising from the ring and fading. */
  float: { duration: 1.5, ease: ease.out, times: [0, 0.25, 1] } satisfies Transition,
} as const

export type Preset = {
  name: string
  use: string
  initial: Record<string, number | string>
  animate: Record<string, number | string>
  exit: Record<string, number | string>
  transition: Transition
  exitTransition: Transition
}

/** Enter/exit pairs. `/design` has a replay button for each. */
export const presets = {
  rise: {
    name: 'rise',
    use: 'Turns, step rows, the receipt: 8px rise, fade and a 2px blur',
    initial: { opacity: 0, y: 8, filter: 'blur(2px)' },
    animate: { opacity: 1, y: 0, filter: 'blur(0px)' },
    exit: { opacity: 0, y: 4 },
    transition: t.enter,
    exitTransition: exitOf(dur[3]),
  },
  pop: {
    name: 'pop',
    use: 'Popovers and tooltips: 4px rise and fade',
    initial: { opacity: 0, y: -4, scale: 0.98 },
    animate: { opacity: 1, y: 0, scale: 1 },
    exit: { opacity: 0 },
    transition: t.fade,
    exitTransition: exitOf(dur[2]),
  },
  sheetRight: {
    name: 'sheetRight',
    use: 'The inspector from the right: 24px slide and fade',
    initial: { opacity: 0, x: 24 },
    animate: { opacity: 1, x: 0 },
    exit: { opacity: 0, x: 24 },
    transition: t.sheet,
    exitTransition: exitOf(dur[4]),
  },
  sheetUp: {
    name: 'sheetUp',
    use: 'The inspector on a phone: from the bottom',
    initial: { opacity: 0, y: 40 },
    animate: { opacity: 1, y: 0 },
    exit: { opacity: 0, y: 40 },
    transition: t.sheet,
    exitTransition: exitOf(dur[4]),
  },
  toast: {
    name: 'toast',
    use: 'Toasts above the composer: 12px rise',
    initial: { opacity: 0, y: 12 },
    animate: { opacity: 1, y: 0 },
    exit: { opacity: 0, y: 8 },
    transition: t.enter,
    exitTransition: exitOf(dur[3]),
  },
  fade: {
    name: 'fade',
    use: 'Scrims, chips, labels that change',
    initial: { opacity: 0 },
    animate: { opacity: 1 },
    exit: { opacity: 0 },
    transition: t.fade,
    exitTransition: exitOf(dur[2]),
  },
  swap: {
    name: 'swap',
    use: 'A step label going from running to its result',
    initial: { opacity: 0, y: 4 },
    animate: { opacity: 1, y: 0 },
    exit: { opacity: 0, y: -4 },
    transition: { duration: dur[2], ease: ease.out },
    exitTransition: { duration: dur[1], ease: ease.in },
  },
} satisfies Record<string, Preset>

export type PresetName = keyof typeof presets

/** Props for a `motion.*` element from a preset, with an optional stagger index. */
export function motionProps(name: PresetName, index = 0) {
  const p: Preset = presets[name]
  return {
    initial: p.initial,
    animate: { ...p.animate, transition: { ...p.transition, delay: staggerDelay(index) } },
    exit: { ...p.exit, transition: p.exitTransition },
  }
}

export const staggerChildren: Variants = {
  show: { transition: { staggerChildren: STAGGER_S } },
}
