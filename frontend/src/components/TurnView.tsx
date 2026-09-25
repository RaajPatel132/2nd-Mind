import { Copy, PanelRightOpen, Undo2 } from 'lucide-react'
import { motion } from 'motion/react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { outputOf, type ChatTurn } from '../hooks/useConversation'
import { useAnnounce } from '../hooks/useAnnouncer'
import { formatClock, formatSeconds, formatTokens, formatUsd, plural, shortId } from '../lib/format'
import { diffCounts, factsOf, stepViews, touched, type Facts, type StepView } from '../trail/model'
import { usePacedSteps } from '../trail/pacing'
import { Trail } from '../trail/Trail'
import { stepContext, stepLabel } from '../trail/view'
import { Button, IconButton, Tag, Tooltip, cx, useToast } from '../ui'
import { motionProps } from '../ui/motion'

type Props = {
  turn: ChatTurn
  timezone: string
  folded: boolean
  onFold: (folded: boolean) => void
  onInspect: (turnId: string) => void
  onUndo: (turnId: string) => Promise<void>
  busy: boolean
}

const NOTE_LABEL: Record<ChatTurn['kind'], string> = { user: 'You', undo: 'Undo', confirm: 'Confirmed', system: 'Housekeeping' }

/** One turn: who and when, the message, the Trail, the answer and the receipt (rulebook §7). */
export function TurnView({ turn, timezone, folded, onFold, onInspect, onUndo, busy }: Props) {
  const events = useMemo(() => turn.events ?? [], [turn.events])
  const target = useMemo(() => stepViews(events, turn.starts), [events, turn.starts])
  const facts = useMemo(() => factsOf(events), [events])
  const shown = usePacedSteps(target, turn.live)
  useAnnounceSteps(turn, shown, facts, timezone)

  const event = turn.kind !== 'user'
  const caughtUp = shown.length === target.length && shown.every((s, i) => s.state === target[i]?.state)
  const answerVisible = !turn.live || target.length === 0 || shown.some((s) => s.step === 'answer') || (caughtUp && turn.status !== 'streaming')
  const finished = turn.status === 'completed' || turn.status === 'failed'

  return (
    <motion.article
      {...(turn.live ? motionProps('rise') : { initial: false })}
      className={cx('min-w-0', event && 'rounded-md')}
      data-testid={event ? 'system-note' : 'turn'}
      data-kind={turn.kind}
      data-turn-id={turn.id ?? undefined}
      data-status={turn.status}
      aria-label={event ? `${NOTE_LABEL[turn.kind]} at ${formatClock(turn.sentAt)}` : `Your message at ${formatClock(turn.sentAt)}`}
    >
      <p className="m-0 mb-2 flex flex-wrap items-center gap-2 font-ui text-overline uppercase text-fg-3">
        {event ? (
          <>
            <Tag>{NOTE_LABEL[turn.kind]}</Tag>
            <span className="tnum">{formatClock(turn.sentAt)}</span>
            {turn.turn?.parent_turn_id && <span className="font-machine normal-case tracking-normal">· turn {shortId(turn.turn.parent_turn_id)}</span>}
          </>
        ) : (
          <span className="tnum">You · {formatClock(turn.sentAt)}</span>
        )}
      </p>
      {!event && <p className="m-0 whitespace-pre-wrap break-words text-pretty text-prompt text-fg">{turn.input}</p>}
      <Trail
        steps={shown}
        facts={facts}
        turn={turn.turn}
        timezone={timezone}
        live={turn.live}
        folded={folded}
        onFold={onFold}
        summary={summaryOf(turn, target, facts)}
      />
      {turn.status === 'failed' ? (
        <Failure turn={turn} />
      ) : (
        answerVisible && <Answer turn={turn} event={event} />
      )}
      {turn.status === 'running' && (
        <p className="m-0 mt-3 text-label font-normal text-fg-3">Still being answered. Refresh in a moment to see the reply.</p>
      )}
      {finished && turn.id && turn.turn && (answerVisible || turn.status === 'failed') && (
        <Receipt turn={turn} facts={facts} onInspect={onInspect} onUndo={onUndo} busy={busy} />
      )}
    </motion.article>
  )
}

function Answer({ turn, event }: { turn: ChatTurn; event: boolean }) {
  const streaming = turn.status === 'streaming'
  if (turn.chunks.length === 0 && !streaming) return null
  return (
    <div
      className={cx('measure mt-4 whitespace-pre-wrap break-words text-pretty', event ? 'text-label font-normal text-fg-2' : 'text-answer text-fg')}
      data-testid="assistant-message"
      aria-busy={streaming}
    >
      {turn.chunks.map((chunk, i) => (
        <span key={i} className={turn.live ? 'chunk-in' : undefined}>
          {chunk}
        </span>
      ))}
      {streaming && <span aria-hidden className="caret" />}
      {streaming && turn.chunks.length === 0 && <span className="sr-only">Thinking…</span>}
    </div>
  )
}

function Failure({ turn }: { turn: ChatTurn }) {
  const retry = turn.turn?.error?.code === 'provider_unavailable' ? 'Send it again in a moment.' : 'Send it again to retry.'
  return (
    <div className="mt-4 border-l-2 border-bad pl-4" data-testid="assistant-message" role="alert">
      <p className="m-0 text-body text-fg">{turn.error ?? 'This turn failed.'}</p>
      <p className="m-0 mt-1 text-label font-normal text-fg-2">Nothing from this message was saved. {retry}</p>
    </div>
  )
}

