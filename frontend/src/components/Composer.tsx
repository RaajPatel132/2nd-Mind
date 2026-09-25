import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useEffect, useState, type KeyboardEvent, type RefObject } from 'react'
import { Kbd, SendButton, TextArea, cx } from '../ui'
import { motionProps } from '../ui/motion'

/** Synthetic examples; the placeholder cycles through them while the composer is empty and idle. */
const EXAMPLES = ['Tell me anything…', 'I moved to Pune last week', 'Nisha recommended Severance'] as const
const CYCLE_MS = 5000

type Props = {
  value: string
  onChange: (value: string) => void
  onSend: (message: string) => void
  sending: boolean
  inputRef: RefObject<HTMLTextAreaElement | null>
  docked: boolean
}

/**
 * The floating composer: 16px above the bottom edge over a fade to canvas, as wide as the
 * conversation. Grows to 8 lines, then scrolls. ↵ sends, ⇧↵ adds a line.
 */
export function Composer({ value, onChange, onSend, sending, inputRef, docked }: Props) {
  const [focused, setFocused] = useState(false)
  const [example, setExample] = useState(0)
  const reduce = useReducedMotion()
  const cycling = !value && !focused && !reduce

  useEffect(() => {
    if (!cycling) return
    const timer = window.setInterval(() => {
      setExample((i) => (i + 1) % EXAMPLES.length)
    }, CYCLE_MS)
    return () => {
      window.clearInterval(timer)
    }
  }, [cycling])

  function submit(e?: { preventDefault: () => void }) {
    e?.preventDefault()
    const message = value.trim()
    if (!message || sending) return
    onSend(message)
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  const placeholder = EXAMPLES[cycling ? example : 0]

  return (
    <div
      className={cx(
        'pointer-events-none fixed inset-x-0 bottom-0 z-10 bg-(image:--composer-fade) px-3 pb-3 pt-9 sm:px-6 sm:pb-4 sm:pt-11',
        docked && '2xl:right-115',
      )}
    >
      <form
        onSubmit={submit}
        className={cx(
          'pointer-events-auto mx-auto flex max-w-180 items-end gap-2.5 rounded-lg border bg-surface py-2.5 pl-4 pr-2.5 shadow-edge transition dur-2',
          focused ? 'border-fg-3 shadow-focus-soft' : 'border-line-control',
        )}
        aria-label="Message"
      >
        <label htmlFor="composer" className="sr-only">
          Message
        </label>
        <div className="relative flex min-w-0 flex-1 py-1.5">
          <TextArea
            id="composer"
            ref={inputRef}
            data-testid="composer"
            value={value}
            onChange={(e) => {
              onChange(e.target.value)
            }}
            onKeyDown={onKeyDown}
            onFocus={() => {
              setFocused(true)
            }}
            onBlur={() => {
              setFocused(false)
            }}
            aria-describedby="composer-hints"
            placeholder={placeholder}
            className="font-ui text-body text-fg placeholder:text-transparent"
          />
          {/* The visible placeholder, so a new example can fade in (the native one can't animate). */}
          {!value && (
            <span aria-hidden className="pointer-events-none absolute inset-x-0 top-1.5 truncate text-body text-fg-3">
              <AnimatePresence mode="wait" initial={false}>
                <motion.span key={placeholder} className="block truncate" {...motionProps('fade')}>
                  {placeholder}
                </motion.span>
              </AnimatePresence>
            </span>
          )}
        </div>
        <SendButton sending={sending} disabled={!value.trim()} />
      </form>
      <p
        id="composer-hints"
        className="pointer-events-auto mx-auto mb-0 mt-2 hidden max-w-180 justify-center gap-4 text-mono-sm font-ui text-fg-3 sm:flex"
      >
        <span className="inline-flex items-center gap-1.5">
          <Kbd>↵</Kbd> send
        </span>
        <span className="inline-flex items-center gap-1.5">
          <Kbd>⇧↵</Kbd> new line
        </span>
        <span className="inline-flex items-center gap-1.5">
          <Kbd>F6</Kbd> inspector
        </span>
      </p>
    </div>
  )
}
