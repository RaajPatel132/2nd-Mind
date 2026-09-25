import type { LucideIcon } from 'lucide-react'

/** A Lucide icon at 16px (inline, trail) or 20px (buttons, bar), 1.5px stroke, currentColor. */
export function Icon({ icon: Glyph, size = 16, className }: { icon: LucideIcon; size?: 16 | 20; className?: string }) {
  return <Glyph aria-hidden focusable={false} size={size} strokeWidth={1.5} className={className} />
}
