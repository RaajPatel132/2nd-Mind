import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { cx } from './cx'

/**
 * The expand/collapse region behind step rows and panels: height animates through grid rows
 * (0fr → 1fr, dur-3). The trigger is the caller's own button with aria-expanded and
 * aria-controls={id}; a closed region is inert, so nothing inside it takes focus.
 */
export function Disclosure({ id, open, children, className }: { id: string; open: boolean; children: ReactNode; className?: string }) {
  return (
    <div id={id} className={cx('grid-reveal', className)} data-open={open} inert={!open}>
      <div>{children}</div>
    </div>
  )
}

type TriggerProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'aria-expanded' | 'aria-controls'> & {
  open: boolean
  controls: string
}

/** The row that opens a Disclosure: a full-width button carrying aria-expanded and aria-controls. */
export const DisclosureTrigger = forwardRef<HTMLButtonElement, TriggerProps>(function DisclosureTrigger(
  { open, controls, className, type = 'button', ...rest },
  ref,
) {
  return <button ref={ref} type={type} aria-expanded={open} aria-controls={controls} className={cx('cursor-pointer', className)} {...rest} />
})
