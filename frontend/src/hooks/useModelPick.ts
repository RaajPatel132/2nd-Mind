import { useCallback, useState } from 'react'
import type { Picker } from '../api/client'

const KEY = 'secondmind.model'

function stored(): string | null {
  try {
    return window.localStorage.getItem(KEY)
  } catch {
    return null
  }
}

/**
 * The model picked for new turns: this viewer's last pick while it is still on offer and
 * usable, otherwise the server's default. Null when the server has no picker.
 */
export function useModelPick(picker: Picker | null | undefined) {
  const usable = useCallback((id: string | null) => Boolean(id && picker?.choices.some((c) => c.id === id && c.available)), [picker])
  const [picked, setPicked] = useState<string | null>(() => stored())
  const fallback = picker ? (usable(picker.default) ? picker.default : (picker.choices.find((c) => c.available)?.id ?? null)) : null
  const model = usable(picked) ? picked : fallback

  const pick = useCallback((id: string) => {
    setPicked(id)
    try {
      window.localStorage.setItem(KEY, id)
    } catch {
      // Private mode or blocked storage: the pick lasts for this visit.
    }
  }, [])

  return { model, pick }
}
