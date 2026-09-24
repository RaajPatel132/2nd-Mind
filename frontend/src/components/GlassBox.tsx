import { useEffect, useState, type ReactNode, type RefObject } from 'react'
import { getTurnEvents, type ModelCallEvent, type StoredEvent, type Turn, type TurnEvent } from '../api/client'
import { formatMs, formatTokens, formatUsd, shortId } from '../lib/format'

type Loaded = { key: string; events: StoredEvent[] | null; error: string | null }

type Props = {
  turn: Turn | null
  pending: boolean
  regionRef: RefObject<HTMLElement | null>
  onClose?: () => void
}

export function GlassBox({ turn, pending, regionRef, onClose }: Props) {
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
            {turn ? `Turn ${shortId(turn.id)} · ${turn.status}` : 'Select a reply to see how it was made'}
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
        {turn && !error && <Panels turn={turn} events={events} />}
      </div>
    </section>
  )
}

function Panels({ turn, events }: { turn: Turn; events: StoredEvent[] | null }) {
  const list: TurnEvent[] = events?.map((e) => e.event) ?? []
  const intent = list.find((e) => e.type === 'intent')
  const calls = list.filter((e): e is ModelCallEvent => e.type === 'model_call')
  const errorEvent = list.find((e) => e.type === 'error')
  const has = (type: TurnEvent['type']) => list.some((e) => e.type === type)

  return (
    <>
      {turn.error && (
        <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-900">
          <strong className="font-semibold">Turn failed.</strong> {turn.error.message}{' '}
          <code className="text-xs">({errorEvent?.type === 'error' ? errorEvent.code : turn.error.code})</code>
        </p>
      )}
      <Panel
        n={1}
        title="Decision"
        reason={
          has('decision')
            ? 'Decisions were recorded this turn.'
            : 'Chit-chat: nothing was classified, dated or linked this turn.'
        }
      >
        {intent?.type === 'intent' && (
          <p className="text-sm text-slate-700">
            Intent <strong>{intent.intent.replace('_', '-')}</strong> ({intent.source}): {intent.reason}
          </p>
        )}
      </Panel>
      <Panel n={2} title="Memory diff" reason="No memory changes this turn." />
      <Panel n={3} title="Retrieval" reason="Nothing was retrieved this turn." />
      <Panel n={4} title="Tool calls" reason="No tool calls this turn." />
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
    </>
  )
}

function Panel(props: { n: number; title: string; reason?: string; open?: boolean; testId?: string; children?: ReactNode }) {
  return (
    <details open={props.open} className="group rounded-lg border border-slate-200 bg-white" data-testid={props.testId}>
      <summary className="flex cursor-pointer list-none items-baseline gap-2 rounded-lg px-3 py-2.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500">
        <span className="text-xs tabular-nums text-slate-500">{props.n}</span>
        <span className="shrink-0 whitespace-nowrap text-sm font-semibold text-slate-900">{props.title}</span>
        {props.reason && !props.open && <span className="min-w-0 truncate text-xs text-slate-600">— {props.reason}</span>}
        <span aria-hidden className="ml-auto text-xs text-slate-500 group-open:rotate-90">▸</span>
      </summary>
      <div className="border-t border-slate-100 px-3 py-3">
        {props.children ?? <p className="text-sm text-slate-600">{props.reason}</p>}
      </div>
    </details>
  )
}

