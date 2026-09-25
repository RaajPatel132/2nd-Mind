import { useEffect, useState, type RefObject } from 'react'
import {
  confirmHeldWrite,
  getTurnEvents,
  listHeldWrites,
  rejectHeldWrite,
  undoTurn,
  type DecisionEvent,
  type HeldWrite,
  type IntentEvent,
  type MemoryDiffEvent,
  type ModelCallEvent,
  type StoredEvent,
  type ToolCallEvent,
  type Turn,
  type TurnEvent,
} from '../api/client'
import { plural, shortId } from '../lib/format'
import { DecisionBody } from './glass/DecisionPanel'
import { DiffBody } from './glass/DiffPanel'
import { Panel } from './glass/Panel'
import { TimingCost } from './glass/TimingCost'
import { ToolCallsBody } from './glass/ToolCallsPanel'

type Loaded = { key: string; events: StoredEvent[] | null; error: string | null }

type Props = {
  turn: Turn | null
  pending: boolean
  workspaceId: string
  regionRef: RefObject<HTMLElement | null>
  /** A new turn made from the glass box (an undo or a confirmation) to show in the chat. */
  onTurnCreated: (turn: Turn) => void
  onClose?: () => void
}

export function GlassBox({ turn, pending, workspaceId, regionRef, onTurnCreated, onClose }: Props) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const turnId = turn?.id
  const turnStatus = turn?.status
  const key = turnId ? `${turnId}:${turnStatus ?? ''}` : null

  useEffect(() => {
    if (!turnId || !key) return
    let cancelled = false
    getTurnEvents(turnId)
      .then((events) => {
        if (!cancelled) setLoaded({ key, events, error: null })
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : 'Could not load this turn.'
        if (!cancelled) setLoaded({ key, events: null, error: message })
      })
    return () => {
      cancelled = true
    }
  }, [turnId, key])

  // Only show what was loaded for the turn currently selected.
  const current = loaded?.key === key ? loaded : null
  const events = current?.events ?? null
  const error = current?.error ?? null

  return (
    <section
      ref={regionRef}
      tabIndex={-1}
      aria-label="Glass box"
      data-testid="glass-box"
      className="flex h-full min-h-0 flex-col bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-500"
    >
      <header className="flex items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-slate-900">Glass box</h2>
          <p className="truncate text-xs text-slate-600">
            {turn ? `Turn ${shortId(turn.id)} · ${kindLabel(turn)}${turn.status}` : 'Select a reply to see how it was made'}
          </p>
        </div>
        {onClose && (
          <button type="button" onClick={onClose} className="btn-chip lg:hidden" aria-label="Close glass box">
            Close
          </button>
        )}
      </header>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
        {!turn && (
          <p className="text-sm text-slate-600">
            {pending ? 'A turn is in progress…' : 'No turn selected yet. Send a message to start.'}
          </p>
        )}
        {turn && error && (
          <p role="alert" className="text-sm text-red-700">
            {error}
          </p>
        )}
        {turn && !error && (
          <Panels key={turn.id} turn={turn} events={events} workspaceId={workspaceId} onTurnCreated={onTurnCreated} />
        )}
      </div>
    </section>
  )
}

function kindLabel(turn: Turn): string {
  return turn.kind === 'user' ? '' : `${turn.kind} · `
}

type PanelsProps = {
  turn: Turn
  events: StoredEvent[] | null
  workspaceId: string
  onTurnCreated: (turn: Turn) => void
}

function Panels({ turn, events, workspaceId, onTurnCreated }: PanelsProps) {
  const list: TurnEvent[] = events?.map((e) => e.event) ?? []
  const intent = list.find((e): e is IntentEvent => e.type === 'intent')
  const decision = list.find((e): e is DecisionEvent => e.type === 'decision')
  const diff = list.find((e): e is MemoryDiffEvent => e.type === 'memory_diff')
  const toolCalls = list.filter((e): e is ToolCallEvent => e.type === 'tool_call')
  const calls = list.filter((e): e is ModelCallEvent => e.type === 'model_call')
  const errorEvent = list.find((e) => e.type === 'error')
  const hasDiff = diff !== undefined && diff.entries.length > 0
  const actions = useMemoryActions(workspaceId, diff, onTurnCreated)

  return (
    <>
      {turn.error && (
        <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-900">
          <strong className="font-semibold">Turn failed.</strong> {turn.error.message}{' '}
          <code className="text-xs">({errorEvent?.type === 'error' ? errorEvent.code : turn.error.code})</code>
        </p>
      )}
      {actions.error && (
        <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-900">
          {actions.error}
        </p>
      )}
      <Panel n={1} title="Decision" open={decision !== undefined} reason={decisionReason(turn, intent)} testId="panel-decision">
        {(intent ?? decision) && <DecisionBody intent={intent} decision={decision} />}
      </Panel>
      <Panel n={2} title="Memory diff" open={hasDiff} reason="No memory changes this turn." testId="panel-diff">
        {hasDiff && (
          <DiffBody
            diff={diff}
            held={actions.held}
            busy={actions.busy}
            onConfirm={(id) => void actions.confirm(id)}
            onReject={(id) => void actions.reject(id)}
          />
        )}
      </Panel>
      <Panel n={3} title="Retrieval" reason="Nothing was retrieved this turn." />
      <Panel n={4} title="Tool calls" open={toolCalls.length > 0} reason="No tool calls this turn." testId="panel-tools">
        {toolCalls.length > 0 && <ToolCallsBody calls={toolCalls} />}
      </Panel>
      <Panel n={5} title="Timing & cost" open testId="timing-cost">
        {turn.status === 'running' ? (
          <p className="text-sm text-slate-600">
            This turn is still running. Timing, cost and the trace link appear when it finishes.
          </p>
        ) : events === null ? (
          <p className="text-sm text-slate-600">Loading…</p>
        ) : (
          <TimingCost turn={turn} calls={calls} />
        )}
      </Panel>
      {diff && <UndoControl turn={turn} diff={diff} busy={actions.busy !== null} onUndo={() => actions.undo(turn.id)} />}
    </>
  )
}

