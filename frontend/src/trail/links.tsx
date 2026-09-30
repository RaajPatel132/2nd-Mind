/** The `fetch` step's two layers (S4.9): what happened to each saved link, in plain words, then
 * the request's facts in Machine type. Only hosts are ever shown, never a path or a query. */
import type { FetchEvent } from '../api/client'
import { formatTokens, plural } from '../lib/format'
import { KV } from './parts'
import type { StepContext } from './types'

type P = { ctx: StepContext }

function sentence(f: FetchEvent): string {
  switch (f.status) {
    case 'pending':
      return `I'm reading ${f.host || 'the page'} now. What you said is already saved; the page fills in when I'm done.`
    case 'full':
      return f.video
        ? `I read the video's title, channel and length. There's no transcript, so I can't search what is said in it.`
        : `I read the page and kept a short summary${f.chunks ? ` and ${plural(f.chunks, 'passage')}` : ''}, so I can find it by what it says. What the page says is marked as coming from the page, not from you.`
    case 'partial':
      return `I could only read part of it${f.reason ? ` (${f.reason})` : ''}. You can paste the text from the memory and I'll read that instead.`
    case 'failed':
      return "I couldn't open the page, so I kept what you said. Nothing else was saved."
    case 'refused':
      return `I didn't open it${f.reason ? `: ${f.reason}` : ''}. No request was made, and I saved the link with what you said.`
  }
}

export function FetchPlain({ ctx }: P) {
  const list = ctx.facts.fetches
  if (list.length === 0) return <p className="m-0 measure text-pretty text-body text-fg">You had already saved this link, so I didn't read it again.</p>
  return (
    <div className="grid gap-2">
      {list.map((f) => (
        <p key={f.item_id} className="m-0 measure text-pretty text-body text-fg" data-testid="fetch-plain">
          {sentence(f)}
        </p>
      ))}
    </div>
  )
}

function rows(f: FetchEvent): [string, string][] {
  const out: [string, string][] = [['host', f.host || '—'], ['status', f.status]]
  if (f.status_code != null) out.push(['http', String(f.status_code)])
  if (f.bytes != null) out.push(['bytes', formatTokens(f.bytes)])
  if (f.redirects != null) out.push(['redirects', String(f.redirects)])
  if (f.content_type) out.push(['content type', f.content_type])
  if (f.extraction_method) out.push(['extraction', f.extraction_method])
  if (f.word_count != null) out.push(['words', formatTokens(f.word_count)])
  if (f.chunks != null) out.push(['passages', String(f.chunks)])
  if (f.rule) out.push(['rule', f.rule])
  return out
}

export function FetchTech({ ctx }: P) {
  const list = ctx.facts.fetches
  if (list.length === 0) return <p className="m-0 text-label font-normal text-fg-3">No request was made.</p>
  return (
    <div className="grid gap-3">
      {list.map((f) => (
        <KV key={f.item_id} rows={rows(f)} testId="fetch-facts" />
      ))}
    </div>
  )
}
