import type { ElementType, ReactNode } from 'react'
import { cx } from './cx'

/** Uppercase section label (`MEMORY DIFF`). Never the only carrier of meaning. */
export function Overline({ children, as: As = 'p', className, id }: { children: ReactNode; as?: ElementType; className?: string; id?: string }) {
  return (
    <As id={id} className={cx('m-0 font-ui text-overline uppercase text-fg-3', className)}>
      {children}
    </As>
  )
}
