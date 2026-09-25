/**
 * The Trail (docs/design/system.md §8): the agent's steps inside a turn, as they happen and after
 * a reload. A list of buttons with aria-expanded; ↑ ↓ move between rows, Enter expands.
 */
import { AnimatePresence, motion } from 'motion/react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import type { Turn } from '../api/client'
import { formatMs, formatSeconds } from '../lib/format'
import { Chip, Disclosure, DisclosureTrigger, Icon, Overline, cx } from '../ui'
import { motionProps } from '../ui/motion'
import * as D from './details'
import type { Facts, StepView } from './model'
import { STEPS } from './steps'
import type { StepContext } from './types'
import { stepContext, stepLabel } from './view'

type TrailProps = {
  steps: StepView[]
  facts: Facts
  turn: Turn | null
  timezone: string
  /** Rows of a live turn enter one by one and draw the rail; stored rows are just there. */
  live: boolean
  folded: boolean
  onFold: (folded: boolean) => void
  summary: string
}

export function Trail({ steps, facts, turn, timezone, live, folded, onFold, summary }: TrailProps) {
  const listId = useId()
  const listRef = useRef<HTMLOListElement>(null)
  if (steps.length === 0) return null

  function onRowKey(e: KeyboardEvent<HTMLButtonElement>) {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
    const rows = [...(listRef.current?.querySelectorAll<HTMLButtonElement>('button[data-step-row]') ?? [])]
    const next = rows[rows.indexOf(e.currentTarget) + (e.key === 'ArrowDown' ? 1 : -1)]
    if (next) {
      e.preventDefault()
      next.focus()
    }
  }

  return (
    <div className="mt-3.5" data-testid="trail" data-folded={folded}>
      <Disclosure id={`${listId}-sum`} open={folded}>
        <DisclosureTrigger
          open={false}
          controls={listId}
          onClick={() => {
            onFold(false)
            requestAnimationFrame(() => listRef.current?.querySelector<HTMLButtonElement>('button[data-step-row]')?.focus())
          }}
          className="inline-flex min-h-9 cursor-pointer items-center gap-2.5 rounded-full py-1.5 pl-1 pr-3 font-machine text-mono-sm text-fg-3 transition-colors dur-2 hover:bg-surface hover:text-fg-2"
          data-testid="trail-summary"
        >
          <span aria-hidden className="inline-flex gap-0.5 pl-1">
            {steps.map((s) => (
              <span
                key={s.key}
                className={cx(
                  'size-1.5 rounded-full',
                  s.state === 'held' ? 'bg-warn' : s.state === 'refused' || s.state === 'failed' ? 'bg-bad' : 'bg-fg-3',
                )}
              />
            ))}
          </span>
          <span>{summary}</span>
          <Icon icon={ChevronDown} />
        </DisclosureTrigger>
      </Disclosure>
      <Disclosure id={listId} open={!folded}>
        <ol ref={listRef} aria-label="What the agent did" className="relative m-0 list-none p-0 py-0.5">
          <AnimatePresence initial={false}>
            {steps.map((view) => (
              <StepRow key={view.key} ctx={stepContext(view, facts, turn, timezone)} live={live} onKey={onRowKey} />
            ))}
          </AnimatePresence>
        </ol>
      </Disclosure>
    </div>
  )
}

function StepRow({ ctx, live, onKey }: { ctx: StepContext; live: boolean; onKey: (e: KeyboardEvent<HTMLButtonElement>) => void }) {
  const { view } = ctx
  const spec = STEPS[view.step]
  const detailId = useId()
  const urgent = view.state === 'held' || view.state === 'refused'
  const [open, setOpen] = useState(urgent)
  const label = stepLabel(ctx)
  const chips = view.state === 'running' || view.state === 'failed' ? [] : spec.chips(ctx).slice(0, 3)

  // Held and refused open by themselves, including when a live step turns into one.
  const [wasUrgent, setWasUrgent] = useState(urgent)
  if (wasUrgent !== urgent) {
    setWasUrgent(urgent)
    if (urgent) setOpen(true)
  }

  const Plain = view.state === 'failed' ? D.FailedPlain : spec.Plain
  const Tech = view.state === 'failed' ? D.FailedTech : spec.Tech
  return (
    <motion.li
      className="trail-step"
      data-state={view.state}
      data-draw={live}
      data-step={view.step}
      data-testid="trail-step"
      {...(live ? motionProps('rise') : { initial: false })}
    >
      <DisclosureTrigger
        data-step-row
        open={open}
        controls={detailId}
        onClick={() => {
          setOpen((o) => !o)
        }}
        onKeyDown={onKey}
        className="step-grid group -ml-1 box-border min-h-9 w-full cursor-pointer rounded-sm py-1.5 pl-1 pr-2.5 text-left text-fg-2 transition-colors dur-2 ease-out hover:bg-surface focus-visible:outline-offset-0"
      >
        <span aria-hidden className="trail-node" />
        <Icon
          icon={spec.icon}
          className={cx(
            view.state === 'held' ? 'text-warn' : view.state === 'refused' || view.state === 'failed' ? 'text-bad' : view.state === 'running' ? 'text-fg-2' : 'text-fg-3',
          )}
        />
        <span className="flex min-w-0 overflow-hidden">
          <AnimatePresence mode="wait" initial={false}>
            <motion.span
              key={label}
              {...motionProps('swap')}
              className={cx(
                'block truncate text-label',
                view.state === 'running' ? 'shimmer-text' : urgent || view.state === 'failed' ? 'text-fg' : 'text-fg-2 group-hover:text-fg',
              )}
              data-testid="step-label"
            >
              {label}
            </motion.span>
          </AnimatePresence>
        </span>
        <span className="hidden min-w-0 gap-1.5 overflow-hidden sm:flex">
          {chips.map((c, i) => (
            <motion.span key={c} {...motionProps('fade', i + 1)}>
              <Chip kind="mono">{c}</Chip>
            </motion.span>
          ))}
        </span>
        <Timer view={view} />
        <Icon icon={ChevronRight} className={cx('text-fg-3 transition-transform dur-3 ease-out', open && 'rotate-90')} />
      </DisclosureTrigger>
      <Disclosure id={detailId} open={open}>
        <div className="mb-3 ml-7 mt-1 rounded-md border border-line bg-surface px-4 pb-4 pt-3.5 shadow-edge sm:ml-14" data-testid="step-detail">
          <Overline>What happened</Overline>
          <div className="mb-3.5 mt-1">
            <Plain ctx={ctx} />
          </div>
          <Overline className="mb-2">Under the hood</Overline>
          <Tech ctx={ctx} />
        </div>
      </Disclosure>
    </motion.li>
  )
}

/** Counts up while the step runs; freezes at the server's latency when it ends. */
function Timer({ view }: { view: StepView }) {
  const ref = useRef<HTMLSpanElement>(null)
  const running = view.state === 'running'
  useEffect(() => {
    if (!running) return
    const began = performance.now()
    let frame = 0
    const tick = (now: number) => {
      if (ref.current) ref.current.textContent = formatMs(now - began)
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(frame)
    }
  }, [running])
  return (
    <span ref={ref} className="min-w-14 text-right font-machine text-mono-sm text-fg-3 tnum">
      {running ? '0 ms' : view.latencyMs != null ? formatSeconds(view.latencyMs) : ''}
    </span>
  )
}
