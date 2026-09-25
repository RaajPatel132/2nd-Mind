import type { ToolCallEvent } from '../../api/client'
import { truncate } from '../../lib/format'
import { Chip } from './Panel'

const TONES = { allowed: 'green', held: 'amber', blocked: 'red' } as const

/** Each writer op in order, with its arguments, result and policy decision. */
export function ToolCallsBody({ calls }: { calls: ToolCallEvent[] }) {
  return (
    <ol className="space-y-2 text-sm">
      {calls.map((call, i) => (
        <li key={`${call.tool}-${String(i)}`} data-testid="tool-call" className="rounded-md border border-slate-100 p-2">
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="text-xs tabular-nums text-slate-500">{i + 1}</span>
            <span className="font-mono text-slate-900">{call.tool}</span>
            {call.policy && (
              <span className="ml-auto" data-testid="tool-call-policy">
                <Chip tone={TONES[call.policy.decision]}>
                  {call.policy.decision} · {call.policy.rule_id}
                </Chip>
              </span>
            )}
          </div>
          {Object.keys(call.arguments).length > 0 && (
            <dl className="mt-1 grid grid-cols-[max-content_1fr] gap-x-2 text-xs">
              {Object.entries(call.arguments).map(([key, value]) => (
                <div key={key} className="contents">
                  <dt className="text-slate-500">{key}</dt>
                  <dd className="break-all font-mono text-slate-800">{truncate(value, 90)}</dd>
                </div>
              ))}
            </dl>
          )}
          {call.result_summary && <p className="mt-1 text-xs text-slate-700">→ {call.result_summary}</p>}
          {call.policy && call.policy.decision !== 'allowed' && (
            <p className="text-xs text-slate-600">{call.policy.reason}</p>
          )}
        </li>
      ))}
    </ol>
  )
}
