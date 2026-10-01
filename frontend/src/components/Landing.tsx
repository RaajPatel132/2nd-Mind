/**
 * The landing page (S4.13, FR-15.1, FR-21.1): for anyone without a session. The one line that says
 * what it is, three numbers that say it works, and where to click. The numbers are the recorded
 * runs' as measured, each linking to `/evals`; a number with no run says "no run yet".
 */
import { motion } from 'motion/react'
import { useState } from 'react'
import type { Meta } from '../api/client'
import type { HeadlineMetric, HeadlineRun } from '../api/headline'
import { useHeadline } from '../hooks/useHeadline'
import { BrandMark, Button, Overline, Wordmark } from '../ui'
import { motionProps } from '../ui/motion'
import { AccessForm } from './Screens'

type Props = {
  meta: Meta
  /** Open the sample persona as a guest; why it failed, or null. */
  onTry: (accessCode?: string) => Promise<string | null>
  /** Sign in with an email and the access code (production). */
  onSignIn: (email: string, accessCode: string) => Promise<string | null>
  /** Dev only: sign in with no code. */
  onDevSignIn: () => Promise<string | null>
}

type Panel = 'home' | 'try' | 'sign-in'

function runOf(runs: HeadlineRun[], id: string | null): HeadlineRun | undefined {
  return id ? runs.find((r) => r.id === id) : undefined
}

function Stat({ metric, run }: { metric: HeadlineMetric; run?: HeadlineRun }) {
  return (
    <a
      href="/evals"
      className="group block min-w-0 rounded-md border border-line bg-surface px-4 py-3.5 text-left no-underline shadow-edge transition-colors dur-2 hover:bg-surface-2 focus-visible:outline-offset-2"
      data-testid="headline-stat"
      data-metric={metric.id}
      aria-label={`${metric.label}: ${metric.display}. See the run behind it.`}
    >
      <Overline>{metric.label}</Overline>
      <p className="m-0 mt-1.5 font-voice text-title-lg text-fg tnum" data-testid="headline-value">
        {metric.display}
      </p>
      <p className="m-0 mt-1 font-machine text-mono-sm text-fg-3" data-testid="headline-run">
        {run ? `${run.date} · ${run.sha}` : 'no run yet'}
      </p>
    </a>
  )
}

export function Landing({ meta, onTry, onSignIn, onDevSignIn }: Props) {
  const [panel, setPanel] = useState<Panel>('home')
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const numbers = useHeadline()
  // Until the site opens to guests, making one asks for the access code too.
  const guestNeedsCode = meta.access_code_required && !meta.guests_open

  async function run(action: () => Promise<string | null>): Promise<void> {
    setBusy(true)
    setProblem(null)
    setProblem(await action())
    setBusy(false)
  }

  function tryIt(): void {
    if (guestNeedsCode) setPanel('try')
    else void run(() => onTry())
  }

  function signIn(): void {
    if (meta.access_code_required) setPanel('sign-in')
    else void run(onDevSignIn)
  }

  const stats =
    numbers.status === 'ready'
      ? numbers.headline.metrics
      : (['Retrieval hit rate', 'Relative-date accuracy', 'p95 time to answer a question'] as const).map(
          (label): HeadlineMetric => ({ id: label, label, value: null, display: numbers.status === 'loading' ? '…' : 'no run yet', detail: '', run: null }),
        )
  const runs = numbers.status === 'ready' ? numbers.headline.runs : []

  return (
    <main id="main" className="mx-auto flex min-h-dvh max-w-180 flex-col px-4 pb-16 pt-6 md:px-6" data-testid="landing">
      <header className="flex items-center justify-between gap-3">
        <span className="inline-flex items-center gap-2.5 text-fg">
          <BrandMark />
          <Wordmark />
        </span>
        <a href="/evals" className="rounded-full px-3 py-1 text-label text-fg-2 no-underline transition-colors dur-1 hover:text-fg">
          How it's measured
        </a>
      </header>

      <div className="mt-16 sm:mt-24">
        <motion.h1 {...motionProps('rise')} className="m-0 max-w-140 text-balance font-voice text-display text-fg" data-testid="landing-line">
          A personal memory you can talk to.
        </motion.h1>
        <motion.p {...motionProps('rise', 1)} className="m-0 mt-5 measure text-pretty text-body text-fg-2">
          Tell it anything and it decides what the thing is, when it matters, who it concerns and where to keep it. Ask for anything back by meaning, time, person or category.
          Every decision it makes can be inspected and corrected.
        </motion.p>
      </div>

      <motion.ul {...motionProps('rise', 2)} className="m-0 mt-10 grid list-none grid-cols-1 gap-3 p-0 sm:grid-cols-3" aria-label="How well it works, as measured">
        {stats.map((m) => (
          <li key={m.id} className="min-w-0">
            <Stat metric={m} run={runOf(runs, m.run)} />
          </li>
        ))}
      </motion.ul>

      <motion.div {...motionProps('rise', 3)} className="mt-10">
        {panel === 'home' && (
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="primary" size="lg" onClick={tryIt} disabled={busy || meta.persona == null} data-testid="try-persona">
              {busy ? 'Opening…' : 'Try the sample persona'}
            </Button>
            <Button variant="secondary" size="lg" onClick={signIn} disabled={busy} data-testid="sign-in">
              Sign in
            </Button>
          </div>
        )}
        {panel === 'home' && meta.persona != null && (
          <p className="m-0 mt-3 max-w-md text-label font-normal text-fg-3">
            Open {meta.persona.name}'s memory, made just for you. No sign-up, and nothing you do there reaches anyone else.
          </p>
        )}
        {panel === 'home' && meta.persona == null && (
          <p className="m-0 mt-3 max-w-md text-label font-normal text-fg-3">The sample persona isn't available on this server yet.</p>
        )}
        {panel === 'try' && (
          <div className="grid gap-3">
            <p className="m-0 max-w-md text-label font-normal text-fg-3">This is a private preview. Use the access code you were given to open the sample.</p>
            <AccessForm codeOnly action="Open the sample" onSubmit={(_email, code) => onTry(code)} />
            <Button variant="ghost" size="sm" className="justify-self-start" onClick={() => { setPanel('home') }}>
              Back
            </Button>
          </div>
        )}
        {panel === 'sign-in' && (
          <div className="grid gap-3">
            <p className="m-0 max-w-md text-label font-normal text-fg-3">Use the access code you were given. Your email keeps your memory apart from everyone else's.</p>
            <AccessForm onSubmit={onSignIn} />
            <Button variant="ghost" size="sm" className="justify-self-start" onClick={() => { setPanel('home') }}>
              Back
            </Button>
          </div>
        )}
        {problem && (
          <p className="m-0 mt-3 text-label text-bad" role="alert" data-testid="landing-problem">
            {problem}
          </p>
        )}
      </motion.div>
    </main>
  )
}
