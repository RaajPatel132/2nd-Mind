import { AnimatePresence, motion } from 'motion/react'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Button } from './Button'
import { motionProps } from './motion'
import { ToastContext, type Show, type ToastAction } from './toast-context'

type Toast = { id: number; message: string; action?: ToastAction }

const TOAST_MS = 4000

/** Toasts: bottom centre above the composer, one at a time, 4s, with an action where one exists. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<Toast | null>(null)
  const next = useRef(0)
  const show = useCallback<Show>((message, action) => {
    next.current += 1
    setToast({ id: next.current, message, action })
  }, [])

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => { setToast((t) => (t?.id === toast.id ? null : t)) }, TOAST_MS)
    return () => { window.clearTimeout(timer) }
  }, [toast])

  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-32 z-50 flex justify-center px-4" role="status" aria-live="polite">
        <AnimatePresence>
          {toast && (
            <motion.div
              key={toast.id}
              {...motionProps('toast')}
              className="pointer-events-auto flex max-w-full items-center gap-3 truncate rounded-full border border-line-strong bg-surface-3 py-2 pl-4 pr-2 text-label text-fg shadow-overlay"
              data-testid="toast"
            >
              <span className="truncate">{toast.message}</span>
              {toast.action && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    toast.action?.run()
                    setToast(null)
                  }}
                >
                  {toast.action.label}
                </Button>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  )
}
