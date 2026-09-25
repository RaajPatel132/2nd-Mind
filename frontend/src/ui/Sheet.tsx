import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useRef, type ReactNode } from 'react'
import { cx } from './cx'
import { motionProps } from './motion'

export type SheetMode = 'docked' | 'overlay' | 'bottom'

type Props = {
  open: boolean
  mode: SheetMode
  onClose: () => void
  label: string
  children: ReactNode
  testId?: string
}

const FOCUSABLE = 'button:not([disabled]), [href], input, textarea, select, [tabindex]:not([tabindex="-1"])'

/**
 * The inspector's container: docked beside the chat at ≥ 1440px, an overlay from the right
 * below that, a bottom sheet at 90% height under 768px. As an overlay it traps focus; Esc
 * closes it and focus returns to whatever opened it.
 */
export function Sheet({ open, mode, onClose, label, children, testId }: Props) {
  const ref = useRef<HTMLElement>(null)
  const opener = useRef<HTMLElement | null>(null)
  const overlay = mode !== 'docked'

  useEffect(() => {
    if (!open) return
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    ref.current?.focus({ preventScroll: true })
    const returnTo = opener
    return () => {
      const target = returnTo.current
      if (target?.isConnected) target.focus({ preventScroll: true })
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
        return
      }
      if (e.key !== 'Tab' || !overlay || !ref.current) return
      const items = [...ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null)
      const first = items[0]
      const last = items.at(-1)
      if (!first || !last) return
      if (e.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('keydown', onKey) }
  }, [open, overlay, onClose])

  return (
    <AnimatePresence>
      {open && overlay && (
        <motion.div
          key="scrim"
          aria-hidden
          {...motionProps('fade')}
          onClick={onClose}
          className="fixed inset-0 z-30 bg-(--scrim)"
        />
      )}
      {open && (
        <motion.aside
          key="sheet"
          ref={ref}
          tabIndex={-1}
          aria-label={label}
          data-testid={testId}
          data-mode={mode}
          {...motionProps(mode === 'bottom' ? 'sheetUp' : 'sheetRight')}
          className={cx(
            'flex flex-col bg-surface outline-none',
            mode === 'docked' && 'sticky top-0 h-dvh w-115 shrink-0 border-l border-line',
            mode === 'overlay' && 'fixed inset-y-0 right-0 z-40 w-115 max-w-full border-l border-line-strong shadow-overlay',
            mode === 'bottom' && 'fixed inset-x-0 bottom-0 z-40 h-9/10 rounded-t-lg border-t border-line-strong shadow-overlay',
          )}
        >
          {mode === 'bottom' && <span aria-hidden className="mx-auto mt-2 block h-1 w-9 shrink-0 rounded-full bg-fg-4" />}
          {children}
        </motion.aside>
      )}
    </AnimatePresence>
  )
}
