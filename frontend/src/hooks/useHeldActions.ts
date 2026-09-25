import { useCallback, useEffect, useMemo, useState } from 'react'
import { confirmHeldWrite, listHeldWrites, rejectHeldWrite, undoTurn, type HeldWrite, type Turn } from '../api/client'
import type { HeldActions } from '../trail/context'

/** Held-write statuses and the undo / confirm / reject actions, shared by the Trail and Inspector. */
export function useHeldActions(workspaceId: string, anyHeld: boolean, onTurnCreated: (turn: Turn) => void) {
  const [held, setHeld] = useState<Map<string, HeldWrite>>(new Map())
  const [version, setVersion] = useState(0)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!anyHeld) return
    let cancelled = false
    listHeldWrites(workspaceId)
      .then((items) => {
        if (!cancelled) setHeld(new Map(items.map((h) => [h.id, h])))
      })
      .catch(() => {
        // Without statuses every held entry still offers Confirm / Reject; the server decides.
      })
    return () => {
      cancelled = true
    }
  }, [workspaceId, anyHeld, version])

  const run = useCallback(async (id: string, action: () => Promise<void>) => {
    setBusy(id)
    setError(null)
    try {
      await action()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'That did not work.')
    } finally {
      setBusy(null)
      setVersion((v) => v + 1)
    }
  }, [])

  const actions: HeldActions = useMemo(
    () => ({
      held,
      busy,
      confirm: (heldId: string) => {
        void run(heldId, async () => {
          onTurnCreated(await confirmHeldWrite(heldId))
        })
      },
      reject: (heldId: string) => {
        void run(heldId, async () => {
          await rejectHeldWrite(heldId)
        })
      },
    }),
    [held, busy, run, onTurnCreated],
  )

  const undo = useCallback(
    (turnId: string) =>
      run(turnId, async () => {
        onTurnCreated(await undoTurn(turnId))
      }),
    [run, onTurnCreated],
  )

  return { actions, undo, busy, error }
}
