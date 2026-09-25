import { cx } from './cx'

/** A surface-2 block with a slow sheen. Only for content that is loading, never for steps. */
export function Skeleton({ className }: { className?: string }) {
  return <span aria-hidden className={cx('skeleton block rounded-sm', className)} />
}
