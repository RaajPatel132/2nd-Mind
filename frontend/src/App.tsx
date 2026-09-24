import { useCallback, useEffect, useRef, useState } from 'react'
import { Chat } from './components/Chat'
import { GlassBox } from './components/GlassBox'
import { useConversation } from './hooks/useConversation'
import { useSession, type Session } from './hooks/useSession'

export default function App() {
  const state = useSession()
  if (state.status === 'loading') {
    return <Centered>Connecting…</Centered>
  }
  if (state.status === 'error') {
    return (
      <Centered>
        <p role="alert" className="text-red-800">
          {state.message}
        </p>
      </Centered>
    )
  }
  return <Workspace key={state.session.workspace.id} session={state.session} />
}

function Workspace({ session }: { session: Session }) {
  const [selectedTurnId, setSelectedTurnId] = useState<string | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const glassRef = useRef<HTMLElement>(null)
  const selectIfNone = useCallback((turnId: string) => {
    setSelectedTurnId((current) => current ?? turnId)
  }, [])
  const conversation = useConversation(session.workspace.id, setSelectedTurnId, selectIfNone)
  const selected = conversation.turns.find((t) => t.id === selectedTurnId)?.turn ?? null
  const answer = session.meta.routes.find((r) => r.step === 'answer')

  const focusGlassBox = useCallback(() => {
    setDrawerOpen(true)
    requestAnimationFrame(() => glassRef.current?.focus())
  }, [])

  const selectTurn = useCallback(
    (turnId: string) => {
      setSelectedTurnId(turnId)
      focusGlassBox()
    },
    [focusGlassBox],
  )

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'F6') {
        event.preventDefault()
        if (glassRef.current?.contains(document.activeElement)) {
          setDrawerOpen(false)
          composerRef.current?.focus()
        } else {
          focusGlassBox()
        }
      } else if (event.key === 'Escape' && drawerOpen) {
        setDrawerOpen(false)
        composerRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
    }
  }, [drawerOpen, focusGlassBox])

  return (
    <div className="flex h-dvh flex-col bg-slate-100 text-slate-900">
      <a href="#composer" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2">
        Skip to message box
      </a>
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-slate-200 bg-white px-4 py-2.5">
        <h1 className="text-base font-semibold tracking-tight">2nd Mind</h1>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700">My memory</span>
        {answer && (
          <span className="hidden truncate text-xs text-slate-600 sm:inline" data-testid="answer-model">
            answer: {answer.provider} · {answer.model}
            {session.meta.provider_mode !== 'live' ? ` (${session.meta.provider_mode})` : ''}
          </span>
        )}
        <button
          type="button"
          className="btn-chip ml-auto lg:hidden"
          aria-expanded={drawerOpen}
          aria-controls="glass-box-panel"
          onClick={() => {
            if (drawerOpen) setDrawerOpen(false)
            else focusGlassBox()
          }}
          data-testid="toggle-glass-box"
        >
          Glass box
        </button>
      </header>
      <div className="relative flex min-h-0 flex-1">
        <Chat
          turns={conversation.turns}
          loading={conversation.loading}
          loadError={conversation.loadError}
          sending={conversation.sending}
          hasEarlier={conversation.hasEarlier}
          selectedTurnId={selectedTurnId}
          composerRef={composerRef}
          onSend={conversation.send}
          onSelect={selectTurn}
          onLoadEarlier={conversation.loadEarlier}
        />
        {drawerOpen && (
          <div
            className="fixed inset-0 z-20 bg-slate-900/30 lg:hidden"
            aria-hidden
            onClick={() => {
              setDrawerOpen(false)
            }}
          />
        )}
        <aside
          id="glass-box-panel"
          className={`${
            drawerOpen ? 'fixed inset-y-0 right-0 z-30 flex w-full max-w-md shadow-2xl' : 'hidden'
          } border-l border-slate-200 lg:static lg:z-auto lg:flex lg:w-[26rem] lg:max-w-none lg:shrink-0 lg:shadow-none`}
        >
          <div className="w-full">
            <GlassBox
              turn={selected}
              pending={conversation.sending}
              regionRef={glassRef}
              onClose={() => {
                setDrawerOpen(false)
                composerRef.current?.focus()
              }}
            />
          </div>
        </aside>
      </div>
    </div>
  )
}

function Centered({ children }: { children: React.ReactNode }) {
  return <main className="flex min-h-dvh items-center justify-center bg-slate-100 p-6 text-slate-700">{children}</main>
}
