/** Done labels and chips: functions of a step's events (rulebook §8, §10). */
import type { EntityResolution, Reconciliation, ToolCallEvent } from '../api/client'
import { formatDay, formatSeconds, plural } from '../lib/format'
import { diffCounts, type Facts } from './model'
import type { StepContext } from './types'

const INTENT_LABEL: Record<string, string> = {
  save: 'Understood: something to save',
  recall: 'Understood: a question',
  save_and_recall: 'Understood: something to save, and a question',
  correct: 'Understood: a correction',
  chit_chat: 'Understood: just chatting',
}

export const FAILED_LABEL = "Couldn't finish this step"

export function understandDone({ facts }: StepContext): string {
  return facts.intent ? (INTENT_LABEL[facts.intent.intent] ?? 'Understood') : 'Read your message'
}
export function understandChips({ facts }: StepContext): string[] {
  const i = facts.intent
  return i ? [`${i.intent} · ${i.confidence.toFixed(2)}`] : []
}

export function extractDone({ facts }: StepContext): string {
  const d = facts.decision
  if (!d) return 'Picked out what to remember'
  const n = d.classifications.filter((c) => c.sensitivity !== 'secret').length
  return n === 0 ? 'Nothing to remember' : `Found ${plural(n, 'thing')} to remember`
}
export function extractChips({ facts }: StepContext): string[] {
  return (facts.decision?.classifications ?? [])
    .filter((c) => c.sensitivity !== 'secret')
    .slice(0, 2)
    .map((c) => (c.subtype ? `${c.kind} · ${c.subtype}` : c.kind))
}

export function datesDone({ facts }: StepContext): string {
  const first = facts.decision?.time_resolutions[0]
  if (!first) return 'Worked out dates'
  return `“${first.expression}” → ${formatDay(first.value)}`
}
export function datesChips({ facts }: StepContext): string[] {
  const n = facts.decision?.time_resolutions.length ?? 0
  return n ? [plural(n, 'date')] : []
}

function outcomeCount(list: EntityResolution[], outcome: EntityResolution['outcome']): number {
  return list.filter((e) => e.outcome === outcome).length
}
export function entitiesDone({ facts }: StepContext): string {
  const list = facts.decision?.entity_resolutions ?? []
  const [only] = list
  if (!only) return 'Recognised people, places and things'
  if (list.length === 1) {
    if (only.outcome === 'matched') return `Recognised ${only.display_name}`
    if (only.outcome === 'new') return `${only.display_name} is new`
    if (only.outcome === 'ambiguous') return `Not sure who “${only.mention}” is`
    return `Updated ${only.display_name}`
  }
  const matched = outcomeCount(list, 'matched') + outcomeCount(list, 'updated')
  const fresh = outcomeCount(list, 'new')
  if (matched && fresh) return `Recognised ${String(matched)} · ${String(fresh)} new`
  return matched ? `Recognised ${String(matched)}` : `${String(fresh)} new`
}
export function entitiesChips({ facts }: StepContext): string[] {
  const list = facts.decision?.entity_resolutions ?? []
  const out: string[] = []
  const matched = outcomeCount(list, 'matched') + outcomeCount(list, 'updated')
  const fresh = outcomeCount(list, 'new')
  if (matched) out.push(`${String(matched)} match`)
  if (fresh) out.push(`${String(fresh)} new`)
  return out
}

function changing(list: Reconciliation[]): Reconciliation[] {
  return list.filter((r) => r.info.decision !== 'new' && r.info.decision !== 'no_op')
}
export function reconcileDone({ facts }: StepContext): string {
  const list = facts.decision?.reconciliations ?? []
  const changes = changing(list)
  if (changes.length === 0) return list.some((r) => r.info.decision === 'no_op') ? 'Already knew this' : 'Nothing like it yet'
  if (changes.every((r) => r.info.decision === 'supersede')) return `Replaces ${plural(changes.length, 'memory', 'memories')}`
  return `Updates ${plural(changes.length, 'memory', 'memories')} you had`
}
export function reconcileChips({ facts }: StepContext): string[] {
  const seen = new Set((facts.decision?.reconciliations ?? []).map((r) => r.info.decision.replace('_', ' ')))
  return [...seen].slice(0, 3)
}

export function enrichDone({ calls }: StepContext): string {
  return calls.some((c) => c.step === 'enrich') ? 'Added search keys' : 'Made it findable later'
}
export function enrichChips({ calls }: StepContext): string[] {
  return calls.map((c) => (c.cache_hits != null ? `${c.step} · ${String(c.cache_hits)} cached` : c.step)).slice(0, 3)
}

function isSecretRefusal(facts: Facts): boolean {
  return facts.policies.some((p) => p.verdict.decision === 'blocked' && p.verdict.rule_id.startsWith('P-SECRET'))
}
function verdicts(tools: ToolCallEvent[], decision: 'allowed' | 'held' | 'blocked'): number {
  return tools.filter((t) => t.policy?.decision === decision).length
}
export function guardDone({ view, facts }: StepContext): string {
  if (view.state === 'refused') {
    if (isSecretRefusal(facts)) return 'Refused: that looks like a password'
    return `Refused ${plural(verdicts(facts.tools, 'blocked'), 'change')}`
  }
  if (view.state === 'held') {
    const held = diffCounts(facts.diff).held || verdicts(facts.tools, 'held')
    return `Held ${plural(held, 'change')} for your OK`
  }
  const allowed = verdicts(facts.tools, 'allowed')
  return allowed ? `${plural(allowed, 'change')} allowed` : 'Nothing to check'
}
export function guardChips({ facts }: StepContext): string[] {
  return [...new Set(facts.tools.map((t) => t.policy?.rule_id).filter((r): r is string => Boolean(r)))].slice(0, 3)
}

export function saveDone({ facts }: StepContext): string {
  const c = diffCounts(facts.diff)
  const parts: string[] = []
  if (c.added) parts.push(`Saved ${String(c.added)}`)
  if (c.updated) parts.push(`${parts.length ? 'updated' : 'Updated'} ${String(c.updated)}`)
  if (c.removed) parts.push(`${parts.length ? 'removed' : 'Removed'} ${String(c.removed)}`)
  return parts.join(' · ') || 'Saved'
}
export function saveChips({ facts }: StepContext): string[] {
  const c = diffCounts(facts.diff)
  const out: string[] = []
  if (c.added) out.push(`+${String(c.added)}`)
  if (c.updated) out.push(`~${String(c.updated)}`)
  if (c.removed) out.push(`−${String(c.removed)}`)
  return out
}

export function answerDone({ view, calls }: StepContext): string {
  if (calls.length === 0) return 'Confirmed what I did'
  return view.latencyMs != null ? `Replied in ${formatSeconds(view.latencyMs)}` : 'Replied'
}
export function answerChips({ calls }: StepContext): string[] {
  return calls.slice(0, 1).map((c) => c.model)
}

export function undoDone({ facts }: StepContext): string {
  const c = diffCounts(facts.diff)
  return `Reverted ${plural(c.added + c.updated + c.removed, 'change')}`
}
export function confirmDone({ facts }: StepContext): string {
  const c = diffCounts(facts.diff)
  return `Applied ${plural(c.added + c.updated + c.removed, 'change')}`
}
export function diffChips(ctx: StepContext): string[] {
  return saveChips(ctx)
}

export function none(): string[] {
  return []
}