function Receipt({
  turn,
  facts,
  onInspect,
  onUndo,
  busy,
}: {
  turn: ChatTurn
  facts: Facts
  onInspect: (turnId: string) => void
  onUndo: (turnId: string) => Promise<void>
  busy: boolean
}) {
  const [asking, setAsking] = useState(false)
  const toast = useToast()
  const t = turn.turn
  if (!t || !turn.id) return null
  const turnId = turn.id
  const usage = t.usage
  const tokens = usage.input_tokens + usage.cached_input_tokens + usage.output_tokens
  const wall = t.finished_at ? Date.parse(t.finished_at) - Date.parse(t.started_at) : null
  const count = touched(facts.diff)
  const undoable = t.status === 'completed' && count > 0
  const answer = outputOf(turn)
  const model = t.models.answer
  const receiptFacts = [wall != null ? formatSeconds(wall) : null, tokens ? `${formatTokens(tokens)} tokens` : 'no model call', formatUsd(usage.cost_usd)].filter(
    (f): f is string => f !== null,
  )

  return (
    <motion.div {...motionProps('rise')} className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5" data-testid="receipt">
      <Tooltip label={model ? `Answered by ${model.provider} · ${model.model}` : 'No model wrote this reply'}>
        {(describedBy) => (
          <span tabIndex={0} {...describedBy} className="flex flex-wrap gap-1.5 rounded-xs font-machine text-mono-sm text-fg-3 tnum" data-testid="receipt-facts">
            {receiptFacts.map((f, i) => (
              <span key={f}>
                {i > 0 && (
                  <span aria-hidden className="mr-1.5 text-fg-4">
                    ·
                  </span>
                )}
                {f}
              </span>
            ))}
          </span>
        )}
      </Tooltip>
      {asking ? (
        <span role="group" aria-label="Confirm undo" className="flex flex-wrap items-center gap-2">
          <span className="text-label font-normal text-fg-2">Undo all {plural(count, 'change')} from this turn?</span>
          <Button
            variant="primary"
            size="sm"
            disabled={busy}
            data-testid="undo-confirm"
            onClick={() => {
              setAsking(false)
              void onUndo(turnId)
            }}
          >
            Undo all
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setAsking(false)
            }}
          >
            Cancel
          </Button>
        </span>
      ) : (
        <span className="-ml-2 flex items-center gap-0.5">
          <Button
            variant="ghost"
            size="sm"
            icon={<PanelRightOpen aria-hidden size={16} strokeWidth={1.5} />}
            onClick={() => {
              onInspect(turnId)
            }}
            data-testid="inspect"
          >
            Inspect
          </Button>
          {undoable && (
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
              icon={<Undo2 aria-hidden size={16} strokeWidth={1.5} />}
              onClick={() => {
                if (count > 1) setAsking(true)
                else void onUndo(turnId)
              }}
              data-testid="undo-turn"
            >
              Undo
            </Button>
          )}
          {answer && (
            <IconButton
              icon={Copy}
              label="Copy reply"
              size="sm"
              onClick={() => {
                navigator.clipboard.writeText(answer).then(
                  () => {
                    toast('Copied the reply')
                  },
                  () => {
                    toast("Copying isn't available here")
                  },
                )
              }}
            />
          )}
        </span>
      )}
    </motion.div>
  )
}

/** "7 steps · 2.1 s · saved 1, updated 2": the folded Trail's one line. */
function summaryOf(turn: ChatTurn, steps: StepView[], facts: Facts): string {
  const t = turn.turn
  const wall = t?.finished_at ? Date.parse(t.finished_at) - Date.parse(t.started_at) : null
  const c = diffCounts(facts.diff)
  const results: string[] = []
  if (c.added) results.push(`saved ${String(c.added)}`)
  if (c.updated) results.push(`updated ${String(c.updated)}`)
  if (c.removed) results.push(`removed ${String(c.removed)}`)
  if (c.held) results.push(`held ${String(c.held)}`)
  if (steps.some((s) => s.state === 'refused')) results.push('refused')
  if (steps.some((s) => s.state === 'failed')) results.push('failed')
  if (results.length === 0) results.push(facts.intent?.intent === 'chit_chat' ? 'chit-chat' : 'nothing saved')
  return [plural(steps.length, 'step'), wall != null ? formatSeconds(wall) : null, results.join(', ')].filter(Boolean).join(' · ')
}

/** A live turn announces each step's result (politely, throttled) and the finished reply. */
function useAnnounceSteps(turn: ChatTurn, shown: StepView[], facts: Facts, timezone: string) {
  const announce = useAnnounce()
  const said = useRef(new Set<string>())
  const reply = useRef(false)
  useEffect(() => {
    if (!turn.live) return
    for (const view of shown) {
      if (view.state === 'running' || said.current.has(view.key)) continue
      said.current.add(view.key)
      if (view.step !== 'answer') announce(stepLabel(stepContext(view, facts, turn.turn, timezone)))
    }
    if (!reply.current && turn.status === 'completed' && shown.length > 0 && shown.every((s) => s.state !== 'running')) {
      reply.current = true
      const text = outputOf(turn)
      if (text) announce(`Reply finished. ${text}`)
    }
  }, [shown, turn, facts, timezone, announce])
}
