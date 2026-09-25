import { lazy, Suspense, useEffect, useState } from 'react'
import { getMeta } from '../api/client'
import { BootError, Connecting } from '../components/Screens'

// Loaded only on /design, so the living reference adds nothing to the app's first load.
const DesignPage = lazy(() => import('./DesignPage'))

/** `/design` is served in every environment except production (from /v1/meta). */
export function DesignRoute() {
  const [state, setState] = useState<'loading' | 'allowed' | 'hidden' | 'error'>('loading')
  useEffect(() => {
    getMeta()
      .then((meta) => {
        setState(meta.env === 'production' ? 'hidden' : 'allowed')
      })
      .catch(() => {
        setState('error')
      })
  }, [])
  if (state === 'loading') return <Connecting />
  if (state === 'error') return <BootError message="Couldn't reach the server to check this page." onRetry={() => { window.location.reload() }} />
  if (state === 'hidden') return <BootError message="There's no page here." onRetry={() => { window.location.assign('/') }} />
  return (
    <Suspense fallback={<Connecting />}>
      <DesignPage />
    </Suspense>
  )
}
