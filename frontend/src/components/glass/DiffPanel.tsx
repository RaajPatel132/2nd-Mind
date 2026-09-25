import { useState } from 'react'
import { getEntity, getItem, type DiffEntry, type HeldWrite, type MemoryDiffEvent } from '../../api/client'
import { formatValue, shortId, truncate } from '../../lib/format'
import { Chip } from './Panel'

const LAYERS = ['core', 'quick', 'archive'] as const

const OPS: Record<DiffEntry['op'], { mark: string; label: string; tone: 'slate' | 'green' | 'amber' | 'red' | 'indigo' }> = {
  added: { mark: '+', label: 'added', tone: 'green' },
  updated: { mark: '~', label: 'updated', tone: 'indigo' },
  superseded: { mark: '~', label: 'superseded', tone: 'indigo' },
  fulfilled: { mark: '✓', label: 'fulfilled', tone: 'green' },
  removed: { mark: '−', label: 'removed', tone: 'red' },
  held: { mark: '⏸', label: 'held', tone: 'amber' },
  not_written: { mark: '∅', label: 'not written', tone: 'slate' },
  conflict: { mark: '!', label: 'conflict', tone: 'amber' },
}

type HeldActions = {
  held: Map<string, HeldWrite>
  busy: string | null
  onConfirm: (heldId: string) => void
  onReject: (heldId: string) => void
}

type Props = { diff: MemoryDiffEvent } & HeldActions

/** The memory changes of a turn, grouped by layer, built from the stored write log. */
export function DiffBody({ diff, ...held }: Props) {
  return (
    <div className="space-y-3 text-sm">
      {diff.undo_of && <p className="text-slate-700">Undoes turn {shortId(diff.undo_of)}.</p>}
      {LAYERS.map((layer) => {
        const entries = diff.entries.filter((e) => e.layer === layer)
        if (entries.length === 0) return null
        return (
          <section key={layer} data-testid={`diff-layer-${layer}`}>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-600">{layer}</h3>
            <ul className="space-y-2">
              {entries.map((entry, i) => (
                <Entry key={`${entry.op}-${entry.item_id ?? entry.entity_id ?? entry.title}-${String(i)}`} entry={entry} {...held} />
              ))}
            </ul>
          </section>
        )
      })}
    </div>
  )
}

function Entry({ entry, held, busy, onConfirm, onReject }: { entry: DiffEntry } & HeldActions) {
  const op = OPS[entry.op]
  const changes = entry.op === 'added' ? entry.changes.filter((c) => c.after !== null) : entry.changes
  return (
    <li data-testid="diff-entry" data-op={entry.op} className="rounded-md border border-slate-100 p-2">
      <div className="flex items-baseline gap-2">
        <span aria-hidden className="w-4 shrink-0 text-center font-mono font-semibold text-slate-700">
          {op.mark}
        </span>
        <span className="min-w-0 flex-1 break-words font-medium text-slate-900">{entry.title}</span>
        <Chip tone={op.tone}>{op.label}</Chip>
      </div>
      <div className="space-y-1 pl-6">
        {changes.length > 0 && (
          <ul className="text-xs text-slate-700">
            {changes.map((c) => (
              <li key={c.field}>
                <span className="text-slate-500">{c.field}</span>{' '}
                {entry.op === 'added' ? (
                  truncate(formatValue(c.after))
                ) : (
                  <>
                    {truncate(formatValue(c.before), 60)} → {truncate(formatValue(c.after), 60)}
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
        {entry.reason && (
          <p className="text-xs text-slate-700">
            {entry.reason}
            {entry.rule_id && <span className="font-mono text-slate-500"> ({entry.rule_id})</span>}
          </p>
        )}
        {entry.reconcile && entry.reconcile.decision !== 'new' && (
          <p className="text-xs text-slate-600">
            Reconciled: {entry.reconcile.decision.replace('_', ' ')}
            {entry.reconcile.candidate_title && ` “${entry.reconcile.candidate_title}”`}
            {entry.reconcile.score != null && ` (score ${entry.reconcile.score.toFixed(2)})`}
          </p>
        )}
        {entry.op === 'held' && entry.held_write_id && (
          <HeldControls heldId={entry.held_write_id} held={held} busy={busy} onConfirm={onConfirm} onReject={onReject} />
        )}
        <Detail entry={entry} />
      </div>
    </li>
  )
}

function HeldControls({ heldId, held, busy, onConfirm, onReject }: { heldId: string } & HeldActions) {
  const record = held.get(heldId)
  if (record && record.status !== 'pending') {
    return (
      <p className="text-xs text-slate-700" data-testid="held-status">
        {record.status === 'confirmed' ? 'Confirmed' : 'Rejected'}
        {record.resolved_turn_id && ` in turn ${shortId(record.resolved_turn_id)}`}.
      </p>
    )
  }
  return (
    <div className="flex gap-2 pt-1">
      <button type="button" className="btn-chip" disabled={busy !== null} onClick={() => { onConfirm(heldId) }} data-testid="held-confirm">
        {busy === heldId ? 'Working…' : 'Confirm'}
      </button>
      <button type="button" className="btn-chip" disabled={busy !== null} onClick={() => { onReject(heldId) }} data-testid="held-reject">
        Reject
      </button>
    </div>
  )
}

/** A plain JSON view of the item or entity (the real item page comes in S6). */
function Detail({ entry }: { entry: DiffEntry }) {
  const [json, setJson] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const target = entry.item_id ? { kind: 'item', id: entry.item_id } : entry.entity_id ? { kind: 'entity', id: entry.entity_id } : null
  if (!target) return null

  async function toggle() {
    if (json !== null) {
      setJson(null)
      return
    }
    if (!target) return
    try {
      const detail = target.kind === 'item' ? await getItem(target.id) : await getEntity(target.id)
      setJson(JSON.stringify(detail, null, 2))
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load it.')
    }
  }

  return (
    <div>
      <button type="button" className="btn-link text-xs" aria-expanded={json !== null} onClick={() => void toggle()}>
        {json !== null ? 'Hide' : 'View'} {target.kind} JSON
      </button>
      {error && (
        <p role="alert" className="text-xs text-red-700">
          {error}
        </p>
      )}
      {json !== null && (
        <pre className="mt-1 max-h-64 overflow-auto rounded bg-slate-900 p-2 text-[11px] leading-snug text-slate-100" data-testid="detail-json">
          {json}
        </pre>
      )}
    </div>
  )
}
