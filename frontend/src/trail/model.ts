/**
 * From a turn's events to the rows of its Trail. The same function serves a live turn (events
 * from `turn.event` frames, starts from `step.started`) and a stored one (from `/events`), so a
 * live turn and the same turn reloaded end up identical (FR-9.2).
 */
import type {
  AgentStep,
  CitationsEvent,
  RetrievalEvent,
  DecisionEvent,
  ErrorEvent,
  IntentEvent,
  MemoryDiffEvent,
  ModelCallEvent,
  PolicyEvent,
  ToolCallEvent,
  TurnEvent,
} from '../api/client'

export type TrailEvent = { seq: number; event: TurnEvent }
export type StepStart = { step: AgentStep; at: string }
export type StepState = 'running' | 'done' | 'held' | 'refused' | 'failed'

export type StepView = {
  key: string
  step: AgentStep
  state: StepState
  startedAt: string
  latencyMs: number | null
  /** The events written with this step (the ones since the previous step's event). */
  own: TurnEvent[]
}

export function stepViews(events: readonly TrailEvent[], starts: readonly StepStart[]): StepView[] {
  const views: StepView[] = []
  let pending: TurnEvent[] = []
  const sorted = [...events].sort((a, b) => a.seq - b.seq)
  for (const { event } of sorted) {
    if (event.type !== 'step') {
      pending.push(event)
      continue
    }
    const step = event
    views.push({
      key: `${step.step}-${String(views.length)}`,
      step: step.step,
      state: step.status,
      startedAt: step.started_at,
      latencyMs: step.latency_ms,
      own: pending,
    })
    pending = []
  }
  // Steps the server started that haven't reported their end yet are running.
  for (const start of starts.slice(views.length)) {
    views.push({ key: `${start.step}-${String(views.length)}`, step: start.step, state: 'running', startedAt: start.at, latencyMs: null, own: pending })
    pending = []
  }
  return views
}

/** Everything a turn said, by kind: what labels and details are computed from. */
export type Facts = {
  intent?: IntentEvent
  decision?: DecisionEvent
  /** Every memory diff of the turn, merged (a recall turn can save, then fire a reminder). */
  diff?: MemoryDiffEvent
  retrieval?: RetrievalEvent
  citations?: CitationsEvent
  tools: ToolCallEvent[]
  policies: PolicyEvent[]
  calls: ModelCallEvent[]
  error?: ErrorEvent
}

export function factsOf(events: readonly TrailEvent[]): Facts {
  const facts: Facts = { tools: [], policies: [], calls: [] }
  for (const { event } of events) {
    switch (event.type) {
      case 'intent':
        facts.intent = event
        break
      case 'decision':
        facts.decision = event
        break
      case 'memory_diff':
        facts.diff = facts.diff ? { ...facts.diff, entries: [...facts.diff.entries, ...event.entries] } : event
        break
      case 'retrieval':
        facts.retrieval = event
        break
      case 'citations':
        facts.citations = event
        break
      case 'tool_call':
        facts.tools.push(event)
        break
      case 'policy':
        facts.policies.push(event)
        break
      case 'model_call':
        facts.calls.push(event)
        break
      case 'error':
        facts.error = event
        break
    }
  }
  return facts
}

const CHANGING = new Set(['added', 'updated', 'removed', 'superseded', 'fulfilled'])

/** How many distinct memories and entities a diff changed (undo asks first above one). */
export function touched(diff: MemoryDiffEvent | undefined): number {
  if (!diff) return 0
  const ids = new Set<string>()
  for (const entry of diff.entries) {
    if (CHANGING.has(entry.op)) ids.add(entry.item_id ?? entry.entity_id ?? entry.title)
  }
  return ids.size
}

export type DiffCounts = { added: number; updated: number; removed: number; held: number; notWritten: number }

export function diffCounts(diff: MemoryDiffEvent | undefined): DiffCounts {
  const c: DiffCounts = { added: 0, updated: 0, removed: 0, held: 0, notWritten: 0 }
  for (const e of diff?.entries ?? []) {
    if (e.op === 'added') c.added += 1
    else if (e.op === 'updated' || e.op === 'superseded' || e.op === 'fulfilled') c.updated += 1
    else if (e.op === 'removed') c.removed += 1
    else if (e.op === 'held') c.held += 1
    else if (e.op === 'not_written') c.notWritten += 1
  }
  return c
}
