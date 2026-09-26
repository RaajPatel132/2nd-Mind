/** Small building blocks of the technical layer: key/value rows, tables, the model line. */
import { useContext, useState, type ReactNode } from 'react'
import { getEntity, getItem, type DiffEntry, type MemoryDiffEvent, type ModelCallEvent } from '../api/client'
import { useItemActions } from '../components/itemContext'
import { HeldContext, modelFacts } from './context'
import { formatValue, shortId, truncate } from '../lib/format'
import { Button, DiffRow, Overline, cx, type DiffGlyph } from '../ui'

export function KV({ rows, testId }: { rows: [string, ReactNode][]; testId?: string }) {
  return (
    <dl className="kv-grid m-0 font-machine text-mono" data-testid={testId}>
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-fg-3">{k}</dt>
          <dd className="m-0 break-words text-fg-2">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

type Row = { key: string; cells: ReactNode[]; testId?: string; attrs?: Record<string, string> }

/** A small machine table that scrolls in its own container (the page never scrolls sideways). */
export function MTable({ head, rows }: { head: string[]; rows: Row[] }) {
  return (
    <div className="scroll-thin my-0.5 overflow-x-auto" tabIndex={0} role="region" aria-label={`${head.join(', ')} table`}>
      <table className="min-w-full border-collapse font-machine text-mono-sm text-fg-2">
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h} scope="col" className="whitespace-nowrap border-b border-line pb-1.5 pr-3.5 text-left font-medium text-fg-3">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} data-testid={r.testId} {...r.attrs} className="border-b border-line last:border-b-0">
              {r.cells.map((c, i) => (
                <td key={i} className="whitespace-nowrap py-1.5 pr-3.5">
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Machine facts in outlined pills: model, prompt version, tokens, time. */
export function FactLine({ facts, testId }: { facts: ReactNode[]; testId?: string }) {
  if (facts.length === 0) return null
  return (
    <div className="mt-3 flex flex-wrap gap-1.5" data-testid={testId}>
      {facts.map((f, i) => (
        <span key={i} className="rounded-full px-2 py-0.5 font-machine text-mono-sm text-fg-3 ring-1 ring-inset ring-line">
          {f}
        </span>
      ))}
    </div>
  )
}

export function ModelLines({ calls }: { calls: ModelCallEvent[] }) {
  return (
    <>
      {calls.map((call, i) => (
        <div key={`${call.step}-${String(i)}`}>
          <FactLine facts={modelFacts(call)} />
          {call.fallback && (
            <p className="mt-2 text-label font-normal text-warn" data-testid="fallback">
              Answered by the fallback model. The primary ({call.fallback.from_provider} · {call.fallback.from_model}) didn't
              answer: {call.fallback.reason}
            </p>
          )}
        </div>
      ))}
    </>
  )
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-4 first:mt-0">
      <Overline className="mb-2">{title}</Overline>
      {children}
    </section>
  )
}

// ------------------------------------------------------------------ memory diff

const GLYPH: Record<DiffEntry['op'], DiffGlyph> = {
  added: '+',
  updated: '~',
  superseded: '~',
  fulfilled: '~',
  corrected: '~',
  removed: '−',
  held: '⏸',
  not_written: '∅',
  conflict: '!',
}

const LAYERS = ['core', 'quick', 'archive'] as const

/** The memory changes of a turn, grouped by layer, built from the stored write log. */
export function DiffList({ diff, only }: { diff: MemoryDiffEvent; only?: (e: DiffEntry) => boolean }) {
  const entries = diff.entries.filter((e) => (only ? only(e) : true))
  return (
    <div className="grid gap-3">
      {LAYERS.map((layer) => {
        const inLayer = entries.filter((e) => e.layer === layer)
        if (inLayer.length === 0) return null
        return (
          <ul key={layer} className="m-0 grid list-none gap-2 p-0" data-testid={`diff-layer-${layer}`}>
            {inLayer.map((entry, i) => (
              <Entry key={`${entry.op}-${entry.item_id ?? entry.entity_id ?? entry.title}-${String(i)}`} entry={entry} />
            ))}
          </ul>
        )
      })}
    </div>
  )
}

function Entry({ entry }: { entry: DiffEntry }) {
  const changes = entry.op === 'added' ? entry.changes.filter((c) => c.after !== null) : entry.changes
  return (
    <DiffRow
      glyph={GLYPH[entry.op]}
      layer={entry.layer}
      title={entry.title}
      strike={entry.op === 'superseded' || entry.op === 'removed' || entry.op === 'corrected'}
      note={entry.op.replace('_', ' ')}
      testId="diff-entry"
      op={entry.op}
    >
      {changes.length > 0 && (
        <ul className="m-0 mt-1 list-none p-0 font-machine text-mono-sm text-fg-3">
          {changes.map((c) => (
            <li key={c.field} className="break-words">
              {c.field}{' '}
              {entry.op === 'added' ? (
                <span className="text-fg-2">{truncate(formatValue(c.after))}</span>
              ) : (
                <span className="text-fg-2">
                  {truncate(formatValue(c.before), 60)} → {truncate(formatValue(c.after), 60)}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {entry.reason && (
        <p className="m-0 mt-1 text-label font-normal text-fg-2">
          {entry.reason}
          {entry.rule_id && <span className="font-machine text-mono-sm text-fg-3"> ({entry.rule_id})</span>}
        </p>
      )}
      {entry.reconcile && entry.reconcile.decision !== 'new' && (
        <p className="m-0 mt-1 font-machine text-mono-sm text-fg-3">
          reconciled: {entry.reconcile.decision.replace('_', ' ')}
          {entry.reconcile.candidate_title && ` “${entry.reconcile.candidate_title}”`}
          {entry.reconcile.score != null && ` · ${entry.reconcile.score.toFixed(2)}`}
        </p>
      )}
      {entry.op === 'held' && entry.held_write_id && <HeldControls heldId={entry.held_write_id} />}
      {entry.item_id && entry.op !== 'removed' && entry.op !== 'held' && entry.op !== 'not_written' && <EditButton itemId={entry.item_id} />}
      <Detail entry={entry} />
    </DiffRow>
  )
}

export function HeldControls({ heldId }: { heldId: string }) {
  const actions = useContext(HeldContext)
  if (!actions) return null
  const record = actions.held.get(heldId)
  if (record && record.status !== 'pending') {
    return (
      <p className="m-0 mt-2 text-label font-normal text-fg-2" data-testid="held-status">
        {record.status === 'confirmed' ? 'Confirmed' : 'Rejected'}
        {record.resolved_turn_id && ` in turn ${shortId(record.resolved_turn_id)}`}.
      </p>
    )
  }
  return (
    <div className="mt-3 flex flex-wrap gap-2">
      <Button
        variant="primary"
        size="sm"
        disabled={actions.busy !== null}
        onClick={() => {
          actions.confirm(heldId)
        }}
        data-testid="held-confirm"
      >
        {actions.busy === heldId ? 'Working…' : 'Confirm'}
      </Button>
      <Button
        variant="secondary"
        size="sm"
        disabled={actions.busy !== null}
        onClick={() => {
          actions.reject(heldId)
        }}
        data-testid="held-reject"
      >
        Reject
      </Button>
    </div>
  )
}

/** Opens the memory's editor (S3.12); the edit runs as its own undoable turn. */
function EditButton({ itemId }: { itemId: string }) {
  const items = useItemActions()
  if (!items) return null
  return (
    <Button
      variant="ghost"
      size="sm"
      className="-ml-3"
      onClick={() => {
        items.open(itemId)
      }}
      data-testid="diff-edit"
    >
      Edit
    </Button>
  )
}

/** The one-tap fix for an assumed date (FR-1.4): the editor opens on the date. */
export function FixDate({ itemId }: { itemId: string }) {
  const items = useItemActions()
  if (!items) return null
  return (
    <>
      {' '}
      <Button
        variant="ghost"
        size="sm"
        onClick={() => {
          items.open(itemId, 'date')
        }}
        data-testid="fix-date"
      >
        Fix the date
      </Button>
    </>
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
    <div className="mt-1">
      <Button variant="ghost" size="sm" className="-ml-3" aria-expanded={json !== null} onClick={() => void toggle()}>
        {json !== null ? 'Hide' : 'View'} {target.kind} JSON
      </Button>
      {error && (
        <p role="alert" className="m-0 text-label text-bad">
          {error}
        </p>
      )}
      {json !== null && (
        <pre
          className={cx('scroll-thin m-0 mt-1 max-h-64 overflow-auto rounded-sm bg-canvas p-3 font-machine text-mono-sm text-fg-2 ring-1 ring-inset ring-line')}
          data-testid="detail-json"
        >
          {json}
        </pre>
      )}
    </div>
  )
}