function TimingCost({ turn, calls }: { turn: Turn; calls: ModelCallEvent[] }) {
  const start = Date.parse(turn.started_at)
  const end = turn.finished_at ? Date.parse(turn.finished_at) : start
  const total = Math.max(end - start, ...calls.map((c) => Date.parse(c.started_at) - start + c.latency_ms), 1)

  return (
    <div className="space-y-4 text-sm">
      {calls.length === 0 && <p className="text-slate-600">No model calls were completed this turn.</p>}
      <ul className="space-y-3">
        {calls.map((call, i) => {
          const offset = Math.max(0, Date.parse(call.started_at) - start)
          return (
            <li key={`${call.step}-${String(i)}`} data-testid="model-call" className="space-y-1.5">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
                <span className="font-medium text-slate-900">{call.step}</span>
                <span className="font-mono text-xs text-slate-700" data-testid="model-call-model">
                  {call.provider} · {call.model}
                </span>
              </div>
              <div className="h-2 rounded bg-slate-100" role="img" aria-label={`Ran ${formatMs(call.latency_ms)} of ${formatMs(total)}`}>
                <div
                  className="h-2 rounded bg-indigo-500"
                  style={{ marginLeft: `${String((offset / total) * 100)}%`, width: `${String(Math.max(2, (call.latency_ms / total) * 100))}%` }}
                />
              </div>
              <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs text-slate-700 sm:grid-cols-4">
                <Stat label="Latency" value={formatMs(call.latency_ms)} testId="call-latency" />
                <Stat
                  label="First token"
                  value={call.time_to_first_token_ms != null ? formatMs(call.time_to_first_token_ms) : '—'}
                />
                <Stat
                  label="Tokens"
                  value={`${formatTokens(call.usage.input_tokens)} in · ${formatTokens(call.usage.cached_input_tokens)} cached · ${formatTokens(call.usage.output_tokens)} out`}
                  wide
                />
                <Stat label="Cost" value={formatUsd(call.usage.cost_usd)} testId="call-cost" />
                <Stat label="Attempts" value={String(call.attempts)} />
                <Stat label="Prompt" value={call.prompt ?? '—'} />
              </dl>
              {call.fallback && (
                <p className="rounded bg-amber-50 px-2 py-1 text-xs text-amber-900" data-testid="fallback">
                  Answered by fallback. {call.fallback.from_provider} · {call.fallback.from_model} was skipped:{' '}
                  {call.fallback.reason}
                </p>
              )}
            </li>
          )
        })}
      </ul>
      <div className="rounded-md bg-slate-50 p-3" data-testid="turn-totals">
        <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-600">Turn total</p>
        <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs text-slate-800 sm:grid-cols-3">
          <Stat label="Wall time" value={formatMs(end - start)} />
          <Stat
            label="Tokens"
            value={formatTokens(turn.usage.input_tokens + turn.usage.cached_input_tokens + turn.usage.output_tokens)}
          />
          <Stat label="Cost" value={formatUsd(turn.usage.cost_usd)} testId="turn-cost" />
        </dl>
      </div>
      <dl className="space-y-1 text-xs text-slate-700">
        <div className="flex flex-wrap gap-x-2">
          <dt className="text-slate-500">Prompts</dt>
          <dd className="font-mono">{turn.prompt_versions.join(', ') || '—'}</dd>
        </div>
        <div className="flex flex-wrap gap-x-2">
          <dt className="text-slate-500">Config</dt>
          <dd className="font-mono" title={turn.config_hash}>
            {turn.config_hash.slice(0, 12)}
          </dd>
        </div>
        <div className="flex flex-wrap gap-x-2" data-testid="trace-link">
          <dt className="text-slate-500">Trace</dt>
          <dd>
            {turn.trace.url ? (
              <a href={turn.trace.url} target="_blank" rel="noreferrer" className="btn-link">
                Open in Langfuse ↗
              </a>
            ) : turn.trace.status === 'unavailable' ? (
              'Unavailable (trace backend was unreachable)'
            ) : (
              'Tracing is off'
            )}
          </dd>
        </div>
      </dl>
    </div>
  )
}

function Stat({ label, value, wide, testId }: { label: string; value: string; wide?: boolean; testId?: string }) {
  return (
    <div className={wide ? 'col-span-2' : undefined}>
      <dt className="text-slate-500">{label}</dt>
      <dd className="tabular-nums text-slate-900" data-testid={testId}>
        {value}
      </dd>
    </div>
  )
}
