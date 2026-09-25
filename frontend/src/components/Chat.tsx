import { useEffect, useRef, useState, type KeyboardEvent, type RefObject } from 'react'
import type { ChatTurn } from '../hooks/useConversation'

type Props = {
  turns: ChatTurn[]
  loading: boolean
  loadError: string | null
  sending: boolean
  hasEarlier: boolean
  selectedTurnId: string | null
  composerRef: RefObject<HTMLTextAreaElement | null>
  onSend: (message: string) => Promise<void>
  onSelect: (turnId: string) => void
  onLoadEarlier: () => Promise<void>
}

export function Chat(props: Props) {
  const { turns, loading, loadError, sending, hasEarlier, selectedTurnId, composerRef } = props
  const [draft, setDraft] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)
  const lastOutput = turns.at(-1)?.output

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [turns.length, lastOutput])

  async function submit(event?: { preventDefault: () => void }) {
    event?.preventDefault()
    const message = draft.trim()
    if (!message || sending) return
    setDraft('')
    await props.onSend(message)
    composerRef.current?.focus()
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      void submit()
    }
  }

  return (
    <section aria-label="Chat" className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-6" aria-live="polite" aria-busy={sending}>
        <ol className="mx-auto flex max-w-2xl flex-col gap-5" data-testid="messages">
          {hasEarlier && (
            <li className="text-center">
              <button type="button" onClick={() => void props.onLoadEarlier()} className="btn-link text-sm">
                Load earlier messages
              </button>
            </li>
          )}
          {loading && <li className="text-sm text-slate-500">Loading history…</li>}
          {loadError && (
            <li role="alert" className="text-sm text-red-700">
              {loadError}
            </li>
          )}
          {!loading && turns.length === 0 && (
            <li className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-600">
              Say hello. Every reply opens its glass box on the right: which model answered, how
              long it took, and what it cost.
            </li>
          )}
          {turns.map((turn) =>
            turn.kind !== 'user' ? (
              <SystemNote key={turn.key} turn={turn} selected={selectedTurnId === turn.id} onSelect={props.onSelect} />
            ) : (
              <li key={turn.key} className="flex flex-col gap-2">
                <p className="max-w-[85%] self-end whitespace-pre-wrap break-words rounded-2xl rounded-br-sm bg-slate-900 px-4 py-2.5 text-white">
                  {turn.input}
                </p>
                <div className="flex max-w-[85%] flex-col items-start gap-1.5 self-start">
                  <div
                    data-testid="assistant-message"
                    className={`whitespace-pre-wrap break-words rounded-2xl rounded-bl-sm px-4 py-2.5 ${
                      turn.status === 'failed'
                        ? 'border border-red-200 bg-red-50 text-red-900'
                        : 'bg-white text-slate-900 shadow-sm ring-1 ring-slate-200'
                    }`}
                  >
                    {turn.output}
                    {turn.status === 'streaming' && (
                      <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-slate-400 align-middle" aria-hidden />
                    )}
                    {turn.status === 'streaming' && !turn.output && <span className="sr-only">Thinking…</span>}
                    {turn.status === 'running' && (
                      <span className="text-sm text-slate-600">Still being answered. Refresh to see the reply.</span>
                    )}
                    {turn.status === 'failed' && <span role="alert">{turn.error ?? 'This turn failed.'}</span>}
                  </div>
                  {turn.id && turn.status !== 'streaming' && (
                    <button
                      type="button"
                      data-testid="open-glass-box"
                      aria-pressed={selectedTurnId === turn.id}
                      onClick={() => {
                        if (turn.id) props.onSelect(turn.id)
                      }}
                      className={`btn-chip ${selectedTurnId === turn.id ? 'bg-indigo-50 text-indigo-800 ring-indigo-300' : ''}`}
                    >
                      <GlassIcon /> Glass box
                    </button>
                  )}
                </div>
              </li>
            ),
          )}
        </ol>
        <div ref={bottomRef} />
      </div>
      <form onSubmit={(e) => void submit(e)} className="border-t border-slate-200 bg-white px-4 py-3 sm:px-6">
        <div className="mx-auto flex max-w-2xl items-end gap-2">
          <label htmlFor="composer" className="sr-only">
            Message
          </label>
          <textarea
            id="composer"
            ref={composerRef}
            data-testid="composer"
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value)
            }}
            onKeyDown={onKeyDown}
            rows={1}
            placeholder="Tell me anything…"
            className="max-h-40 min-h-11 flex-1 resize-y rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-base text-slate-900 placeholder:text-slate-500 focus-visible:border-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          />
          <button type="submit" disabled={sending || !draft.trim()} className="btn-primary h-11" data-testid="send">
            {sending ? 'Sending…' : 'Send'}
          </button>
        </div>
        <p className="mx-auto mt-1.5 hidden max-w-2xl text-xs text-slate-500 sm:block">
          Enter to send · Shift+Enter for a new line · F6 to switch between chat and glass box
        </p>
      </form>
    </section>
  )
}

const NOTE_LABELS: Record<ChatTurn['kind'], string> = {
  user: '',
  undo: 'Undo',
  confirm: 'Confirmed',
  system: 'Housekeeping',
}

/** An undo, confirmation or system turn: not a message, but visible and inspectable. */
function SystemNote({ turn, selected, onSelect }: { turn: ChatTurn; selected: boolean; onSelect: (turnId: string) => void }) {
  return (
    <li data-testid="system-note" data-kind={turn.kind} className="flex flex-col items-center gap-1.5">
      <p className="max-w-[90%] rounded-lg bg-slate-200/70 px-3 py-1.5 text-center text-sm text-slate-800">
        <span className="font-semibold">{NOTE_LABELS[turn.kind]}:</span>{' '}
        {turn.status === 'failed' ? <span role="alert">{turn.error ?? 'This failed.'}</span> : turn.output}
      </p>
      {turn.id && (
        <button
          type="button"
          data-testid="open-glass-box"
          aria-pressed={selected}
          onClick={() => {
            if (turn.id) onSelect(turn.id)
          }}
          className={`btn-chip ${selected ? 'bg-indigo-50 text-indigo-800 ring-indigo-300' : ''}`}
        >
          <GlassIcon /> Glass box
        </button>
      )}
    </li>
  )
}

function GlassIcon() {
  return (
    <svg aria-hidden viewBox="0 0 20 20" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.8">
      <rect x="3" y="3" width="14" height="14" rx="2" />
      <path d="M3 8h14M8 8v9" />
    </svg>
  )
}
