import { RotateCcw } from 'lucide-react'
import { motion } from 'motion/react'
import type { ReactNode } from 'react'
import { BrandMark, Button } from '../ui'
import { motionProps } from '../ui/motion'

function Screen({ children }: { children: ReactNode }) {
  return (
    <main id="main" className="grid min-h-dvh place-items-center px-6 text-center">
      <motion.div {...motionProps('rise')} className="flex flex-col items-center gap-5">
        {children}
      </motion.div>
    </main>
  )
}

/** While the session loads: the mark and one line. */
export function Connecting() {
  return (
    <Screen>
      <BrandMark size="lg" />
      <p className="m-0 text-body text-fg-2" role="status">
        Connecting to your memory…
      </p>
    </Screen>
  )
}

/** The server couldn't be reached or refused the session: what happened, and a retry. */
export function BootError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <Screen>
      <BrandMark size="lg" />
      <div>
        <p className="m-0 text-body text-fg" role="alert">
          {message}
        </p>
        <p className="m-0 mt-1 text-label font-normal text-fg-3">Check that the server is running, then try again.</p>
      </div>
      <Button variant="primary" icon={<RotateCcw aria-hidden size={16} strokeWidth={1.5} />} onClick={onRetry}>
        Try again
      </Button>
    </Screen>
  )
}

/** After Sign out: nothing is loaded until you sign back in. */
export function SignedOut({ onSignIn }: { onSignIn: () => void }) {
  return (
    <Screen>
      <BrandMark size="lg" />
      <p className="m-0 font-voice text-title-lg text-fg">You're signed out.</p>
      <Button variant="primary" onClick={onSignIn}>
        Sign in again
      </Button>
    </Screen>
  )
}
