import { useCallback, useState } from 'react'
import type { Picker } from '../api/client'

const KEY = 'secondmind.model'
export const AUTO = 'auto'

function stored(): string | null {
  try {
    return window.localStorage.getItem(KEY)
  } catch {
    return null
  }
}

/** What this person's plan may pick besides Auto: the choices offered to their tier and usable. */
export function offeredTo(picker: Picker | null | undefined, tier: string | undefined) {
  return picker && tier ? picker.choices.filter((c) => c.available && (c.tiers as string[]).includes(tier)) : []
}

/**
 * The model for new turns: null is Auto (no pick is sent, and each step uses the model that
 * suits it). A viewer's earlier pick counts only while their plan still offers it.
 */
export function useModelPick(picker: Picker | null | undefined, tier: string | undefined) {
  const [picked, setPicked] = useState<string | null>(() => stored())
  const offered = offeredTo(picker, tier)
  const model = picked && offered.some((c) => c.id === picked) ? picked : null

  const pick = useCallback((id: string) => {
    const next = id === AUTO ? null : id
    setPicked(next)
    try {
      if (next) window.localStorage.setItem(KEY, next)
      else window.localStorage.removeItem(KEY)
    } catch {
      // Private mode or blocked storage: the pick lasts for this visit.
    }
  }, [])

  return { model, pick, offered }
}
