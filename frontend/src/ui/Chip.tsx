import type { ReactNode } from 'react'
import { cx } from './cx'

export type Tone = 'neutral' | 'ok' | 'warn' | 'bad'

const DOT: Record<Tone, string> = { neutral: 'bg-fg-3', ok: 'bg-ok', warn: 'bg-warn', bad: 'bg-bad' }

/** A status dot: the only filled shape besides the brand mark. */
export function Dot({ tone = 'neutral', className }: { tone?: Tone; className?: string }) {
  return <span aria-hidden className={cx('inline-block size-1.5 shrink-0 rounded-full forced-colors:outline forced-colors:outline-1', DOT[tone], className)} />
}

type ChipProps = {
  children: ReactNode
  /** `label` for words, `mono` for machine facts (trail chips, receipts). */
  kind?: 'label' | 'mono'
  dot?: Tone
  className?: string
  title?: string
}

/** Pill on surface-2, with an optional leading status dot. */
export function Chip({ children, kind = 'label', dot, className, title }: ChipProps) {
  return (
    <span
      title={title}
      className={cx(
        'inline-flex max-w-full items-center gap-2 truncate whitespace-nowrap rounded-full bg-surface-2',
        kind === 'mono' ? 'px-2 py-0.5 font-machine text-mono-sm text-fg-3 tnum' : 'h-7 px-3 text-label text-fg-2',
        className,
      )}
    >
      {dot && <Dot tone={dot} />}
      {children}
    </span>
  )
}
