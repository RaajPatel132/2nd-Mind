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
