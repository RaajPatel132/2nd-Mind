import { useEffect, useState } from 'react'
import { ApiError, devLogin, getMe, getMeta, type Me, type Meta } from '../api/client'

export type Session = { me: Me; workspace: Me['workspaces'][number]; meta: Meta }

type State =
  | { status: 'loading' }
  | { status: 'ready'; session: Session }
  | { status: 'signed-out' }
  /** Production: signing in needs an email and the access code. */
  | { status: 'needs-code'; meta: Meta }
  | { status: 'error'; message: string }

/**
 * Signs in with the dev identity when there is no session (DEV_AUTH only), except right after
 * signing out (`/?signed-out`), which shows the signed-out screen instead.
 */
export function useSession(): State & {
  retry: () => void
  signIn: (email: string, accessCode: string) => Promise<string | null>
} {
  const [state, setState] = useState<State>({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    const signedOut = new URLSearchParams(window.location.search).has('signed-out')
    async function load(): Promise<void> {
      try {
        const [meta, existing] = await Promise.all([getMeta(), getMe()])
        if (!existing && signedOut && attempt === 0) {
          if (!cancelled) setState({ status: 'signed-out' })
          return
        }
        if (!existing && meta.access_code_required) {
          if (!cancelled) setState({ status: 'needs-code', meta })
          return
        }
        const me = existing ?? (await devLogin())
        const workspace = me.workspaces[0]
        if (!workspace) throw new Error('No workspace found for this account.')
        if (!cancelled) setState({ status: 'ready', session: { me, workspace, meta } })
      } catch (err) {
        const message =
          err instanceof ApiError && err.status === 404
            ? 'Sign-in is not available on this server yet.'
            : err instanceof Error
              ? err.message
              : 'Could not reach the server.'
        if (!cancelled) setState({ status: 'error', message })
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [attempt])

  /** Signs in with the access code; returns why it failed, or null once it worked. */
  async function signIn(email: string, accessCode: string): Promise<string | null> {
    try {
      const me = await devLogin({ email, accessCode })
      const workspace = me.workspaces[0]
      if (!workspace) return 'No workspace found for this account.'
      const meta = await getMeta()
      setState({ status: 'ready', session: { me, workspace, meta } })
      return null
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) return 'Too many attempts. Wait a moment and try again.'
      if (err instanceof ApiError && err.status === 401) return err.message
      return err instanceof Error ? err.message : 'Could not reach the server.'
    }
  }

  return {
    ...state,
    signIn,
    retry: () => {
      if (window.location.search) window.history.replaceState(null, '', '/')
      setState({ status: 'loading' })
      setAttempt((a) => a + 1)
    },
  }
}