function decisionReason(turn: Turn, intent: IntentEvent | undefined): string {
  if (turn.kind === 'undo') return 'An undo replays the write log backwards, so nothing new was decided.'
  if (turn.kind === 'confirm') return 'A confirmation applies a change you approved; nothing new was decided.'
  if (turn.kind === 'system') return 'Housekeeping by the system; no model decided anything.'
  if (intent?.intent === 'recall') return "Recall isn't wired up yet, so nothing was looked up or saved."
  if (intent?.intent === 'correct') return "Correcting by chat isn't wired up yet, so nothing was changed."
  return 'Chit-chat: nothing was classified, dated or linked this turn.'
}

const CHANGING = new Set(['added', 'updated', 'removed', 'superseded', 'fulfilled'])

/** How many distinct memories and entities a turn changed. */
function touched(diff: MemoryDiffEvent): number {
  const ids = new Set<string>()
  for (const entry of diff.entries) {
    if (!CHANGING.has(entry.op)) continue
    ids.add(entry.item_id ?? entry.entity_id ?? entry.title)
  }
  return ids.size
}

function UndoControl({ turn, diff, busy, onUndo }: { turn: Turn; diff: MemoryDiffEvent; busy: boolean; onUndo: () => Promise<void> }) {
  const [asking, setAsking] = useState(false)
  const count = touched(diff)
  if (count === 0 || turn.status !== 'completed') return null
  const label = turn.kind === 'undo' ? 'Redo (undo this undo)' : 'Undo this turn'

  if (asking) {
    return (
      <div role="group" aria-label="Confirm undo" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
        <p>This turn changed {plural(count, 'memory', 'memories')}. Undo all of them?</p>
        <div className="mt-2 flex gap-2">
          <button
            type="button"
            className="btn-primary"
            disabled={busy}
            onClick={() => {
              setAsking(false)
              void onUndo()
            }}
            data-testid="undo-confirm"
          >
            Undo all
          </button>
          <button type="button" className="btn-chip" onClick={() => { setAsking(false) }}>
            Cancel
          </button>
        </div>
      </div>
    )
  }
  return (
    <button
      type="button"
      className="btn-chip"
      disabled={busy}
      onClick={() => {
        if (count > 1) setAsking(true)
        else void onUndo()
      }}
      data-testid="undo-turn"
    >
      ↶ {label}
    </button>
  )
}

/** Held-write status for the diff, and the undo / confirm / reject actions. */
function useMemoryActions(workspaceId: string, diff: MemoryDiffEvent | undefined, onTurnCreated: (turn: Turn) => void) {
  const [held, setHeld] = useState<Map<string, HeldWrite>>(new Map())
  const [version, setVersion] = useState(0)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const hasHeld = diff?.entries.some((e) => e.op === 'held') ?? false

  useEffect(() => {
    if (!hasHeld) return
    let cancelled = false
    listHeldWrites(workspaceId)
      .then((items) => {
        if (!cancelled) setHeld(new Map(items.map((h) => [h.id, h])))
      })
      .catch(() => {
        // Without statuses every held entry still offers Confirm / Reject; the server decides.
      })
    return () => {
      cancelled = true
    }
  }, [workspaceId, hasHeld, version])

  async function run(id: string, action: () => Promise<void>) {
    setBusy(id)
    setError(null)
    try {
      await action()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'That did not work.')
    } finally {
      setBusy(null)
      setVersion((v) => v + 1)
    }
  }

  return {
    held,
    busy,
    error,
    undo: (turnId: string) =>
      run(turnId, async () => {
        onTurnCreated(await undoTurn(turnId))
      }),
    confirm: (heldId: string) =>
      run(heldId, async () => {
        onTurnCreated(await confirmHeldWrite(heldId))
      }),
    reject: (heldId: string) =>
      run(heldId, async () => {
        await rejectHeldWrite(heldId)
      }),
  }
}
