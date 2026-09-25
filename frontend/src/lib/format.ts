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

/** Seconds with two decimals under 10 s ("2.14 s"), else one. */
export function formatSeconds(ms: number): string {
  return ms < 1000 ? formatMs(ms) : `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`
}

/** "−1.2k" style: a compact signed token count. */
export function formatCompact(n: number): string {
  if (Math.abs(n) < 1000) return String(n)
  return `${(n / 1000).toFixed(1)}k`
}

/** "Thu 24 Sep" for a resolved date value ("2026-09-24", "2026-09-24T19:00", "2027-05"). */
export function formatDay(value: string): string {
  const day = /^\d{4}-\d{2}-\d{2}/.exec(value)?.[0]
  if (!day) {
    const month = /^(\d{4})-(\d{2})$/.exec(value)
    if (month) {
      const d = new Date(Date.UTC(Number(month[1]), Number(month[2]) - 1, 1))
      return d.toLocaleDateString('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' })
    }
    return value
  }
  const d = new Date(`${day}T12:00:00Z`)
  return d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' }).replace(',', '')
}

/** The weekday of an instant in a timezone ("Friday"). */
export function weekdayIn(iso: string, timeZone: string): string {
  try {
    return new Date(iso).toLocaleDateString('en-GB', { weekday: 'long', timeZone })
  } catch {
    return ''
  }
}

/** "10:42" in the viewer's clock. */
export function formatClock(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

/** A model's quota weight against the baseline: "1×", "2.5×", "0.42×". */
export function formatWeight(weight: number): string {
  const text = weight >= 1 ? String(Math.round(weight * 100) / 100) : weight.toFixed(2).replace(/0$/, '')
  return `${text}×`
}

/** How fast a model uses the quota against the baseline, in words ("Uses quota 2.5× as fast"). */
export function weightNote(weight: number, isBaseline: boolean): string {
  if (isBaseline) return 'The baseline for your quota'
  if (weight === 1) return 'Uses quota at the baseline rate'
  if (weight > 1) return `Uses quota ${formatWeight(weight)} as fast`
  return `Uses ${String(Math.round((1 - weight) * 100))}% less quota`
}

export function initialsOf(email: string): string {
  const name = email.split('@')[0] ?? ''
  const parts = name.split(/[._-]+/).filter(Boolean)
  const letters = parts.length > 1 ? `${parts[0]?.[0] ?? ''}${parts[1]?.[0] ?? ''}` : name.slice(0, 2)
  return letters.toUpperCase() || '?'
}
