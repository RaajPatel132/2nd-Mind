/**
 * The Inspector: the whole glass box for one turn (FR-9), in the five panels in their order, built
 * from the same detail components as the Trail. Panels that don't apply are collapsed with their
 * one-line reason (FR-9.1).
 */
import { ChevronRight, X } from 'lucide-react'
import { useId, useMemo, useState, type ReactNode } from 'react'
import type { AgentStep, ModelCallEvent, Usage } from '../api/client'
import type { ChatTurn } from '../hooks/useConversation'
import { formatClock, formatMs, formatSeconds, formatTokens, formatUsd, shortId } from '../lib/format'
import { DiffTech, ToolCalls } from '../trail/details'
import { factsOf, stepViews, type Facts, type StepView } from '../trail/model'
import { STEPS } from '../trail/steps'
import { stepContext, stepLabel } from '../trail/view'
import { Disclosure, DisclosureTrigger, Icon, IconButton, Overline, Sheet, cx, type SheetMode } from '../ui'

type Props = {
  turn: ChatTurn | null
  open: boolean
  mode: SheetMode
  onClose: () => void
  timezone: string
  quotaNow: Usage | null
}

export function Inspector({ turn, open, mode, onClose, timezone, quotaNow }: Props) {
  return (
    <Sheet open={open && turn !== null} mode={mode} onClose={onClose} label="Inspector" testId="inspector">
      {turn && <Body turn={turn} onClose={onClose} timezone={timezone} quotaNow={quotaNow} />}
    </Sheet>
  )
}

const DECISION_STEPS: AgentStep[] = ['understand', 'extract', 'dates', 'entities', 'reconcile']

function Body({ turn, onClose, timezone, quotaNow }: { turn: ChatTurn; onClose: () => void; timezone: string; quotaNow: Usage | null }) {
  const events = useMemo(() => turn.events ?? [], [turn.events])
  const views = useMemo(() => stepViews(events, turn.starts), [events, turn.starts])
  const facts = useMemo(() => factsOf(events), [events])
  const t = turn.turn
  const kind = turn.kind === 'user' ? (facts.intent?.intent.replaceAll('_', '-') ?? 'turn') : turn.kind

  return (
    <>
      <div className="flex items-start gap-3 border-b border-line px-5 pb-3.5 pt-4">
        <div className="min-w-0 flex-1">
          <Overline>Inspector</Overline>
          <h2 className="m-0 mt-0.5 font-voice text-title-lg text-fg">Turn {turn.id ? shortId(turn.id) : '…'}</h2>
          <p className="m-0 font-machine text-mono-sm text-fg-3">
            {kind} · {turn.status} · {formatClock(turn.sentAt)}
          </p>
        </div>
        <IconButton icon={X} label="Close inspector" size="sm" onClick={onClose} tooltipSide="bottom" />
      </div>
      <div className="scroll-thin flex-1 overflow-y-auto px-5 pb-7 pt-1.5" data-testid="glass-box">
        {turn.error && (
          <p role="alert" className="m-0 mt-4 border-l-2 border-bad pl-3 text-label font-normal text-fg">
            This turn failed: {turn.error}{' '}
            <span className="font-machine text-mono-sm text-fg-3">({facts.error?.code ?? t?.error?.code ?? 'error'})</span>
          </p>
        )}
        <DecisionPanel turn={turn} views={views} facts={facts} timezone={timezone} />
        <Panel n={2} title="Memory diff" testId="panel-diff" reason="No memory changes this turn." applies={Boolean(facts.diff?.entries.length)}>
          {facts.diff && <DiffTech ctx={ctxFor('save', views, facts, turn, timezone)} />}
        </Panel>
        <Panel n={3} title="Retrieval" testId="panel-retrieval" reason="Nothing was retrieved this turn." applies={false} />
        <Panel n={4} title="Tool calls" testId="panel-tools" reason="No tool calls this turn." applies={facts.tools.length > 0}>
          <ToolCalls ctx={ctxFor('guard', views, facts, turn, timezone)} />
        </Panel>
        <Panel n={5} title="Timing & cost" testId="timing-cost" applies>
          <Timing turn={turn} views={views} facts={facts} quotaNow={quotaNow} />
        </Panel>
      </div>
    </>
  )
}

function ctxFor(step: AgentStep, views: StepView[], facts: Facts, turn: ChatTurn, timezone: string) {
  const view: StepView = views.find((v) => v.step === step) ?? { key: step, step, state: 'done', startedAt: turn.sentAt, latencyMs: null, own: [] }
  return stepContext(view, facts, turn.turn, timezone)
}

