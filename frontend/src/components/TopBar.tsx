import { motion, useReducedMotion } from 'motion/react'
import { useEffect, useId, useRef, useState } from 'react'
import { logout, type Me, type Picker, type Usage } from '../api/client'
import type { LastSpend } from '../hooks/useUsage'
import { formatTokens, formatUsd, initialsOf } from '../lib/format'
import { BrandMark, Button, CountUp, Overline, Popover, QuotaRing, Tag, Wordmark, cx } from '../ui'
import { t } from '../ui/motion'
import { ModelPicker } from './ModelPicker'

type Props = {
  me: Me
  providerMode: string
  /** The model picker; null when the server offers none (the provider-mode tag shows instead). */
  picker: Picker | null
  model: string | null
  onModel: (id: string) => void
  usage: Usage | null
  last: LastSpend | null
  delta: { key: number; text: string } | null
  /** Leaves room for the docked inspector on wide screens. */
  docked: boolean
}

/**
 * Brand, wordmark and workspace on the left; the model picker and the avatar with its quota
 * ring on the right. Transparent at rest; surface, blur and a hairline once content scrolls under.
 */
export function TopBar({ me, providerMode, picker, model, onModel, usage, last, delta, docked }: Props) {
  const [scrolled, setScrolled] = useState(false)
  useEffect(() => {
    const onScroll = () => {
      setScrolled(window.scrollY > 4)
    }
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      window.removeEventListener('scroll', onScroll)
    }
  }, [])

  return (
    <header
      className={cx(
        'fixed inset-x-0 top-0 z-20 border-b transition-colors dur-2 ease-out',
        scrolled ? 'border-line bg-(--bar-fill) backdrop-blur-bar' : 'border-transparent bg-transparent',
        docked && '2xl:right-115',
      )}
      data-scrolled={scrolled}
      data-testid="top-bar"
    >
      <div className="flex h-14 items-center justify-between gap-3 pl-4 pr-3 md:pl-6 lg:pl-8">
        <div className="flex min-w-0 items-center gap-3">
          <a href="/" className="inline-flex items-center gap-2.5 rounded-sm text-fg no-underline" aria-label="2nd Mind, home">
            <BrandMark />
            <Wordmark />
          </a>
          {/* A single workspace: a plain pill, no menu (the switcher arrives with a second one). */}
          <span className="hidden whitespace-nowrap rounded-full px-3 py-1 text-label text-fg-2 ring-1 ring-inset ring-line-strong sm:inline">
            My memory
          </span>
        </div>
        <div className="flex min-w-0 items-center gap-3">
          {picker && model ? (
            <ModelPicker picker={picker} value={model} onChange={onModel} />
          ) : providerMode !== 'live' && (
            <span className="rounded-full px-2 py-0.5 font-machine text-mono-sm text-fg-3 ring-1 ring-inset ring-line-strong" data-testid="provider-mode">
              {providerMode}
            </span>
          )}
          <Account me={me} usage={usage} last={last} delta={delta} baseline={picker?.baseline_label ?? null} />
        </div>
      </div>
    </header>
  )
}

function Account({ me, usage, last, delta, baseline }: Pick<Props, 'me' | 'usage' | 'last' | 'delta'> & { baseline: string | null }) {
  const [open, setOpen] = useState(false)
  const ring = useRef<HTMLButtonElement>(null)
  const popId = useId()
  const reduce = useReducedMotion()
  const remaining = usage && usage.limit_tokens > 0 ? usage.remaining_tokens / usage.limit_tokens : 1
  const initials = initialsOf(me.user.email ?? '')

  return (
    <div className="relative">
      <QuotaRing
        ref={ring}
        remaining={remaining}
        initials={initials}
        delta={delta}
        expanded={open}
        controls={popId}
        onClick={() => {
          setOpen((o) => !o)
        }}
      />
      <Popover
        id={popId}
        open={open}
        onClose={() => {
          setOpen(false)
        }}
        anchor={ring}
        label="Quota and account"
        className="right-0 top-12 w-74"
      >
        <div data-testid="quota-popover">
          <Overline>Quota</Overline>
          <p className="m-0 mb-0.5 mt-1.5 flex items-baseline gap-1.5">
            <CountUp
              value={remaining * 100}
              format={(n) => n.toFixed(1)}
              className="font-voice text-display text-fg"
            />
            <span className="font-voice text-title-lg text-fg-2">%</span>
            <span className="ml-1 text-label text-fg-2">left</span>
          </p>
          <p className="m-0 font-machine text-mono-sm text-fg-3 tnum" data-testid="quota-remaining">
            {usage ? `${formatTokens(usage.remaining_tokens)} of ${formatTokens(usage.limit_tokens)} tokens` : 'Loading…'}
          </p>
          <div className="mb-3.5 mt-3 h-1 overflow-hidden rounded-full bg-surface-2" aria-hidden>
            <motion.span
              className="block h-full origin-left bg-(image:--gradient-chrome)"
              initial={false}
              animate={{ scaleX: remaining }}
              transition={reduce ? t.instant : t.meter}
            />
          </div>
          <dl className="kv-grid m-0 items-center">
            <dt className="text-label text-fg-3">Tier</dt>
            <dd className="m-0 justify-self-end">
              <Tag>{usage?.tier ?? '—'}</Tag>
            </dd>
            <dt className="text-label text-fg-3">Last turn</dt>
            <dd className="m-0 justify-self-end font-machine text-mono-sm text-fg-2 tnum" data-testid="quota-last">
              {last ? `−${formatTokens(last.tokens)} tokens · ${formatUsd(last.costUsd)}` : '—'}
            </dd>
          </dl>
          <p className="m-0 mt-3 text-label font-normal text-fg-3">
            {baseline
              ? `A lifetime allowance, counted in ${baseline} tokens. Bigger models use it faster; every call counts, embeddings included.`
              : 'A lifetime allowance. Every model call counts, embeddings included.'}
          </p>
          <div className="-mx-4 my-3.5 h-px bg-line-strong" />
          <div className="flex items-center gap-3">
            <span aria-hidden className="grid size-8 shrink-0 place-items-center rounded-full bg-surface-2 font-ui text-mono-sm font-semibold">
              {initials}
            </span>
            <div className="min-w-0 flex-1">
              <p className="m-0 text-label text-fg">Dev sign-in</p>
              <p className="m-0 truncate font-machine text-mono-sm text-fg-3" data-testid="account-email">
                {me.user.email}
              </p>
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                void logout().finally(() => {
                  window.location.assign('/?signed-out')
                })
              }}
            >
              Sign out
            </Button>
          </div>
        </div>
      </Popover>
    </div>
  )
}
