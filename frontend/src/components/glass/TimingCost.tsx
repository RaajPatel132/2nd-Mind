import type { ModelCallEvent, Turn } from '../../api/client'
import { formatMs, formatTokens, formatUsd } from '../../lib/format'
import { Stat } from './Panel'

export function TimingCost({ turn, calls }: { turn: Turn; calls: ModelCallEvent[] }) {
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
                {call.cache_hits != null && <Stat label="Cache hits" value={String(call.cache_hits)} testId="call-cache-hits" />}
              </dl>
              {call.fallback && (
                <p className="rounded bg-amber-50 px-2 py-1 text-xs text-amber-900" data-testid="fallback">
                  Answered by fallback. The primary model ({call.fallback.from_provider} ·{' '}
                  {call.fallback.from_model}) did not answer: {call.fallback.reason}
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
