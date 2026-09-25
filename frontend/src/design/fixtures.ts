/** Synthetic turns for /design: every step in every state. Made-up people and places only. */
import type { AgentStep, ModelCallEvent, StepEvent, TurnEvent } from '../api/client'
import type { StepStart, TrailEvent } from '../trail/model'

const NOW = '2026-09-25T05:11:00Z'
const TZ = 'Asia/Kolkata'

function call(step: string, model: string, inTok: number, outTok: number, ms: number, extra: Partial<ModelCallEvent> = {}): ModelCallEvent {
  return {
    type: 'model_call',
    v: 1,
    step,
    provider: model.startsWith('text-') ? 'openai' : 'anthropic',
    model,
    prompt: step === 'embed' ? null : `${step}@2`,
    started_at: NOW,
    latency_ms: ms,
    attempts: 1,
    usage: {
      provider: 'anthropic',
      model,
      input_tokens: inTok,
      cached_input_tokens: 0,
      output_tokens: outTok,
      cost_usd: (inTok + outTok) / 1_000_000,
      price_version: '2026-09',
      latency_ms: ms,
    },
    ...extra,
  }
}

function step(name: AgentStep, status: StepEvent['status'], latency: number, offsetMs: number): StepEvent {
  return { type: 'step', v: 1, step: name, status, started_at: new Date(Date.parse(NOW) + offsetMs).toISOString(), latency_ms: latency }
}

const FAST = 'claude-haiku-4-5'

/** A save that supersedes an old fact: every catalogue step of a save turn, done. */
export const SAVE_EVENTS: TurnEvent[] = [
  call('intent', FAST, 412, 38, 380),
  { type: 'intent', v: 1, intent: 'save', confidence: 0.94, reason: 'Two things to remember, not a question.', source: 'model' },
  step('understand', 'done', 380, 0),
  call('extract', FAST, 1208, 214, 910),
  step('extract', 'done', 910, 400),
  call('resolve', FAST, 940, 81, 540),
  step('entities', 'done', 540, 1320),
  step('dates', 'done', 3, 1870),
  call('reconcile', FAST, 1512, 96, 610),
  {
    type: 'decision',
    v: 1,
    summary: 'One thing you did and one change of home.',
    classifications: [
      { label: 'Watched Severance', kind: 'episode', subtype: 'watch', state: 'happened', layer: 'quick', modality: 'asserted', sensitivity: 'normal', rationale: 'Something you did.' },
      { label: 'Lives in Pune', kind: 'fact', subtype: 'residence', state: 'current', layer: 'core', modality: 'asserted', sensitivity: 'normal', rationale: 'Where you live.' },
    ],
    time_resolutions: [
      { expression: 'last night', clock: 'occurred', value: '2026-09-24T19:00', precision: 'datetime', now: NOW, timezone: TZ, rule: 'rel.last_night', assumed: false },
      { expression: '12 Sep', clock: 'valid', value: '2026-09-12', precision: 'day', now: NOW, timezone: TZ, rule: 'abs.day_month', assumed: false },
    ],
    entity_resolutions: [
      { mention: 'Severance', entity_id: null, entity_kind: 'work', display_name: 'Severance', outcome: 'matched', created: false, candidates: [], rationale: 'Already on your list.' },
      { mention: 'Pune', entity_id: null, entity_kind: 'place', display_name: 'Pune', outcome: 'new', created: true, candidates: [], rationale: 'A new place.' },
    ],
    normalisations: [],
    reconciliations: [
      { label: 'Lives in Pune', info: { decision: 'supersede', candidate_title: 'Lives in Bengaluru', score: 0.96, rule: 'same subject and predicate' } },
    ],
    not_written: [],
    rules_applied: [],
    rationale: '',
    decided_by: { extract: `anthropic:${FAST}` },
  },
  step('reconcile', 'done', 610, 1880),
  { type: 'policy', v: 1, op: 'write', target: 'Watched Severance', verdict: { decision: 'allowed', rule_id: 'P-DEFAULT', reason: 'nothing sensitive' } },
  { type: 'tool_call', v: 1, tool: 'memory.write', arguments: { kind: 'episode', title: 'Watched Severance' }, result_summary: 'created', policy: { decision: 'allowed', rule_id: 'P-DEFAULT', reason: 'nothing sensitive' } },
  { type: 'tool_call', v: 1, tool: 'memory.supersede', arguments: { title: 'Lives in Bengaluru' }, result_summary: 'superseded', policy: { decision: 'allowed', rule_id: 'P-DEFAULT', reason: 'nothing sensitive' } },
  step('guard', 'done', 4, 2500),
  {
    type: 'memory_diff',
    v: 1,
    entries: [
      { op: 'added', layer: 'quick', title: 'Watched Severance', changes: [{ field: 'occurred_start', after: '2026-09-24T19:00' }], reason: '' },
      { op: 'superseded', layer: 'core', title: 'Lives in Bengaluru', changes: [{ field: 'valid_to', before: null, after: '2026-09-12' }], reason: '' },
      { op: 'added', layer: 'core', title: 'Lives in Pune', changes: [{ field: 'valid_from', after: '2026-09-12' }], reason: '' },
    ],
  },
  step('save', 'done', 90, 2504),
  call('enrich', FAST, 620, 140, 180),
  call('embed', 'text-embedding-3-small', 7, 0, 60, { cache_hits: 2 }),
  step('enrich', 'done', 240, 2600),
  step('answer', 'done', 2, 2840),
]

export const HELD_EVENTS: TurnEvent[] = [
  { type: 'policy', v: 1, op: 'write', target: 'Prefers aisle seats', verdict: { decision: 'held', rule_id: 'P-CORE-1', reason: 'a change to your core profile' } },
  { type: 'tool_call', v: 1, tool: 'memory.write', arguments: { layer: 'core', title: 'Prefers aisle seats' }, result_summary: 'held', policy: { decision: 'held', rule_id: 'P-CORE-1', reason: 'a change to your core profile' } },
  { type: 'memory_diff', v: 1, entries: [{ op: 'held', layer: 'core', title: 'Prefers aisle seats', changes: [], reason: 'Held for your confirmation', rule_id: 'P-CORE-1', held_write_id: 'demo-held' }] },
  step('guard', 'held', 4, 0),
]

export const REFUSED_EVENTS: TurnEvent[] = [
  { type: 'policy', v: 1, op: 'write', target: 'a secret (not shown)', verdict: { decision: 'blocked', rule_id: 'P-SECRET-1', reason: 'looks like a credential' } },
  { type: 'tool_call', v: 1, tool: 'memory.write', arguments: { title: 'a secret (not shown)' }, result_summary: 'refused', policy: { decision: 'blocked', rule_id: 'P-SECRET-1', reason: 'looks like a credential' } },
  step('guard', 'refused', 2, 0),
]

export const FAILED_EVENTS: TurnEvent[] = [
  step('extract', 'failed', 20000, 0),
  { type: 'error', v: 1, code: 'provider_unavailable', message: "The model didn't answer in time, so nothing from this message was saved.", step: 'extract', retryable: true },
]

export function withSeq(events: TurnEvent[]): TrailEvent[] {
  return events.map((event, i) => ({ seq: i + 1, event }))
}

export const RUNNING_START: StepStart[] = [{ step: 'reconcile', at: NOW }]
