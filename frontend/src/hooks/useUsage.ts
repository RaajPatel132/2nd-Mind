import { useCallback, useEffect, useState } from 'react'
import { getUsage, type Usage } from '../api/client'
import { formatCompact } from '../lib/format'

export type LastSpend = { tokens: number; costUsd: number }

/** The quota ring's data: loaded once, then updated from each turn's `turn.completed`. */
export function useUsage() {
  const [usage, setUsage] = useState<Usage | null>(null)
  const [last, setLast] = useState<LastSpend | null>(null)
  const [delta, setDelta] = useState<{ key: number; text: string } | null>(null)

  const refresh = useCallback(async () => {
    try {
      setUsage(await getUsage())
    } catch {
      // The ring stays as it was; the next turn's frame brings a fresh value.
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    getUsage()
      .then((u) => {
        if (!cancelled) setUsage(u)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [])

  const report = useCallback((next: Usage, spent: number, costUsd: number) => {
    setUsage(next)
    setLast({ tokens: spent, costUsd })
    if (spent > 0) setDelta({ key: Date.now(), text: `−${formatCompact(spent)}` })
  }, [])

  return { usage, last, delta, report, refresh }
}
