import { useEffect, useState } from 'react'
import { ApiError, devLogin, getMe, getMeta, openPersona, openScratch, resetPersona, startGuest, type Me, type Meta } from '../api/client'

export type Session = { me: Me; workspace: Me['workspaces'][number]; meta: Meta }

const REMEMBERED = 'sm.workspace'

/** The workspace to open: the one this browser used last, if it is still the person's, else the first. */
function chosen(me: Me): Me['workspaces'][number] | undefined {
  try {
    const id = window.localStorage.getItem(REMEMBERED)
    const found = id ? me.workspaces.find((w) => w.id === id) : undefined
    if (found) return found
  } catch {
    // storage can be blocked: the first workspace is fine
  }
  return me.workspaces[0]
}

function remember(id: string): void {
  try {
    window.localStorage.setItem(REMEMBERED, id)
  } catch {
    // a convenience only
  }
}

type State =
  | { status: 'loading' }
  | { status: 'ready'; session: Session }
  | { status: 'signed-out' }
  /** No session: the landing page, where a visitor tries the sample persona or signs in (S4.13). */
  | { status: 'landing'; meta: Meta }
  | { status: 'error'; message: string }

/**
 * Opens the person's session, or the landing page when there is none. Right after signing out
 * (`/?signed-out`) it shows the signed-out screen instead.
 */
export function useSession(): State & {
  retry: () => void
  signIn: (email: string, accessCode: string) => Promise<string | null>
  /** Open the sample persona as a guest (the access code is asked for until the site opens); why it failed, or null. */
  tryPersona: (accessCode?: string) => Promise<string | null>
  /** Dev only: sign in as the dev user, with no code. */
  devSignIn: () => Promise<string | null>
  /** Open another of the person's workspaces. */
  switchTo: (workspaceId: string) => void
  /** Open the sample persona (made on first use), or start it over; returns why it failed, or null. */
  sample: (reset: boolean) => Promise<string | null>
  /** A guest's empty memory of their own. */
  scratch: () => Promise<string | null>
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
        if (!existing) {
          if (!cancelled) setState({ status: 'landing', meta })
          return
        }
        const me = existing
        const workspace = chosen(me)
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

  async function enter(me: Me): Promise<string | null> {
    const workspace = chosen(me)
    if (!workspace) return 'No workspace found for this account.'
    const meta = await getMeta()
    setState({ status: 'ready', session: { me, workspace, meta } })
    return null
  }

  async function tryPersona(accessCode?: string): Promise<string | null> {
    try {
      const me = await startGuest(accessCode)
      const sample = me.workspaces.find((w) => w.kind === 'persona_copy')
      if (sample) remember(sample.id)
      return await enter(me)
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) return err.message
      if (err instanceof ApiError && err.status === 429) return err.message
      return err instanceof Error ? err.message : 'Could not open the sample.'
    }
  }

  async function devSignIn(): Promise<string | null> {
    try {
      return await enter(await devLogin())
    } catch (err) {
      return err instanceof Error ? err.message : 'Could not sign in.'
    }
  }

  async function scratch(): Promise<string | null> {
    try {
      const workspace = await openScratch()
      const me = await getMe()
      if (!me) return 'Sign in again to open a scratch memory.'
      remember(workspace.id)
      return await enter(me)
    } catch (err) {
      return err instanceof Error ? err.message : 'Could not open a scratch memory.'
    }
  }

  function switchTo(workspaceId: string): void {
    setState((current) => {
      if (current.status !== 'ready') return current
      const workspace = current.session.me.workspaces.find((w) => w.id === workspaceId)
      if (!workspace) return current
      remember(workspace.id)
      return { status: 'ready', session: { ...current.session, workspace } }
    })
  }

  async function sample(reset: boolean): Promise<string | null> {
    try {
      const workspace = await (reset ? resetPersona() : openPersona())
      const me = await getMe()
      if (!me) return 'Sign in again to open the sample.'
      const meta = await getMeta()
      const opened = me.workspaces.find((w) => w.id === workspace.id) ?? workspace
      remember(opened.id)
      setState({ status: 'ready', session: { me, workspace: opened, meta } })
      return null
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) return 'Too many copies just now. Wait a moment.'
      return err instanceof Error ? err.message : 'Could not open the sample.'
    }
  }

  return {
    ...state,
    signIn,
    tryPersona,
    devSignIn,
    switchTo,
    sample,
    scratch,
    retry: () => {
      if (window.location.search) window.history.replaceState(null, '', '/')
      setState({ status: 'loading' })
      setAttempt((a) => a + 1)
    },
  }
}
