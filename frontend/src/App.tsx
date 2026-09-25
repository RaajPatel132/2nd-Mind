import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Turn } from './api/client'
import { Composer } from './components/Composer'
import { Conversation } from './components/Conversation'
import { Inspector } from './components/Inspector'
import { BootError, Connecting, SignedOut } from './components/Screens'
import { TopBar } from './components/TopBar'
import { useConversation } from './hooks/useConversation'
import { useHeldActions } from './hooks/useHeldActions'
import { useMediaQuery } from './hooks/useMediaQuery'
import { useSession, type Session } from './hooks/useSession'
import { useUsage } from './hooks/useUsage'
import { HeldContext } from './trail/context'
import { cx, useToast, type SheetMode } from './ui'

export default function App() {
  const state = useSession()
  if (state.status === 'loading') return <Connecting />
  if (state.status === 'signed-out') return <SignedOut onSignIn={state.retry} />
  if (state.status === 'error') return <BootError message={state.message} onRetry={state.retry} />
  return <Workspace key={state.session.workspace.id} session={state.session} />
}

function isTyping(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))
}

function Workspace({ session }: { session: Session }) {
  const { workspace, meta, me } = session
  const usage = useUsage()
  const toast = useToast()
  const [draft, setDraft] = useState('')
  const [inspecting, setInspecting] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const wide = useMediaQuery('(min-width: 1440px)')
  const phone = useMediaQuery('(max-width: 767px)')
  const mode: SheetMode = wide ? 'docked' : phone ? 'bottom' : 'overlay'

  const conversation = useConversation(workspace.id, { onQuota: usage.report })
  const { turns, addTurn } = conversation
  const refreshUsage = usage.refresh
  const onTurnCreated = useCallback(
    (turn: Turn) => {
      addTurn(turn)
      void refreshUsage()
    },
    [addTurn, refreshUsage],
  )
  const anyHeld = useMemo(
    () => turns.some((t) => t.events?.some((e) => e.event.type === 'memory_diff' && e.event.entries.some((x) => x.op === 'held'))),
    [turns],
  )
  const held = useHeldActions(workspace.id, anyHeld, onTurnCreated)
  useEffect(() => {
    if (held.error) toast(held.error)
  }, [held.error, toast])

  const latestId = [...turns].reverse().find((t) => t.id !== null)?.id ?? null
  const inspected = turns.find((t) => t.id === inspecting) ?? null
  const docked = mode === 'docked' && open && inspected !== null

  const inspect = useCallback((turnId: string) => {
    setInspecting(turnId)
    setOpen(true)
  }, [])
  const close = useCallback(() => {
    setOpen(false)
  }, [])

  // F6 toggles the inspector for the latest turn; / focuses the composer from anywhere else.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'F6') {
        e.preventDefault()
        if (open) setOpen(false)
        else if (latestId) inspect(latestId)
      } else if (e.key === '/' && !isTyping(e.target) && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault()
        composerRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
    }
  }, [open, latestId, inspect])

  const send = useCallback(
    (message: string) => {
      setDraft('')
      void conversation.send(message).then(() => composerRef.current?.focus())
    },
    [conversation],
  )

  return (
    <HeldContext.Provider value={held.actions}>
      <a
        href="#composer"
        className="sr-only z-50 rounded-sm bg-surface-3 px-3 py-2 text-label text-fg focus:not-sr-only focus:fixed focus:left-2 focus:top-2"
      >
        Skip to message box
      </a>
      <TopBar me={me} providerMode={meta.provider_mode} usage={usage.usage} last={usage.last} delta={usage.delta} docked={docked} />
      <main id="main" className={cx('px-4 pb-48 pt-20 md:px-6 lg:px-8', docked && '2xl:mr-115')}>
        <Conversation
          turns={turns}
          loading={conversation.loading}
          loadError={conversation.loadError}
          hasEarlier={conversation.hasEarlier}
          timezone={workspace.timezone}
          busy={held.busy}
          onLoadEarlier={conversation.loadEarlier}
          onInspect={inspect}
          onUndo={held.undo}
          onPick={(text) => {
            setDraft(text)
            composerRef.current?.focus()
          }}
        />
      </main>
      <Composer value={draft} onChange={setDraft} onSend={send} sending={conversation.sending} inputRef={composerRef} docked={docked} />
      <Inspector turn={inspected} open={open} mode={mode} onClose={close} timezone={workspace.timezone} quotaNow={usage.usage} />
    </HeldContext.Provider>
  )
}
