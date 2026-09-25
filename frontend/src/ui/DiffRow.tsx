import type { ReactNode } from 'react'
import { cx } from './cx'
import { Tag } from './Tag'

export type DiffGlyph = '+' | '~' | '−' | '⏸' | '∅' | '!'

const GLYPH: Record<DiffGlyph, { tone: string; said: string }> = {
  '+': { tone: 'text-ok', said: 'added' },
  '~': { tone: 'text-fg', said: 'changed' },
  '−': { tone: 'text-bad', said: 'removed' },
  '⏸': { tone: 'text-warn', said: 'held' },
  '∅': { tone: 'text-fg-3', said: 'not written' },
  '!': { tone: 'text-warn', said: 'conflict' },
}

type Props = {
  glyph: DiffGlyph
  layer: string
  title: ReactNode
  note?: ReactNode
  /** Superseded or removed content: fg-3 and struck through, not red. */
  strike?: boolean
  children?: ReactNode
  testId?: string
  op?: string
}

/** One memory change: op glyph in Machine type, the layer tag, the text and a mono note. */
export function DiffRow({ glyph, layer, title, note, strike, children, testId, op }: Props) {
  const g = GLYPH[glyph]
  return (
    <li className="grid diff-grid items-baseline gap-2.5" data-testid={testId} data-op={op}>
      <span aria-hidden className={cx('text-center font-machine text-label font-semibold', g.tone)}>
        {glyph}
      </span>
      <Tag className="justify-self-start">{layer}</Tag>
      <span className="min-w-0 break-words text-label font-normal text-fg">
        <span className="sr-only">{g.said}: </span>
        {strike ? <s className="text-fg-3 decoration-fg-3">{title}</s> : title}
        {note && <span className="ml-1.5 font-machine text-mono-sm text-fg-3">{note}</span>}
        {children}
      </span>
    </li>
  )
}
