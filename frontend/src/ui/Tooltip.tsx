import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useId, useRef, useState, type ReactElement, type ReactNode } from 'react'
import { motionProps } from './motion'
import { cx } from './cx'

const DELAY_MS = 400

type Props = {
  label: ReactNode
  /** The trigger; it gets aria-describedby while the tooltip shows. */
  children: (props: { 'aria-describedby': string | undefined }) => ReactElement
  side?: 'top' | 'bottom'
  className?: string
}

/** surface-3, radius-md, 400ms delay, 4px rise. Shows on hover and on keyboard focus. */
export function Tooltip({ label, children, side = 'top', className }: Props) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => { window.clearTimeout(timer.current) }, [])

  const show = (delay: number) => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => { setOpen(true) }, delay)
  }
  const hide = () => {
    window.clearTimeout(timer.current)
    setOpen(false)
  }

  return (
    <span
      className={cx('relative inline-flex', className)}
      onPointerEnter={() => { show(DELAY_MS) }}
      onPointerLeave={hide}
      onFocus={(e) => { if (e.target.matches(':focus-visible')) show(0) }}
      onBlur={hide}
      onKeyDown={(e) => { if (e.key === 'Escape') hide() }}
    >
      {children({ 'aria-describedby': open ? id : undefined })}
      <AnimatePresence>
        {open && (
          <motion.span
            id={id}
            role="tooltip"
            {...motionProps('pop')}
            className={cx(
              'pointer-events-none absolute left-1/2 z-50 -translate-x-1/2 whitespace-nowrap rounded-sm border border-line-strong bg-surface-3 px-2.5 py-1 text-label text-fg shadow-overlay',
              side === 'top' ? 'bottom-full mb-2' : 'top-full mt-2',
            )}
          >
            {label}
          </motion.span>
        )}
      </AnimatePresence>
    </span>
  )
}
