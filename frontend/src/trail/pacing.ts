/**
 * Honest pacing for a live Trail (rulebook §5, rule 5). Rows appear only when the server starts
 * a step. A step that finishes fast stays "running" on screen for at least 240ms so it can be
 * read, but the delay this adds to a turn is capped at 400ms; after that, the remaining
 * changes resolve together on the stagger.
 */
import { useEffect, useState } from 'react'
import { STAGGER_MS, STEP_DWELL_BUDGET_MS, STEP_DWELL_MS } from '../ui/motion'
import type { StepView } from './model'

type Change = { index: number; kind: 'add' | 'finish' }

function nextChange(shown: readonly StepView[], target: readonly StepView[]): Change | null {
  for (let i = 0; i < target.length; i++) {
    const s = shown[i]
    const t = target[i]
    if (!t) break
    if (!s) return { index: i, kind: 'add' }
    if (s.state === 'running' && t.state !== 'running') return { index: i, kind: 'finish' }
  }
  return null
}

/** Walks the shown rows towards the server's rows, one change at a time. */
export class Pacer {
  private shown: StepView[] = []
  private goal: StepView[] = []
  private readonly since = new Map<number, number>()
  private budget = STEP_DWELL_BUDGET_MS
  private due: (Change & { at: number }) | null = null
  private timer: ReturnType<typeof setTimeout> | undefined
  private readonly publish: (rows: StepView[]) => void
  private readonly now: () => number

  constructor(publish: (rows: StepView[]) => void, now: () => number = () => performance.now()) {
    this.publish = publish
    this.now = now
  }

  update(goal: StepView[]): void {
    this.goal = goal
    this.timer ??= setTimeout(() => {
      this.tick()
    }, 0)
  }

  dispose(): void {
    clearTimeout(this.timer)
    this.timer = undefined
  }

  private schedule(ms: number): void {
    this.timer = setTimeout(() => {
      this.tick()
    }, ms)
  }

  private commit(rows: StepView[]): void {
    this.shown = rows
    this.publish(rows)
  }

  private tick(): void {
    this.timer = undefined
    const current = this.shown
    const goal = this.goal
    const change = nextChange(current, goal)
    if (!change) {
      this.due = null
      const fresh = current.map((s, i) => {
        const g = goal[i]
        return g && g.state === s.state ? g : s
      })
      if (fresh.some((s, i) => s !== current[i])) this.commit(fresh)
      return
    }
    const now = this.now()
    const pending = this.due
    if (pending?.index === change.index && pending.kind === change.kind) {
      if (now < pending.at) {
        this.schedule(pending.at - now)
        return
      }
      this.due = null
    } else {
      let delay = 0
      if (change.kind === 'finish') {
        const want = Math.max(0, STEP_DWELL_MS - (now - (this.since.get(change.index) ?? now)))
        if (want > 0) {
          delay = Math.min(want, this.budget)
          this.budget -= delay
          if (delay === 0) delay = STAGGER_MS
        }
      } else if (this.budget <= 0 && change.index > 0) {
        delay = STAGGER_MS
      }
      if (delay > 0) {
        this.due = { ...change, at: now + delay }
        this.schedule(delay)
        return
      }
    }
    const step = goal[change.index]
    if (!step) return
    const next = [...current]
    if (change.kind === 'add') {
      this.since.set(change.index, now)
      next[change.index] = { ...step, state: 'running', latencyMs: null }
    } else {
      next[change.index] = step
    }
    this.commit(next)
    this.schedule(0)
  }
}

/** The rows to show: a live turn's rows at an honest pace; a stored turn's rows as they are. */
export function usePacedSteps(target: StepView[], live: boolean): StepView[] {
  const [shown, setShown] = useState<StepView[]>([])
  const [pacer] = useState(() => new Pacer(setShown))
  useEffect(() => {
    if (live) pacer.update(target)
  }, [target, live, pacer])
  useEffect(
    () => () => {
      pacer.dispose()
    },
    [pacer],
  )
  return live ? shown : target
}
