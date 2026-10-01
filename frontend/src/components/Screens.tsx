import { RotateCcw } from 'lucide-react'
import { motion } from 'motion/react'
import { useState, type ReactNode, type SubmitEvent } from 'react'
import { BrandMark, Button, TextField } from '../ui'
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

/** The email and the access code, or just the code when `codeOnly` (a guest): the form itself. */
export function AccessForm({
  onSubmit,
  codeOnly = false,
  action = 'Continue',
}: {
  onSubmit: (email: string, accessCode: string) => Promise<string | null>
  codeOnly?: boolean
  action?: string
}) {
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: SubmitEvent) {
    e.preventDefault()
    setBusy(true)
    setProblem(await onSubmit(email.trim(), code))
    setBusy(false)
  }

  return (
    <form onSubmit={(e) => void submit(e)} className="grid w-full max-w-72 gap-3 text-left" data-testid="access-gate">
      {!codeOnly && (
        <TextField label="Email" type="email" autoComplete="email" required value={email} onChange={(e) => { setEmail(e.target.value) }} />
      )}
      <TextField label="Access code" type="password" autoComplete="off" required value={code} onChange={(e) => { setCode(e.target.value) }} />
      {problem && (
        <p className="m-0 text-label text-bad" role="alert" data-testid="access-problem">
          {problem}
        </p>
      )}
      <Button variant="primary" type="submit" disabled={busy || !code || (!codeOnly && !email)}>
        {busy ? 'Checking…' : action}
      </Button>
    </form>
  )
}

/** Production: the way in is an email and the access code, until real accounts arrive. */
export function AccessGate({ onSignIn }: { onSignIn: (email: string, accessCode: string) => Promise<string | null> }) {
  return (
    <Screen>
      <BrandMark size="lg" />
      <div>
        <p className="m-0 font-voice text-title-lg text-fg">This is a private preview.</p>
        <p className="m-0 mt-1 text-label font-normal text-fg-3">Use the access code you were given. Your email keeps your memory apart from everyone else&apos;s.</p>
      </div>
      <AccessForm onSubmit={onSignIn} />
    </Screen>
  )
}
