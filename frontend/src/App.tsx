import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getUpcoming, type Turn } from './api/client'
import { Composer } from './components/Composer'
import { Conversation } from './components/Conversation'
import { Inspector } from './components/Inspector'
import { ItemContext, type ItemActions, type ItemField } from './components/itemContext'
import { ItemSheet } from './components/ItemSheet'
import { UpcomingPage } from './components/UpcomingPage'
import { BootError, Connecting, SignedOut } from './components/Screens'
import { TopBar } from './components/TopBar'
import { useConversation } from './hooks/useConversation'
import { useHeldActions } from './hooks/useHeldActions'
import { useMediaQuery } from './hooks/useMediaQuery'
import { useModelPick } from './hooks/useModelPick'
import { useSession, type Session } from './hooks/useSession'
import { useUsage } from './hooks/useUsage'
import { HeldContext } from './trail/context'
import { Dot, cx, useToast, type SheetMode } from './ui'

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

type View = 'chat' | 'upcoming'

function viewOf(hash: string): View {
  return hash === '#/upcoming' ? 'upcoming' : 'chat'
}

/** The page shown: the chat, or Upcoming (#/upcoming), kept in the URL so it can be linked. */
function useView(): View {
  const [view, setView] = useState<View>(() => viewOf(window.location.hash))
  useEffect(() => {
    const onHash = () => {
      setView(viewOf(window.location.hash))
      window.scrollTo({ top: 0 })
    }
    window.addEventListener('hashchange', onHash)
    return () => {
      window.removeEventListener('hashchange', onHash)
    }
  }, [])
  return view
}

/** The due-soon note (FR-7.2): built in code on the server, shown once when the chat opens. */
function useDueSoon(workspaceId: string): string | null {
  const [note, setNote] = useState<string | null>(null)
  useEffect(() => {
    getUpcoming(workspaceId, 1).then(
      (u) => {
        setNote(u.note ?? null)
      },
      () => {
        setNote(null)
      },
    )
  }, [workspaceId])
  return note
}

function Workspace({ session }: { session: Session }) {
  const { workspace, meta, me } = session
  const usage = useUsage()
  const { model, pick } = useModelPick(meta.picker)
  const toast = useToast()
  const [draft, setDraft] = useState('')
  const [inspecting, setInspecting] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<{ id: string; focus?: ItemField } | null>(null)
  const view = useView()
  const dueSoon = useDueSoon(workspace.id)
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

  const itemActions = useMemo<ItemActions>(
    () => ({
      open: (id, focus) => {
        setEditing({ id, focus })
      },
    }),
    [],
  )

  const send = useCallback(
    (message: string) => {
      setDraft('')
      void conversation.send(message, model).then(() => composerRef.current?.focus())
    },
    [conversation, model],
  )

  return (
    <HeldContext.Provider value={held.actions}>
      <ItemContext.Provider value={itemActions}>
        <a
          href="#composer"
          className="sr-only z-50 rounded-sm bg-surface-3 px-3 py-2 text-label text-fg focus:not-sr-only focus:fixed focus:left-2 focus:top-2"
        >
          Skip to message box
        </a>
        <TopBar
          me={me}
          providerMode={meta.provider_mode}
          picker={meta.picker ?? null}
          model={model}
          onModel={pick}
          usage={usage.usage}
          last={usage.last}
          delta={usage.delta}
          docked={docked}
          view={view}
        />
        <main id="main" className={cx('px-4 pb-48 pt-20 md:px-6 lg:px-8', docked && '2xl:mr-115')}>
          {view === 'upcoming' ? (
            <UpcomingPage workspaceId={workspace.id} timezone={workspace.timezone} onTurn={onTurnCreated} />
          ) : (
            <>
              {dueSoon && (
                <p className="mx-auto mb-6 flex w-full max-w-180 flex-wrap items-center gap-2 text-label font-normal text-fg-2" data-testid="due-soon" role="status">
                  <Dot tone="warn" />
                  {dueSoon}{' '}
                  <a href="#/upcoming" className="text-fg underline underline-offset-2">
                    See Upcoming
                  </a>
                </p>
              )}
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
            </>
          )}
        </main>
        {view === 'chat' && (
          <Composer value={draft} onChange={setDraft} onSend={send} sending={conversation.sending} inputRef={composerRef} docked={docked} />
        )}
        <Inspector turn={inspected} open={open} mode={mode} onClose={close} timezone={workspace.timezone} quotaNow={usage.usage} />
        <ItemSheet
          itemId={editing?.id ?? null}
          focus={editing?.focus}
          mode={mode}
          timezone={workspace.timezone}
          onClose={() => {
            setEditing(null)
          }}
          onTurn={onTurnCreated}
        />
      </ItemContext.Provider>
    </HeldContext.Provider>
  )
}
