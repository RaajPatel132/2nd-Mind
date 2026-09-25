/** A step row's context and label, shared by the Trail, the Inspector and announcements. */
import type { ModelCallEvent, Turn } from '../api/client'
import { FAILED_LABEL } from './labels'
import type { Facts, StepView } from './model'
import { STEPS } from './steps'
import type { StepContext } from './types'

export function stepContext(view: StepView, facts: Facts, turn: Turn | null, timezone: string): StepContext {
  const calls = view.own.filter((e): e is ModelCallEvent => e.type === 'model_call')
  return { view, facts, calls, turn, timezone }
}

export function stepLabel(ctx: StepContext): string {
  const spec = STEPS[ctx.view.step]
  if (ctx.view.state === 'running') return spec.running
  if (ctx.view.state === 'failed') return FAILED_LABEL
  return spec.done(ctx)
}
