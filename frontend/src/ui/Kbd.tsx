import type { ReactNode } from 'react'

/** A key: radius-xs, mono-sm, 1px line-strong. */
export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded-xs px-1.5 py-px font-machine text-mono-sm text-fg-2 shadow-edge ring-1 ring-inset ring-line-strong">
      {children}
    </kbd>
  )
}
