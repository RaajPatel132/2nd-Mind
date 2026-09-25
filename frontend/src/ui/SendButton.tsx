import { AnimatePresence, motion } from 'motion/react'
import { ArrowUp } from 'lucide-react'
import { useState } from 'react'
import { cx } from './cx'
import { ease, t } from './motion'

type Props = {
  sending: boolean
  disabled: boolean
  onClick?: () => void
  type?: 'submit' | 'button'
  label?: string
}

/**
 * 36px, inverse. On send the arrow lifts out and a new one rises in (slight overshoot); while
 * sending a chrome arc spins around it. Disabled still reads as a button.
 */
export function SendButton({ sending, disabled, onClick, type = 'submit', label = 'Send' }: Props) {
  // A new arrow per send, so the old one can leave upward while the next rises in.
  const [launches, setLaunches] = useState(0)
  const [wasSending, setWasSending] = useState(sending)
  if (sending !== wasSending) {
    setWasSending(sending)
    if (sending) setLaunches((n) => n + 1)
  }

  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled || sending}
      aria-label={sending ? 'Sending' : label}
      data-state={sending ? 'sending' : disabled ? 'disabled' : 'idle'}
      data-testid="send"
      className={cx(
        'relative grid size-9 shrink-0 cursor-pointer place-items-center overflow-visible rounded-full',
        'transition dur-1 ease-out active:scale-90 motion-reduce:active:scale-100',
        sending && 'send-arc bg-surface-3 text-fg-3',
        !sending && disabled && 'cursor-default bg-surface-3 text-fg-3 ring-1 ring-inset ring-line-strong',
        !sending && !disabled && 'bg-inverse text-on-inverse',
      )}
    >
      <span className="relative block size-5 overflow-hidden" aria-hidden>
        <AnimatePresence initial={false}>
          {!sending && (
            <motion.span
              key={launches}
              className="absolute inset-0 grid place-items-center"
              initial={{ y: '140%', opacity: 0 }}
              animate={{ y: 0, opacity: 1, transition: t.land }}
              exit={{ y: '-140%', opacity: 0, transition: { duration: t.fade.duration * 0.8, ease: ease.in } }}
            >
              <ArrowUp size={20} strokeWidth={1.5} />
            </motion.span>
          )}
        </AnimatePresence>
      </span>
    </button>
  )
}
