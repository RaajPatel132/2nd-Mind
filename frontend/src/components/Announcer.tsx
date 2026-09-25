import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { AnnounceContext } from '../hooks/useAnnouncer'

const GAP_MS = 900

/** One polite live region for the app. Messages closer than 0.9 s apart collapse to the latest. */
export function Announcer({ children }: { children: ReactNode }) {
  const [text, setText] = useState('')
  const pending = useRef<string | null>(null)
  const last = useRef(0)
  const timer = useRef<number | undefined>(undefined)

  const announce = useCallback((message: string) => {
    pending.current = message
    if (timer.current !== undefined) return
    const wait = Math.max(0, last.current + GAP_MS - performance.now())
    timer.current = window.setTimeout(() => {
      timer.current = undefined
      last.current = performance.now()
      setText(pending.current ?? '')
      pending.current = null
    }, wait)
  }, [])

  useEffect(
    () => () => {
      window.clearTimeout(timer.current)
    },
    [],
  )

  return (
    <AnnounceContext.Provider value={announce}>
      {children}
      <div className="sr-only" aria-live="polite" data-testid="announcer">
        {text}
      </div>
    </AnnounceContext.Provider>
  )
}
