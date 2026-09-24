import { useCallback, useEffect, useState } from 'react'
import { ApiError, listTurns, streamTurn, type Turn } from '../api/client'

export type ChatTurn = {
  key: string
  id: string | null
  input: string
  output: string
  status: 'streaming' | 'completed' | 'failed' | 'running'
  turn: Turn | null
  error: string | null
}

function fromTurn(turn: Turn): ChatTurn {
  return {
    key: turn.id,
    id: turn.id,
    input: turn.input,
    output: turn.output ?? '',
    status: turn.status,
    turn,
    error: turn.error?.message ?? null,
  }
}

/**
 * Conversation state for one workspace. `onTurnDone` fires when a streamed turn ends;
 * `onHistory` fires once with the newest stored turn so the glass box can show it.
 */
export function useConversation(
  workspaceId: string,
  onTurnDone: (turnId: string) => void,
  onHistory: (latestTurnId: string) => void,
) {
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [nextBefore, setNextBefore] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)

  // History loads once per workspace (the parent remounts on workspace change).
  useEffect(() => {
    let cancelled = false
    listTurns(workspaceId)
      .then((page) => {
        if (cancelled) return
        const loaded = page.items.map(fromTurn).reverse()
        // Merge, don't replace: a message sent before history arrived must survive, and its
        // live (streaming) copy wins over the stored one.
        setTurns((current) => {
          const local = new Set(current.map((t) => t.id).filter((id): id is string => id !== null))
          return [...loaded.filter((t) => !local.has(t.id ?? '')), ...current]
        })
        setNextBefore(page.next_before ?? null)
        const latest = page.items[0]
        if (latest) onHistory(latest.id)
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
  }, [workspaceId, onHistory])

  const loadEarlier = useCallback(async () => {
    if (!nextBefore) return
    const page = await listTurns(workspaceId, nextBefore)
    setTurns((current) => [...page.items.map(fromTurn).reverse(), ...current])
    setNextBefore(page.next_before ?? null)
  }, [workspaceId, nextBefore])

  const send = useCallback(
    async (message: string) => {
      const key = `local-${String(Date.now())}`
      const update = (patch: Partial<ChatTurn>) => {
        setTurns((current) => current.map((t) => (t.key === key ? { ...t, ...patch } : t)))
      }
      setTurns((current) => [
        ...current,
        { key, id: null, input: message, output: '', status: 'streaming', turn: null, error: null },
      ])
      setSending(true)
      let output = ''
      try {
        for await (const frame of streamTurn(workspaceId, message)) {
          switch (frame.event) {
            case 'turn.started':
              update({ id: frame.data.turn_id })
              break
            case 'token':
              output += frame.data.text
              update({ output })
              break
            case 'turn.completed':
              update({ status: 'completed', turn: frame.data.turn, output: frame.data.turn.output ?? output })
              onTurnDone(frame.data.turn_id)
              break
            case 'turn.failed':
              update({ status: 'failed', turn: frame.data.turn, error: frame.data.error.message })
              onTurnDone(frame.data.turn_id)
              break
          }
        }
      } catch (err) {
        const text = err instanceof ApiError || err instanceof Error ? err.message : 'Something went wrong.'
        update({ status: 'failed', error: text })
      } finally {
        setSending(false)
      }
    },
    [workspaceId, onTurnDone],
  )

  return { turns, loading, loadError, sending, send, loadEarlier, hasEarlier: nextBefore !== null }
}
