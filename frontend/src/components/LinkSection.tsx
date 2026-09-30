/**
 * The link part of a memory's sheet (S4.9): what was read from the page, how it went, a way to
 * open the original, and "Add the text" when the page could only be read in part. Everything the
 * page said is shown as plain text.
 */
import { useId, useState } from 'react'
import { addItemText, type ItemDetail, type LinkSource } from '../api/client'
import { formatDay, formatLength } from '../lib/format'
import { Button, Disclosure, LinkCard, TextAreaField, type LinkStatus } from '../ui'

const MAX_TEXT = 100_000

function factsOf(source: LinkSource): string[] {
  if (source.kind === 'video') {
    return [source.channel, source.duration_s != null ? formatLength(source.duration_s) : null].filter((f): f is string => Boolean(f))
  }
  return [source.author, source.published_at ? formatDay(source.published_at) : null].filter((f): f is string => Boolean(f))
}

type Props = {
  title: string
  summary: string | null
  source: LinkSource
  onUpdated: (detail: ItemDetail) => void
}

export function LinkSection({ title, summary, source, onUpdated }: Props) {
  const formId = useId()
  const [adding, setAdding] = useState(false)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const status: LinkStatus = source.fetch_status
  const canAdd = status === 'partial' || status === 'failed'
  const tooLong = text.length > MAX_TEXT

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      onUpdated(await addItemText(source.item_id, text.trim()))
      setAdding(false)
      setText('')
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't go through. Try again.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid gap-3" data-testid="link-section">
      <LinkCard
        title={title}
        href={source.url}
        host={source.final_host}
        site={source.site}
        kind={source.kind === 'video' ? 'video' : 'link'}
        status={status}
        note={source.fetch_reason}
        facts={factsOf(source)}
        summary={summary}
      >
        {canAdd && (
          <Button
            size="sm"
            aria-expanded={adding}
            aria-controls={formId}
            onClick={() => {
              setAdding((a) => !a)
            }}
            data-testid="link-add-text"
          >
            Add the text
          </Button>
        )}
      </LinkCard>
      {canAdd && (
        <Disclosure id={formId} open={adding}>
          <form
            className="grid gap-3 pb-1"
            aria-label="Add the text of this page"
            onSubmit={(e) => {
              e.preventDefault()
              if (text.trim() && !tooLong) void submit()
            }}
          >
            <TextAreaField
              label="Paste the text of the page"
              hint="I read it the same way I read the page, and mark it as the page's words, not yours."
              value={text}
              onChange={(e) => {
                setText(e.target.value)
              }}
              data-testid="link-text"
            />
            {tooLong && (
              <p role="alert" className="m-0 text-label text-bad">
                That is longer than I can read at once. Paste the part you care about.
              </p>
            )}
            {error && (
              <p role="alert" className="m-0 text-label text-bad">
                {error}
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button type="submit" variant="primary" size="sm" disabled={busy || !text.trim() || tooLong} data-testid="link-read-text">
                {busy ? 'Reading…' : 'Read this text'}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setAdding(false)
                }}
              >
                Cancel
              </Button>
            </div>
          </form>
        </Disclosure>
      )}
    </div>
  )
}
