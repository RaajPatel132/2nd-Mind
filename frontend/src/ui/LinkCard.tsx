import { ExternalLink, Globe, PlayCircle } from 'lucide-react'
import type { ReactNode } from 'react'
import { ButtonLink } from './Button'
import { Chip, type Tone } from './Chip'
import { cx } from './cx'
import { Icon } from './Icon'
import { isWebAddress } from './web-address'

export type LinkStatus = 'pending' | 'full' | 'partial' | 'failed' | 'refused'

const STATUS: Record<LinkStatus, { label: string; tone: Tone }> = {
  pending: { label: 'Reading the page', tone: 'neutral' },
  full: { label: 'Read', tone: 'ok' },
  partial: { label: 'Partly read', tone: 'warn' },
  failed: { label: "Couldn't open it", tone: 'bad' },
  refused: { label: "Didn't open it", tone: 'bad' },
}

type Props = {
  title: string
  /** The original address. Only http(s) is ever linked. */
  href: string
  /** The page's host, as read (never the path). */
  host?: string | null
  site?: string | null
  kind?: 'link' | 'video'
  status: LinkStatus
  /** Why it is partial, failed or refused, in a few words. */
  note?: string | null
  /** Short facts in Machine type: author and date, or channel and length. */
  facts?: string[]
  /** What the page says, as plain text. */
  summary?: string | null
  /** Buttons for what the person can do about it ("Add the text"). */
  children?: ReactNode
  className?: string
}

/** A saved link: what it is, how reading it went, and a way to open the original in a new tab. All text is plain text, never HTML. */
export function LinkCard({ title, href, host, site, kind = 'link', status, note, facts = [], summary, children, className }: Props) {
  const state = STATUS[status]
  return (
    <article className={cx('rounded-md border border-line bg-surface px-4 py-3.5 shadow-edge', className)} data-testid="link-card" data-status={status} data-kind={kind}>
      <div className="flex items-start gap-3">
        <Icon icon={kind === 'video' ? PlayCircle : Globe} size={20} className="mt-0.5 shrink-0 text-fg-3" />
        <div className="min-w-0 flex-1">
          <p className="m-0 break-words text-title text-fg" data-testid="link-title">
            {title}
          </p>
          <p className="m-0 mt-0.5 truncate font-machine text-mono-sm text-fg-3">{[site, host].filter(Boolean).join(' · ')}</p>
          {facts.length > 0 && (
            <p className="m-0 mt-0.5 font-machine text-mono-sm text-fg-3" data-testid="link-facts">
              {facts.join(' · ')}
            </p>
          )}
        </div>
      </div>
      <p className="m-0 mt-3 flex flex-wrap items-center gap-2">
        <Chip dot={state.tone} kind="label" className="h-7">
          <span data-testid="link-status">{state.label}</span>
        </Chip>
        {note && <span className="font-machine text-mono-sm text-fg-3">{note}</span>}
      </p>
      {summary && (
        <p className="m-0 mt-3 measure text-pretty text-body text-fg-2" data-testid="link-summary">
          {summary}
        </p>
      )}
      <div className="mt-3.5 flex flex-wrap items-center gap-2">
        {isWebAddress(href) && (
          <ButtonLink href={href} target="_blank" rel="noopener noreferrer" size="sm" icon={<Icon icon={ExternalLink} />} data-testid="link-open">
            Open original
          </ButtonLink>
        )}
        {children}
      </div>
    </article>
  )
}
