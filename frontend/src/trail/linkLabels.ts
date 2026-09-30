/** The `fetch` step's labels and chips (S4.9): one row for every link a message saved. */
import type { FetchEvent } from '../api/client'
import { plural } from '../lib/format'
import type { StepContext } from './types'

function hostLabel(f: FetchEvent): string {
  return f.host || 'the page'
}

/** The line for one link, from the status table (the server writes it; the row repeats it). */
function line(f: FetchEvent, waiting: boolean): string {
  if (f.status === 'pending') return waiting ? `Reading ${hostLabel(f)}` : `Still waiting to read ${hostLabel(f)}`
  return f.message || `Read ${hostLabel(f)}`
}

export function fetchDone({ facts, view }: StepContext): string {
  const list = facts.fetches
  const [only] = list
  if (!only) return 'Already saved that link'
  if (list.length === 1) return line(only, view.state === 'running')
  const pending = list.filter((f) => f.status === 'pending').length
  if (pending > 0) return view.state === 'running' ? `Reading ${plural(pending, 'link')}` : `Still waiting on ${plural(pending, 'link')}`
  const read = list.filter((f) => f.status === 'full' || f.status === 'partial').length
  const refused = list.filter((f) => f.status === 'refused').length
  if (refused === list.length) return `Didn't open ${plural(refused, 'link')}`
  return refused ? `Read ${String(read)} of ${String(list.length)} links` : `Read ${plural(read, 'link')}`
}

export function fetchChips({ facts }: StepContext): string[] {
  const list = facts.fetches
  const [only] = list
  if (!only) return []
  if (list.length > 1) return [plural(list.length, 'link')]
  const chips: string[] = [only.host]
  if (only.status === 'full' && only.chunks) chips.push(plural(only.chunks, 'passage'))
  else if (only.status === 'partial' && only.reason) chips.push(only.reason)
  else if (only.status === 'refused' && only.rule) chips.push(only.rule)
  return chips.filter(Boolean).slice(0, 3)
}
