import type { LucideIcon } from 'lucide-react'
import type { ComponentType } from 'react'
import type { ModelCallEvent, Turn } from '../api/client'
import type { Facts, StepView } from './model'

/** What a step's labels and details are computed from. */
export type StepContext = {
  view: StepView
  /** Everything the turn has said so far (a live turn fills in as events arrive). */
  facts: Facts
  /** The model calls written with this step. */
  calls: ModelCallEvent[]
  turn: Turn | null
  timezone: string
}

export type StepSpec = {
  icon: LucideIcon
  /** Present tense while it runs: what it is doing. */
  running: string
  /** Past tense when done: what it found. */
  done: (ctx: StepContext) => string
  /** Up to three short machine facts beside the label. */
  chips: (ctx: StepContext) => string[]
  /** "What happened": one or two plain sentences in the product's voice. */
  Plain: ComponentType<{ ctx: StepContext }>
  /** "Under the hood": the technical detail, in Machine type. Never prompts or raw output. */
  Tech: ComponentType<{ ctx: StepContext }>
}
