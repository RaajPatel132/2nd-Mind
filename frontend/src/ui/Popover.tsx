import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useRef, type ReactNode, type RefObject } from 'react'
import { cx } from './cx'
import { motionProps } from './motion'

type Props = {
  open: boolean
  onClose: () => void
  /** The button that opened it: clicks on it don't count as outside, and focus goes back to it. */
  anchor: RefObject<HTMLElement | null>
  label: string
  children: ReactNode
  className?: string
  id?: string
}

/** surface-3, radius-md, overlay shadow; 4px rise. Esc or a click outside closes it. */
export function Popover({ open, onClose, anchor, label, children, className, id }: Props) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const first = ref.current?.querySelector<HTMLElement>('button, [href], [tabindex="0"]')
    ;(first ?? ref.current)?.focus({ preventScroll: true })
    function onPointer(e: PointerEvent) {
      const target = e.target as Node
      if (ref.current?.contains(target) || anchor.current?.contains(target)) return
      onClose()
    }
    function onKey(e: KeyboardEvent) {
      if (e.key !== 'Escape') return
      e.stopPropagation()
      onClose()
      anchor.current?.focus()
    }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey, true)
    }
  }, [open, onClose, anchor])

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          ref={ref}
          id={id}
          role="dialog"
          aria-label={label}
          tabIndex={-1}
          {...motionProps('pop')}
          className={cx(
            'absolute z-40 origin-top-right rounded-md border border-line-strong bg-surface-3 p-4 shadow-overlay outline-none',
            className,
          )}
        >
          {children}
        </motion.div>
      )}
    </AnimatePresence>
  )
}
