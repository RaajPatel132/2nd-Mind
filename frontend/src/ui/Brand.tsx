import { cx } from './cx'

/** The brand mark: a ring with its left half in chrome. */
export function BrandMark({ size = 'md', className }: { size?: 'md' | 'lg' | 'xl'; className?: string }) {
  return <span aria-hidden className={cx('brand-mark', size === 'md' ? 'size-5' : size === 'lg' ? 'size-10' : 'size-14', className)} />
}

/** "2nd Mind": the ordinal in Voice italic, the name in Interface. */
export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cx('whitespace-nowrap font-ui text-body font-semibold tracking-tight text-fg', className)}>
      <em className="mr-px font-voice text-title font-normal">2nd</em> Mind
    </span>
  )
}