function decisionReason(turn: ChatTurn, facts: Facts): string {
  if (turn.kind === 'undo') return 'An undo replays the write log backwards, so nothing new was decided.'
  if (turn.kind === 'confirm') return 'A confirmation applies a change you approved; nothing new was decided.'
  if (turn.kind === 'system') return 'Housekeeping by the system; no model decided anything.'
  if (facts.intent?.intent === 'recall') return "Recall isn't wired up yet, so nothing was looked up or saved."
  if (facts.intent?.intent === 'correct') return "Correcting by chat isn't wired up yet, so nothing was changed."
  return 'Chit-chat: nothing was classified, dated or linked this turn.'
}

function DecisionPanel({ turn, views, facts, timezone }: { turn: ChatTurn; views: StepView[]; facts: Facts; timezone: string }) {
  const d = facts.decision
  const applies = Boolean(d)
  const present = (step: AgentStep) =>
    views.some((v) => v.step === step) ||
    (step === 'understand' && facts.intent !== undefined) ||
    (step === 'extract' && Boolean(d?.classifications.length)) ||
    (step === 'dates' && Boolean(d?.time_resolutions.length)) ||
    (step === 'entities' && Boolean(d?.entity_resolutions.length)) ||
    (step === 'reconcile' && Boolean(d?.reconciliations.length))
  return (
    <Panel n={1} title="Decision" testId="panel-decision" reason={decisionReason(turn, facts)} applies={applies}>
      {DECISION_STEPS.filter(present).map((step) => {
        const ctx = ctxFor(step, views, facts, turn, timezone)
        const spec = STEPS[step]
        return (
          <section key={step} className="mt-4 first:mt-0">
            <p className="m-0 mb-2 flex items-center gap-2 text-label text-fg">
              <Icon icon={spec.icon} className="text-fg-3" />
              {stepLabel(ctx)}
            </p>
            <spec.Tech ctx={ctx} />
          </section>
        )
      })}
    </Panel>
  )
}

function Panel({ n, title, reason, applies, testId, children }: { n: number; title: string; reason?: string; applies: boolean; testId: string; children?: ReactNode }) {
  const id = useId()
  const [open, setOpen] = useState(applies)
  return (
    <section className="border-b border-line py-4 last:border-b-0" data-testid={testId} data-open={open}>
      <h3 className="m-0">
        <DisclosureTrigger
          open={open}
          controls={id}
          onClick={() => {
            setOpen((o) => !o)
          }}
          className="flex w-full cursor-pointer items-center gap-2.5 rounded-xs text-left font-ui text-overline uppercase text-fg-2"
        >
          <span className="font-machine text-mono-sm normal-case tracking-normal text-fg-3">{n}</span>
          <span className={cx(!applies && 'text-fg-3')}>{title}</span>
          {!open && reason && <span className="min-w-0 truncate font-ui text-label font-normal normal-case tracking-normal text-fg-3">{reason}</span>}
          <Icon icon={ChevronRight} className={cx('ml-auto shrink-0 text-fg-3 transition-transform dur-3', open && 'rotate-90')} />
        </DisclosureTrigger>
      </h3>
      <Disclosure id={id} open={open}>
        <div className="pt-3">{children ?? <p className="m-0 text-label font-normal text-fg-3">{reason}</p>}</div>
      </Disclosure>
    </section>
  )
}

// ------------------------------------------------------------------ timing & cost

function Timing({ turn, views, facts, quotaNow }: { turn: ChatTurn; views: StepView[]; facts: Facts; quotaNow: Usage | null }) {
  const t = turn.turn
  if (!t || turn.status === 'running' || turn.status === 'streaming') {
    return <p className="m-0 text-label font-normal text-fg-3">This turn is still running. Timing, cost and the trace link appear when it finishes.</p>
  }
  const u = t.usage
  const tokens = u.input_tokens + u.cached_input_tokens + u.output_tokens
  const start = Date.parse(t.started_at)
  const end = t.finished_at ? Date.parse(t.finished_at) : start
  const quota = turn.quotaAfter
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-baseline gap-x-3.5 gap-y-1.5 font-machine text-mono-sm text-fg-3 tnum" data-testid="turn-totals">
        <span className="font-voice text-title-lg text-fg">{formatSeconds(end - start)}</span>
        <span>{tokens ? `${formatTokens(tokens)} tokens` : 'no tokens'}</span>
        <span data-testid="turn-cost">{formatUsd(u.cost_usd)}</span>
        <span data-testid="turn-charged">−{formatTokens(u.charged_tokens)} quota</span>
      </div>
      <Waterfall views={views} start={start} end={end} />
      <ModelTable calls={facts.calls} />
      <p className="m-0 text-label font-normal text-fg-2" data-testid="quota-after">
        {quota ? (
          <>
            Quota after this turn: <b className="font-machine text-mono font-medium text-fg">{formatTokens(quota.remaining_tokens)}</b> tokens left (
            {((quota.remaining_tokens / Math.max(1, quota.limit_tokens)) * 100).toFixed(1)}%)
          </>
        ) : quotaNow ? (
          <>
            Quota now: <b className="font-machine text-mono font-medium text-fg">{formatTokens(quotaNow.remaining_tokens)}</b> tokens left (
            {((quotaNow.remaining_tokens / Math.max(1, quotaNow.limit_tokens)) * 100).toFixed(1)}%)
          </>
        ) : (
          'Quota is loading.'
        )}
      </p>
      <dl className="kv-grid m-0 font-machine text-mono-sm">
        <dt className="text-fg-3">prompts</dt>
        <dd className="m-0 break-words text-fg-2">{t.prompt_versions.join(', ') || '—'}</dd>
        <dt className="text-fg-3">config</dt>
        <dd className="m-0 text-fg-2" title={t.config_hash}>
          {t.config_hash.slice(0, 12)}
        </dd>
        <dt className="text-fg-3">trace</dt>
        <dd className="m-0 text-fg-2" data-testid="trace-link">
          {t.trace.url ? (
            <a href={t.trace.url} target="_blank" rel="noreferrer" className="text-fg underline underline-offset-2">
              Open in Langfuse ↗
            </a>
          ) : t.trace.status === 'unavailable' ? (
            'unavailable (the trace backend was unreachable)'
          ) : (
            'tracing is off'
          )}
        </dd>
      </dl>
    </div>
  )
}

