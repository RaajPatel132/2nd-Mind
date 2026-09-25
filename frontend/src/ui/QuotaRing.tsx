import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { forwardRef, useId } from 'react'
import { cx } from './cx'
import { t } from './motion'
import { quotaTone } from './quota'

const R = 18
const C = 2 * Math.PI * R

type Props = {
  /** Remaining quota, 0..1 (the arc shows what's left, like a fuel gauge). */
  remaining: number
  initials: string
  /** The latest spend, floated up from the ring ("−2.9k"); a new key replays it. */
  delta?: { key: string | number; text: string } | null
  expanded: boolean
  controls: string
  onClick: () => void
  size?: 'md' | 'lg'
}

/**
 * The 40px ring around the 32px avatar: the arc is the remaining quota in chrome, warn below
 * 25%, bad below 10%. It animates to each new value (dur-5), and the spent amount rises from it.
 */
export const QuotaRing = forwardRef<HTMLButtonElement, Props>(function QuotaRing(
  { remaining, initials, delta, expanded, controls, onClick, size = 'md' },
  ref,
) {
  const gradient = useId()
  const reduce = useReducedMotion()
  const p = Math.min(1, Math.max(0, remaining))
  const tone = quotaTone(p)
  return (
    <span className="relative inline-flex items-center">
      <AnimatePresence>
        {delta && (
          <motion.span
            key={delta.key}
            aria-hidden
            className="pointer-events-none absolute right-full mr-2 whitespace-nowrap font-machine text-mono-sm text-fg-2"
            initial={{ opacity: 0, y: 6 }}
            animate={reduce ? { opacity: [0, 1, 0] } : { opacity: [0, 1, 0], y: [6, 0, -14] }}
            transition={t.float}
            data-testid="quota-delta"
          >
            {delta.text}
          </motion.span>
        )}
      </AnimatePresence>
      <button
        ref={ref}
        type="button"
        onClick={onClick}
        aria-expanded={expanded}
        aria-controls={controls}
        aria-label={`Quota ${(p * 100).toFixed(1)}% left. Open account and quota`}
        data-testid="quota-ring"
        data-remaining={p.toFixed(4)}
        data-tone={tone}
        className={cx(
          'relative shrink-0 cursor-pointer rounded-full transition dur-2 ease-out hover:scale-105 motion-reduce:hover:scale-100',
          size === 'lg' ? 'size-20' : 'size-10',
        )}
      >
        <svg viewBox="0 0 40 40" aria-hidden className="absolute inset-0 size-full -rotate-90 overflow-visible">
          <defs>
            <linearGradient id={gradient} x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" className="chrome-stop-1" />
              <stop offset="0.38" className="chrome-stop-2" />
              <stop offset="0.52" className="chrome-stop-3" />
              <stop offset="1" className="chrome-stop-4" />
            </linearGradient>
          </defs>
          <circle cx="20" cy="20" r={R} fill="none" strokeWidth="2.5" className="stroke-line-strong" />
          <motion.circle
            cx="20"
            cy="20"
            r={R}
            fill="none"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeDasharray={C}
            initial={false}
            animate={{ strokeDashoffset: C * (1 - p) }}
            transition={reduce ? t.instant : t.meter}
            stroke={tone === 'chrome' ? `url(#${gradient})` : undefined}
            className={cx(tone === 'warn' && 'stroke-warn', tone === 'bad' && 'stroke-bad')}
          />
        </svg>
        <span
          className={cx(
            'absolute inset-1 grid place-items-center rounded-full bg-surface-2 font-ui font-semibold text-fg',
            size === 'lg' ? 'text-title' : 'text-mono-sm',
          )}
        >
          {initials}
        </span>
      </button>
    </span>
  )
})
