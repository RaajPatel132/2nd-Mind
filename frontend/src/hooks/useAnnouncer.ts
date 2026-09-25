import { createContext, useContext } from 'react'

/** Polite, throttled announcements (step results, the finished reply). Tokens are never announced. */
export const AnnounceContext = createContext<(message: string) => void>(() => undefined)

export function useAnnounce(): (message: string) => void {
  return useContext(AnnounceContext)
}
