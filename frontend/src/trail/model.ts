/**
 * From a turn's events to the rows of its Trail. The same function serves a live turn (events
 * from `turn.event` frames, starts from `step.started`) and a stored one (from `/events`), so a
 * live turn and the same turn reloaded end up identical (FR-9.2).
 */
import type {
  AgentStep,
  BlockedEvent,
  QuotaEvent,
  CitationsEvent,
  RetrievalEvent,
  DecisionEvent,
  ErrorEvent,
  FetchEvent,
  IntentEvent,
  MemoryDiffEvent,
  ModelCallEvent,
  PolicyEvent,
  TimeResolution,
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

/** A saved link is read by the worker after the turn completes; past this a pending read stops counting as running. */
const FETCH_RUNNING_MS = 15 * 60_000

export function stepViews(events: readonly TrailEvent[], starts: readonly StepStart[], nowMs: number = Date.now()): StepView[] {
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
  return mergeFetches(views, nowMs)
}

/** The latest FetchEvent of each link in a list of events (a link is saved as pending, then read). */
export function latestFetches(events: readonly TurnEvent[]): FetchEvent[] {
  const byItem = new Map<string, FetchEvent>()
  for (const event of events) if (event.type === 'fetch') byItem.set(event.item_id, event)
  return [...byItem.values()]
}

/**
 * A turn that saved links has one `fetch` step from the save (each link pending or refused) and
 * one more, appended later, for each read. They are one row: it stays where the save put it and
 * takes the state of the latest event of every link, so the row updates in place when a read ends.
 */
function mergeFetches(views: StepView[], nowMs: number): StepView[] {
  const first = views.findIndex((v) => v.step === 'fetch')
  if (first < 0) return views
  const fetchViews = views.filter((v) => v.step === 'fetch')
  const own = fetchViews.flatMap((v) => v.own)
  const latest = latestFetches(own)
  const settled = fetchViews.length > 1 ? fetchViews[fetchViews.length - 1] : undefined
  const head = fetchViews[0]
  if (!head) return views
  const waiting = latest.some((f) => f.status === 'pending')
  const fresh = nowMs - Date.parse(head.startedAt) < FETCH_RUNNING_MS
  let state: StepState = head.state
  if (waiting) state = fresh ? 'running' : 'done'
  else if (latest.length > 0 && latest.every((f) => f.status === 'refused')) state = 'refused'
  else if (latest.length > 0 && latest.every((f) => f.status === 'failed')) state = 'failed'
  else if (latest.length > 0) state = 'done'
  const merged: StepView = {
    ...head,
    state,
    own,
    // The row reports the read that finished, at the time it happened.
    startedAt: waiting || !settled ? head.startedAt : settled.startedAt,
    latencyMs: waiting ? null : settled ? settled.latencyMs : head.latencyMs,
  }
  return views.flatMap((v, i) => (i === first ? [merged] : v.step === 'fetch' ? [] : [v]))
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
  /** Each saved link's latest state: pending while the worker reads it, then how it went. */
  fetches: FetchEvent[]
  policies: PolicyEvent[]
  calls: ModelCallEvent[]
  error?: ErrorEvent
  /** The spend gate stopped this turn before any model call (ADR-0032). */
  blocked?: BlockedEvent
  /** The quota right after the turn, stored with it so a reload shows the same number. */
  quota?: QuotaEvent
}

export function factsOf(events: readonly TrailEvent[]): Facts {
  const facts: Facts = { tools: [], policies: [], calls: [], fetches: latestFetches(events.map((e) => e.event)) }
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
      case 'blocked':
        facts.blocked = event
        break
      case 'quota':
        facts.quota = event
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

/** Time expressions grouped under the memory each was written to (a message can date several). */
export function datesByMemory(list: readonly TimeResolution[]): { key: string; title: string | null; dates: TimeResolution[] }[] {
  const groups: { key: string; title: string | null; dates: TimeResolution[] }[] = []
  for (const t of list) {
    const key = t.item_id ?? t.memory ?? ''
    const group = groups.find((g) => g.key === key)
    if (group) group.dates.push(t)
    else groups.push({ key, title: t.memory ?? null, dates: [t] })
  }
  return groups
}

/** Whether a turn still waits for a page to be read (the conversation polls it until it is). */
export function hasPendingFetch(events: readonly TrailEvent[] | null): boolean {
  return latestFetches((events ?? []).map((e) => e.event)).some((f) => f.status === 'pending')
}
