export function formatMs(ms: number): string {
  if (ms < 1000) return `${String(Math.round(ms))} ms`
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`
}

export function formatUsd(usd: number): string {
  if (usd === 0) return '$0'
  if (usd < 0.0001) return `$${usd.toFixed(6)}`
  if (usd < 0.01) return `$${usd.toFixed(5)}`
  return `$${usd.toFixed(4)}`
}

export function formatTokens(n: number): string {
  return n.toLocaleString('en-US')
}

export function shortId(id: string): string {
  return id.replace(/-/g, '').slice(-8)
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

/** A diff or argument value as short plain text. */
export function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (Array.isArray(value)) return value.length ? value.map(formatValue).join(', ') : '—'
  if (typeof value === 'object') return JSON.stringify(value)
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

export function truncate(text: string, max = 80): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${String(n)} ${n === 1 ? one : many}`
}

/** An instant as a short date and time in the given IANA timezone ("23 Sep 2026, 10:00"). */
export function formatInZone(iso: string, timeZone: string): string {
  try {
    return new Date(iso).toLocaleString('en-GB', {
      timeZone,
      day: 'numeric',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}
