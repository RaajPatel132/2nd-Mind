/**
 * The two layers of every step: "What happened" (plain words, the product's voice) and "Under the
 * hood" (Machine type). The Inspector's panels are built from these same components.
 */
import { formatDay, formatInZone, formatMs, plural, shortId, truncate, weekdayIn } from '../lib/format'
import type { ReactNode } from 'react'
import { DiffList, FactLine, FixDate, KV, MTable, ModelLines } from './parts'
import { diffCounts } from './model'
import type { StepContext } from './types'

type P = { ctx: StepContext }

function Plain({ children }: { children: ReactNode }) {
  return <p className="m-0 measure text-pretty text-body text-fg">{children}</p>
}

function Muted({ children }: { children: ReactNode }) {
  return <p className="m-0 text-label font-normal text-fg-3">{children}</p>
}

// ------------------------------------------------------------------ understand

export function UnderstandPlain({ ctx }: P) {
  const i = ctx.facts.intent
  if (!i) return <Plain>I read your message.</Plain>
  return <Plain>{i.source === 'rule' ? 'The secret check matched, so no model saw this message.' : i.reason}</Plain>
}

export function UnderstandTech({ ctx }: P) {
  const i = ctx.facts.intent
  return (
    <>
      {i && (
        <>
          <p className="m-0 mb-2 font-machine text-mono text-fg-2" data-testid="decision-intent">
            Intent {i.intent.replaceAll('_', '-')} · {i.source} · {Math.round(i.confidence * 100)}%
          </p>
          <KV rows={[['intent', i.intent], ['confidence', i.confidence.toFixed(2)], ['source', i.source]]} />
        </>
      )}
      <ModelLines calls={ctx.calls} />
    </>
  )
}

// ------------------------------------------------------------------ extract

export function ExtractPlain({ ctx }: P) {
  const d = ctx.facts.decision
  if (!d) return <Plain>I picked out what's worth keeping from your message.</Plain>
  return (
    <>
      <Plain>{d.summary}</Plain>
      {d.not_written.length > 0 && (
        <ul className="m-0 mt-2 list-disc pl-5 text-label font-normal text-fg-2">
          {d.not_written.map((n) => (
            <li key={n}>Not kept: {n}</li>
          ))}
        </ul>
      )}
    </>
  )
}

export function ExtractTech({ ctx }: P) {
  const d = ctx.facts.decision
  return (
    <>
      {d && d.classifications.length > 0 && (
        <MTable
          head={['memory', 'kind', 'state', 'layer', 'modality']}
          rows={d.classifications.map((c, i) => ({
            key: `${c.label}-${String(i)}`,
            testId: 'decision-memory',
            cells: [
              truncate(c.label, 48),
              c.subtype ? `${c.kind} · ${c.subtype}` : c.kind,
              c.state ?? '—',
              c.layer,
              c.sensitivity !== 'normal' ? `${c.modality} · ${c.sensitivity}` : c.modality,
            ],
          }))}
        />
      )}
      {d && d.normalisations.length > 0 && (
        <FactLine facts={d.normalisations.map((n) => `${n.vocab}: ${n.reused ? 'reused' : 'new'} ${n.chosen}`)} />
      )}
      <ModelLines calls={ctx.calls} />
    </>
  )
}

// ------------------------------------------------------------------ dates

export function DatesPlain({ ctx }: P) {
  const list = ctx.facts.decision?.time_resolutions ?? []
  if (list.length === 0) return <Plain>I worked out the dates in your message.</Plain>
  return (
    <>
      {list.slice(0, 2).map((t, i) => (
        <Plain key={`${t.expression}-${String(i)}`}>
          I read “{t.expression}” as {formatDay(t.value)}, because it's {weekdayIn(t.now, t.timezone)} today in {t.timezone}.
          {t.assumed && ` That was an assumption${t.alternative ? `; the other reading was ${t.alternative}` : ''}.`}
          {t.item_id && <FixDate itemId={t.item_id} />}
        </Plain>
      ))}
    </>
  )
}

export function DatesTech({ ctx }: P) {
  const list = ctx.facts.decision?.time_resolutions ?? []
  const first = list[0]
  return (
    <>
      <MTable
        head={['expression', 'value', 'precision', 'clock', 'rule']}
        rows={list.map((t, i) => ({
          key: `${t.expression}-${t.clock}-${String(i)}`,
          testId: 'decision-date',
          cells: [`“${t.expression}”`, t.rrule ? `${t.value} (${t.rrule})` : t.value, t.precision, t.clock, t.rule],
        }))}
      />
      {first && (
        <div className="mt-3">
          <KV rows={[['now', formatInZone(first.now, first.timezone)], ['timezone', first.timezone]]} />
        </div>
      )}
      <FactLine facts={['code, no model']} />
    </>
  )
}

// ------------------------------------------------------------------ entities

