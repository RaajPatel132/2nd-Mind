import { useEffect, useState } from 'react'
import { getHeadline, type Headline } from '../api/headline'

export type HeadlineState = { status: 'loading' } | { status: 'ready'; headline: Headline } | { status: 'unavailable' }

/** The landing page's numbers, loaded once. */
export function useHeadline(): HeadlineState {
  const [state, setState] = useState<HeadlineState>({ status: 'loading' })
  useEffect(() => {
    let live = true
    void getHeadline().then((headline) => {
      if (live) setState(headline ? { status: 'ready', headline } : { status: 'unavailable' })
    })
    return () => {
      live = false
    }
  }, [])
  return state
}
