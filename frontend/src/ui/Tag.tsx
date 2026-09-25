import type { ReactNode } from 'react'
import { cx } from './cx'

/** radius-xs, overline type: layers (CORE, QUICK, ARCHIVE) and tiers. */
export function Tag({ children, strong = false, className }: { children: ReactNode; strong?: boolean; className?: string }) {
  return (
    <span
      className={cx(
        'inline-block whitespace-nowrap rounded-xs px-1.5 font-ui text-overline uppercase ring-1 ring-inset',
        strong ? 'text-fg-2 ring-fg-3' : 'text-fg-3 ring-line-strong',
        className,
      )}
    >
      {children}
    </span>
  )
}