export function EntitiesPlain({ ctx }: P) {
  const list = ctx.facts.decision?.entity_resolutions ?? []
  if (list.length === 0) return <Plain>I looked for the people, places and things you mentioned.</Plain>
  return (
    <Plain>
      {list
        .slice(0, 3)
        .map((e) =>
          e.outcome === 'matched'
            ? `“${e.mention}” is ${e.display_name}, who I already knew.`
            : e.outcome === 'new'
              ? `${e.display_name} is new, so I added them as a ${e.entity_kind}.`
              : e.outcome === 'ambiguous'
                ? `I wasn't sure who “${e.mention}” is.`
                : `I updated what I know about ${e.display_name}.`,
        )
        .join(' ')}
    </Plain>
  )
}

export function EntitiesTech({ ctx }: P) {
  const list = ctx.facts.decision?.entity_resolutions ?? []
  return (
    <>
      <MTable
        head={['mention', 'kind', 'outcome', 'name', 'other candidates']}
        rows={list.map((e, i) => ({
          key: `${e.mention}-${String(i)}`,
          testId: 'decision-entity',
          attrs: { 'data-outcome': e.outcome },
          cells: [`“${e.mention}”`, e.entity_kind, e.outcome, e.display_name, e.candidates.join(', ') || '—'],
        }))}
      />
      <ModelLines calls={ctx.calls} />
    </>
  )
}

// ------------------------------------------------------------------ reconcile

export function ReconcilePlain({ ctx }: P) {
  const list = ctx.facts.decision?.reconciliations ?? []
  const changes = list.filter((r) => r.info.decision !== 'new')
  if (changes.length === 0) return <Plain>There was nothing like this in your memory, so it's new.</Plain>
  return (
    <Plain>
      {changes
        .slice(0, 3)
        .map((r) => {
          const old = r.info.candidate_title ? `“${r.info.candidate_title}”` : 'what you had'
          switch (r.info.decision) {
            case 'supersede':
              return `“${r.label}” replaces ${old}, which stays in your history.`
            case 'fulfil':
              return `“${r.label}” ticks off ${old}.`
            case 'add_detail':
              return `“${r.label}” adds detail to ${old}.`
            case 'link':
              return `“${r.label}” is linked to ${old}.`
            default:
              return `I already knew “${r.label}”.`
          }
        })
        .join(' ')}
    </Plain>
  )
}

export function ReconcileTech({ ctx }: P) {
  const d = ctx.facts.decision
  const list = d?.reconciliations ?? []
  return (
    <>
      {list.length > 0 && (
        <MTable
          head={['memory', 'decision', 'closest', 'score', 'rule']}
          rows={list.map((r, i) => ({
            key: `${r.label}-${String(i)}`,
            testId: 'decision-reconcile',
            cells: [
              truncate(r.label, 40),
              r.info.decision.replace('_', ' '),
              r.info.candidate_title ? truncate(r.info.candidate_title, 32) : '—',
              r.info.score != null ? r.info.score.toFixed(2) : '—',
              r.info.rule || '—',
            ],
          }))}
        />
      )}
      {d && Object.keys(d.decided_by).length > 0 && (
        <p className="m-0 mt-3 font-machine text-mono-sm text-fg-3" data-testid="decided-by">
          decided by {Object.entries(d.decided_by).map(([step, model]) => `${step}: ${model}`).join(' · ')}
        </p>
      )}
      <ModelLines calls={ctx.calls} />
    </>
  )
}

// ------------------------------------------------------------------ enrich

export function EnrichPlain() {
  return <Plain>I wrote a few more ways to find this later, so a search in other words still finds it.</Plain>
}

export function EnrichTech({ ctx }: P) {
  if (ctx.calls.length === 0) return <Muted>Keys were written from templates; no model was needed.</Muted>
  return <ModelLines calls={ctx.calls} />
}

// ------------------------------------------------------------------ guard

export function GuardPlain({ ctx }: P) {
  const { view, facts } = ctx
  if (view.state === 'refused') {
    const secret = facts.policies.some((p) => p.verdict.decision === 'blocked' && p.verdict.rule_id.startsWith('P-SECRET'))
    return (
      <Plain>
        {secret
          ? "I don't keep passwords or keys. Nothing was saved, and the stored message has it removed."
          : facts.policies.filter((p) => p.verdict.decision === 'blocked').map((p) => p.verdict.reason).join(' ')}
      </Plain>
    )
  }
  if (view.state === 'held') {
    const reasons = [...new Set(facts.policies.filter((p) => p.verdict.decision === 'held').map((p) => p.verdict.reason))]
    const heldOnly = facts.diff?.entries.filter((e) => e.op === 'held') ?? []
    const saved = diffCounts(facts.diff)
    return (
      <>
        <Plain>
          This needs your say-so before I keep it{reasons.length ? `: ${reasons.join('; ')}` : ''}.
          {saved.added + saved.updated > 0 ? ' Everything else from this message was saved.' : ''}
        </Plain>
        {facts.diff && heldOnly.length > 0 && (
          <div className="mt-3">
            <DiffList diff={facts.diff} only={(e) => e.op === 'held'} />
          </div>
        )}
      </>
    )
  }
  return <Plain>Nothing sensitive, and nothing that needs your OK.</Plain>
}

