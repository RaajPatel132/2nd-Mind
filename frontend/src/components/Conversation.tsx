import { AnimatePresence, motion } from 'motion/react'
import { ArrowDown } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { ChatTurn } from '../hooks/useConversation'
import { Button, Skeleton } from '../ui'
import { motionProps } from '../ui/motion'
import { FirstRun } from './FirstRun'
import { TurnView } from './TurnView'

type Props = {
  turns: ChatTurn[]
  loading: boolean
  loadError: string | null
  hasEarlier: boolean
  timezone: string
  busy: string | null
  onLoadEarlier: () => Promise<void>
  onInspect: (turnId: string) => void
  onUndo: (turnId: string) => Promise<void>
  onPick: (text: string) => void
}

const NEAR_BOTTOM_PX = 48

function nearBottom(): boolean {
  const doc = document.documentElement
  return doc.scrollHeight - window.scrollY - window.innerHeight < NEAR_BOTTOM_PX
}

/**
 * The conversation column (max 720px). Follows new content only while you're at the bottom;
 * scroll up and a "Jump to latest" chip appears. Loading earlier turns keeps your place.
 */
export function Conversation(props: Props) {
  const { turns, loading, loadError, hasEarlier, timezone, busy } = props
  const following = useRef(true)
  const [showJump, setShowJump] = useState(false)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [collapsedLatest, setCollapsedLatest] = useState(false)
  const latest = turns.at(-1)
  const latestKey = latest?.key

  // A new turn folds every older Trail back into its summary row.
  const [foldedFor, setFoldedFor] = useState(latestKey)
  if (foldedFor !== latestKey) {
    setFoldedFor(latestKey)
    setExpanded(new Set())
    setCollapsedLatest(false)
  }

  useEffect(() => {
    const onScroll = () => {
      if (nearBottom()) {
        following.current = true
        setShowJump(false)
      }
    }
    const onIntent = (e: WheelEvent | TouchEvent | KeyboardEvent) => {
      if (e instanceof WheelEvent && e.deltaY >= 0) return
      if (e instanceof KeyboardEvent && !['ArrowUp', 'PageUp', 'Home'].includes(e.key)) return
      requestAnimationFrame(() => {
        if (!nearBottom()) {
          following.current = false
          setShowJump(true)
        }
      })
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('wheel', onIntent, { passive: true })
    window.addEventListener('touchmove', onIntent, { passive: true })
    window.addEventListener('keydown', onIntent)
    return () => {
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('wheel', onIntent)
      window.removeEventListener('touchmove', onIntent)
      window.removeEventListener('keydown', onIntent)
    }
  }, [])

  // Follow growth (new rows, streamed chunks) while following.
  const column = useRef<HTMLOListElement>(null)
  useEffect(() => {
    const el = column.current
    if (!el) return
    const observer = new ResizeObserver(() => {
      if (following.current) window.scrollTo({ top: document.documentElement.scrollHeight })
    })
    observer.observe(el)
    return () => {
      observer.disconnect()
    }
  }, [])

  // A turn you send always brings you back down.
  useEffect(() => {
    if (latest?.live && latest.status === 'streaming') {
      following.current = true
      window.scrollTo({ top: document.documentElement.scrollHeight })
    }
  }, [latest?.key, latest?.live, latest?.status])

  // Keep the reader's place when earlier turns are added above.
  const anchor = useRef<{ height: number; top: number } | null>(null)
  const loadEarlier = useCallback(async () => {
    anchor.current = { height: document.documentElement.scrollHeight, top: window.scrollY }
    following.current = false
    await props.onLoadEarlier()
  }, [props])
  useLayoutEffect(() => {
    const a = anchor.current
    if (!a) return
    anchor.current = null
    window.scrollTo({ top: a.top + document.documentElement.scrollHeight - a.height })
  }, [turns.length])

  const jump = () => {
    following.current = true
    setShowJump(false)
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' })
  }

  return (
    <>
      <div className="mx-auto w-full max-w-180">
        {hasEarlier && (
          <div className="mb-10 flex justify-center">
            <Button variant="ghost" size="sm" onClick={() => void loadEarlier()}>
              Load earlier messages
            </Button>
          </div>
        )}
        {loadError && (
          <p role="alert" className="m-0 mb-6 border-l-2 border-bad pl-4 text-label text-fg">
            {loadError} Refresh to try again.
          </p>
        )}
        {loading && turns.length === 0 && (
          <div className="grid gap-3" aria-label="Loading your conversation">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-7 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        )}
        {!loading && !loadError && turns.length === 0 && <FirstRun onPick={props.onPick} />}
        <ol ref={column} className="m-0 flex list-none flex-col gap-14 p-0" data-testid="messages" aria-label="Conversation">
          {turns.map((turn) => {
            const isLatest = turn.key === latestKey
            const folded = isLatest ? collapsedLatest : !expanded.has(turn.key)
            return (
              <li key={turn.key}>
                <TurnView
                  turn={turn}
                  timezone={timezone}
                  folded={folded}
                  onFold={(fold) => {
                    if (isLatest) setCollapsedLatest(fold)
                    else
                      setExpanded((s) => {
                        const next = new Set(s)
                        if (fold) next.delete(turn.key)
                        else next.add(turn.key)
                        return next
                      })
                  }}
                  onInspect={props.onInspect}
                  onUndo={props.onUndo}
                  busy={busy !== null}
                />
              </li>
            )
          })}
        </ol>
      </div>
      <AnimatePresence>
        {showJump && (
          <motion.div {...motionProps('toast')} className="fixed inset-x-0 bottom-30 z-20 flex justify-center sm:bottom-34">
            <Button variant="secondary" size="sm" className="bg-surface-3 shadow-overlay" icon={<ArrowDown aria-hidden size={16} strokeWidth={1.5} />} onClick={jump}>
              Jump to latest
            </Button>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}
