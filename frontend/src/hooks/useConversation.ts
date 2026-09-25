import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, getTurnEvents, listTurns, streamTurn, type Turn, type Usage } from '../api/client'
import type { StepStart, TrailEvent } from '../trail/model'

export type ChatTurn = {
  key: string
  id: string | null
  /** user turns are messages; undo, confirm and system turns show as compact event rows. */
  kind: Turn['kind']
  input: string
  /** The reply as it arrived, chunk by chunk (each chunk fades in once). */
  chunks: string[]
  status: 'streaming' | 'completed' | 'failed' | 'running'
  turn: Turn | null
  error: string | null
  /** The turn's stored events (null until loaded); a live turn fills them from the stream. */
  events: TrailEvent[] | null
  starts: StepStart[]
  /** Streamed or made in this session: its Trail plays at the server's pace. */
  live: boolean
  sentAt: string
  /** The quota right after this turn, when the stream reported it. */
  quotaAfter: Usage | null
}

export function outputOf(turn: ChatTurn): string {
  return turn.chunks.join('')
}

function fromTurn(turn: Turn, live = false): ChatTurn {
  return {
    key: turn.id,
    id: turn.id,
    kind: turn.kind,
    input: turn.input,
    chunks: turn.output ? [turn.output] : [],
    status: turn.status,
    turn,
    error: turn.error?.message ?? null,
    events: null,
    starts: [],
    live,
    sentAt: turn.started_at,
    quotaAfter: null,
  }
}

const EVENT_FETCHES = 4

type Options = {
  /** A streamed turn finished (completed or failed), with its reply text. */
  onTurnDone?: (turnId: string, output: string, failed: boolean) => void
  onQuota?: (usage: Usage, spentTokens: number, costUsd: number) => void
}

/** Conversation state for one workspace: history with each turn's events, and live turns. */
export function useConversation(workspaceId: string, options: Options = {}) {
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [nextBefore, setNextBefore] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const opts = useRef(options)
  useEffect(() => {
    opts.current = options
  })

  const patch = useCallback((match: (t: ChatTurn) => boolean, change: (t: ChatTurn) => Partial<ChatTurn>) => {
    setTurns((current) => current.map((t) => (match(t) ? { ...t, ...change(t) } : t)))
  }, [])

  /** Load stored events for turns that don't have them, a few at a time. */
  const loadEvents = useCallback(
    async (ids: string[]) => {
      const queue = [...ids]
      async function worker() {
        for (let id = queue.shift(); id !== undefined; id = queue.shift()) {
          const turnId = id
          try {
            const stored = await getTurnEvents(turnId)
            const events = stored.map((e) => ({ seq: e.seq, event: e.event }))
            patch((t) => t.id === turnId, () => ({ events }))
          } catch {
            patch((t) => t.id === turnId && t.events === null, () => ({ events: [] }))
          }
        }
      }
      await Promise.all(Array.from({ length: Math.min(EVENT_FETCHES, queue.length) }, worker))
    },
    [patch],
  )

  // History loads once per workspace (the parent remounts on workspace change).
  useEffect(() => {
    let cancelled = false
    listTurns(workspaceId)
      .then((page) => {
        if (cancelled) return
        const loaded = page.items.map((t) => fromTurn(t)).reverse()
        // Merge, don't replace: a message sent before history arrived must survive.
        setTurns((current) => {
          const local = new Set(current.map((t) => t.id).filter((id): id is string => id !== null))
          return [...loaded.filter((t) => !local.has(t.id ?? '')), ...current]
        })
        setNextBefore(page.next_before ?? null)
        void loadEvents(page.items.map((t) => t.id))
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : 'Could not load history.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [workspaceId, loadEvents])

  const loadEarlier = useCallback(async () => {
    if (!nextBefore) return
    const page = await listTurns(workspaceId, nextBefore)
    setTurns((current) => [...page.items.map((t) => fromTurn(t)).reverse(), ...current])
    setNextBefore(page.next_before ?? null)
    void loadEvents(page.items.map((t) => t.id))
  }, [workspaceId, nextBefore, loadEvents])

  const send = useCallback(
    async (message: string) => {
      const key = `local-${String(Date.now())}`
      const mine = (t: ChatTurn) => t.key === key
      const now = new Date().toISOString()
      setTurns((current) => [
        ...current,
        {
          key,
          id: null,
          kind: 'user',
          input: message,
          chunks: [],
          status: 'streaming',
          turn: null,
          error: null,
          events: [],
          starts: [],
          live: true,
          sentAt: now,
          quotaAfter: null,
        },
      ])
      setSending(true)
      try {
        for await (const frame of streamTurn(workspaceId, message)) {
          switch (frame.event) {
            case 'turn.started':
              patch(mine, () => ({ id: frame.data.turn_id, sentAt: frame.data.started_at }))
              break
            case 'token':
              patch(mine, (t) => ({ chunks: [...t.chunks, frame.data.text] }))
              break
            case 'step.started':
              patch(mine, (t) => ({ starts: [...t.starts, { step: frame.data.step, at: frame.data.at }] }))
              break
            case 'turn.event':
              patch(mine, (t) => ({ events: [...(t.events ?? []), { seq: frame.data.seq, event: frame.data.event }] }))
              break
            case 'turn.completed':
            case 'turn.failed': {
              const { turn, quota } = frame.data
              const failed = frame.event === 'turn.failed'
              const output = turn.output ?? ''
              patch(mine, (t) => ({
                status: failed ? 'failed' : 'completed',
                turn,
                // Keep the streamed chunks when they add up to the reply: no jump at the end.
                chunks: failed || t.chunks.join('') === output ? t.chunks : [output],
                error: failed ? (turn.error?.message ?? 'This turn failed.') : null,
                quotaAfter: quota ?? null,
              }))
              opts.current.onTurnDone?.(turn.id, output, failed)
              if (quota) {
                const u = turn.usage
                opts.current.onQuota?.(quota, u.input_tokens + u.cached_input_tokens + u.output_tokens, u.cost_usd)
              }
              break
            }
          }
        }
      } catch (err) {
        const text = err instanceof ApiError || err instanceof Error ? err.message : 'Something went wrong.'
        patch(mine, () => ({ status: 'failed', error: text }))
      } finally {
        setSending(false)
      }
    },
    [workspaceId, patch],
  )

  /** A finished turn made elsewhere (an undo or a confirmation): shown, with its Trail played. */
  const addTurn = useCallback(
    (turn: Turn) => {
      setTurns((current) => (current.some((t) => t.id === turn.id) ? current : [...current, fromTurn(turn, true)]))
      void loadEvents([turn.id])
    },
    [loadEvents],
  )

  return { turns, loading, loadError, sending, send, addTurn, loadEarlier, hasEarlier: nextBefore !== null }
}