/** Monochrome bars on one time axis, drawn to scale; the answer is the one bar in fg. */
function Waterfall({ views, start, end }: { views: StepView[]; start: number; end: number }) {
  const rows = views.filter((v) => v.latencyMs != null)
  if (rows.length === 0) return null
  const total = Math.max(
    end - start,
    ...rows.map((v) => Date.parse(v.startedAt) - start + (v.latencyMs ?? 0)),
    1,
  )
  const tick = [100, 250, 500, 1000, 2000, 5000, 10000].find((s) => total / s <= 5) ?? 20000
  const ticks: number[] = []
  for (let x = 0; x <= total; x += tick) ticks.push(x)
  return (
    <div className="grid gap-1.5" role="img" aria-label={`Time per step, to scale, over ${formatSeconds(total)}`} data-testid="waterfall">
      {rows.map((v) => {
        const left = Math.max(0, Date.parse(v.startedAt) - start)
        const width = v.latencyMs ?? 0
        return (
          <div key={v.key} className="wf-grid" data-testid="waterfall-row">
            <span className="truncate font-machine text-mono-sm text-fg-3">{v.step}</span>
            <span className="wf-track relative h-3">
              <span
                className={cx('absolute inset-y-0.5 min-w-0.5 rounded-xs', v.step === 'answer' ? 'bg-fg' : 'bg-fg-3')}
                style={{ left: `${String((left / total) * 100)}%`, width: `${String((width / total) * 100)}%` }}
              />
            </span>
            <span className="text-right font-machine text-mono-sm text-fg-3 tnum">{formatMs(width)}</span>
          </div>
        )
      })}
      <div className="wf-grid" aria-hidden>
        <span />
        <span className="relative h-4">
          {ticks.map((x, i) => (
            <span
              key={x}
              className={cx('absolute top-0 whitespace-nowrap font-machine text-mono-sm text-fg-3', i === 0 ? '' : x / total > 0.9 ? '-translate-x-full' : '-translate-x-1/2')}
              style={{ left: `${String((x / total) * 100)}%` }}
            >
              {x === 0 ? '0' : formatSeconds(x)}
            </span>
          ))}
        </span>
        <span />
      </div>
    </div>
  )
}

function ModelTable({ calls }: { calls: ModelCallEvent[] }) {
  if (calls.length === 0) return <p className="m-0 text-label font-normal text-fg-3">No model calls were completed this turn.</p>
  return (
    <div className="scroll-thin overflow-x-auto" tabIndex={0} role="region" aria-label="Model calls">
      <table className="min-w-full border-collapse font-machine text-mono-sm text-fg-2">
        <thead>
          <tr>
            {['step', 'model', 'tokens', 'time', 'cost'].map((h) => (
              <th key={h} scope="col" className="whitespace-nowrap border-b border-line pb-1.5 pr-3.5 text-left font-medium text-fg-3">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {calls.map((c, i) => (
            <tr key={`${c.step}-${String(i)}`} data-testid="model-call" className="border-b border-line last:border-b-0">
              <td className="py-1.5 pr-3.5">{c.step}</td>
              <td className="whitespace-nowrap py-1.5 pr-3.5" data-testid="model-call-model">
                {c.provider} · {c.model}
              </td>
              <td className="whitespace-nowrap py-1.5 pr-3.5 tnum">
                {formatTokens(c.usage.input_tokens + c.usage.cached_input_tokens)} → {formatTokens(c.usage.output_tokens)}
              </td>
              <td className="whitespace-nowrap py-1.5 pr-3.5 tnum" data-testid="call-latency">
                {formatMs(c.latency_ms)}
              </td>
              <td className="whitespace-nowrap py-1.5 pr-3.5 tnum" data-testid="call-cost">
                {formatUsd(c.usage.cost_usd)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