export function GuardTech({ ctx }: P) {
  const writes = ctx.facts.tools.filter((t) => t.access !== 'read')
  if (writes.length === 0) return <Muted>No changes to check.</Muted>
  return <ToolCalls ctx={{ ...ctx, facts: { ...ctx.facts, tools: writes } }} />
}

/** Each writer op in order, with its arguments, result and policy decision (FR-9 panel 4). */
export function ToolCalls({ ctx }: P) {
  return (
    <ol className="m-0 grid list-none gap-3 p-0">
      {ctx.facts.tools.map((call, i) => (
        <li key={`${call.tool}-${String(i)}`} data-testid="tool-call" className="min-w-0">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 font-machine text-mono">
            <span className="text-fg-3 tnum">{i + 1}</span>
            <span className="text-fg">{call.tool}</span>
            {call.access === 'read' && (
              <span className="text-fg-3" data-testid="tool-call-read" title="A read: no write policy applies">
                read{call.latency_ms != null ? ` · ${formatMs(call.latency_ms)}` : ''}
              </span>
            )}
            {call.policy && (
              <span
                className={
                  call.policy.decision === 'allowed' ? 'text-ok' : call.policy.decision === 'held' ? 'text-warn' : 'text-bad'
                }
                data-testid="tool-call-policy"
              >
                {call.policy.decision} · {call.policy.rule_id}
              </span>
            )}
          </div>
          {Object.keys(call.arguments).length > 0 && (
            <div className="mt-1 pl-5">
              <KV rows={Object.entries(call.arguments).map(([k, v]) => [k, truncate(v, 90)])} />
            </div>
          )}
          {call.result_summary && <p className="m-0 mt-1 pl-5 font-machine text-mono-sm text-fg-3">→ {call.result_summary}</p>}
          {call.policy && call.policy.decision !== 'allowed' && (
            <p className="m-0 mt-1 pl-5 text-label font-normal text-fg-2">{call.policy.reason}</p>
          )}
        </li>
      ))}
    </ol>
  )
}

// ------------------------------------------------------------------ save, undo, confirm

function changedTitles(ctx: StepContext): string {
  const entries = ctx.facts.diff?.entries.filter((e) => e.op !== 'not_written' && e.op !== 'held') ?? []
  const names = entries.slice(0, 3).map((e) => `“${truncate(e.title, 40)}”`)
  const more = entries.length - names.length
  return names.join(', ') + (more > 0 ? ` and ${String(more)} more` : '')
}

export function SavePlain({ ctx }: P) {
  const c = diffCounts(ctx.facts.diff)
  const parts: string[] = []
  if (c.added) parts.push(`added ${plural(c.added, 'memory', 'memories')}`)
  if (c.updated) parts.push(`updated ${String(c.updated)}`)
  if (c.removed) parts.push(`removed ${String(c.removed)}`)
  return (
    <Plain>
      I {parts.join(', ') || 'saved it'}: {changedTitles(ctx)}.
    </Plain>
  )
}

export function DiffTech({ ctx }: P) {
  const diff = ctx.facts.diff
  if (!diff || diff.entries.length === 0) return <Muted>No memory changes.</Muted>
  return (
    <>
      {diff.undo_of && (
        <p className="m-0 mb-3 font-machine text-mono text-fg-2">Undoes turn {shortId(diff.undo_of)}</p>
      )}
      <DiffList diff={diff} />
    </>
  )
}

export function UndoPlain() {
  return <Plain>Everything that turn changed is back the way it was. This undo is a turn of its own, so it can be undone too.</Plain>
}

export function ConfirmPlain({ ctx }: P) {
  return <Plain>I applied the change you approved: {changedTitles(ctx)}. It can be undone like any other turn.</Plain>
}

// ------------------------------------------------------------------ answer

export function AnswerPlain({ ctx }: P) {
  const call = ctx.calls[0]
  if (!call) return <Plain>A short confirmation of what I did. No model was needed to write it.</Plain>
  return (
    <Plain>
      I wrote the reply{call.fallback ? ' with the fallback model, because the primary one didn\'t answer' : ''}.
    </Plain>
  )
}

export function AnswerTech({ ctx }: P) {
  if (ctx.calls.length === 0) {
    return <FactLine facts={['code, no model', ...(ctx.view.latencyMs != null ? [formatMs(ctx.view.latencyMs)] : [])]} />
  }
  return <ModelLines calls={ctx.calls} />
}

// ------------------------------------------------------------------ failed, reserved

export function FailedPlain({ ctx }: P) {
  const e = ctx.facts.error
  return <Plain>{e?.message ?? 'This step stopped before it finished, so nothing after it ran.'}</Plain>
}

export function FailedTech({ ctx }: P) {
  const e = ctx.facts.error
  if (!e) return <Muted>No error was recorded.</Muted>
  return <KV rows={[['code', e.code], ['step', e.step ?? '—'], ['retryable', e.retryable ? 'yes' : 'no']]} />
}

export function ReservedPlain() {
  return <Plain>This step arrives with recall and links in a later version.</Plain>
}

export function ReservedTech({ ctx }: P) {
  return <ModelLines calls={ctx.calls} />
}
