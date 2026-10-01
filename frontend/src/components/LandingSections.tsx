/**
 * What sits under the landing page's call to action: the three things the product does, each with
 * the sample persona's own prompt as its example, and the footer that points at the code, the
 * write-ups and the runs. Plain text, so it reads the same without scripts.
 */
import { motion } from 'motion/react'
import type { PersonaInfo } from '../api/client'
import { SOURCE_LINKS } from '../lib/links'
import { motionProps } from '../ui/motion'

type Beat = { id: string; name: string; example: string | null; text: string }

function beatsOf(persona: PersonaInfo | null): Beat[] {
  return [
    {
      id: 'catch',
      name: 'Catch it.',
      example: persona?.prompts.save ?? null,
      text: 'It works out what this is, who it is about and when it matters, shows the memory diff, and can undo it.',
    },
    {
      id: 'find',
      name: 'Find it.',
      example: persona?.prompts.recall ?? null,
      text: 'It searches by meaning, person and time together, and puts a citation on every claim it makes.',
    },
    {
      id: 'prove',
      name: 'Prove it.',
      example: 'Why did you say that?',
      text: 'Open any answer to see each step, the memories it used and what it cost. The numbers above come from recorded runs.',
    },
  ]
}

export function LandingBeats({ persona }: { persona: PersonaInfo | null }) {
  return (
    <motion.section {...motionProps('rise', 4)} className="mt-20" aria-labelledby="landing-beats" data-testid="landing-beats">
      <h2 id="landing-beats" className="sr-only">
        What it does
      </h2>
      <ol className="m-0 grid list-none gap-10 p-0 sm:grid-cols-3 sm:gap-6">
        {beatsOf(persona).map((beat) => (
          <li key={beat.id} className="min-w-0" data-beat={beat.id}>
            <p className="m-0 font-voice text-title-lg text-fg">{beat.name}</p>
            {beat.example && <p className="m-0 mt-3 text-pretty text-label font-normal text-fg-3">“{beat.example}”</p>}
            <p className="m-0 mt-3 text-pretty text-body text-fg-2">{beat.text}</p>
          </li>
        ))}
      </ol>
    </motion.section>
  )
}

export function LandingFooter() {
  const link = 'rounded-sm text-fg-3 no-underline transition-colors dur-1 hover:text-fg'
  return (
    <footer className="mt-20 border-t border-line pt-6" data-testid="landing-footer">
      <nav aria-label="Source and write-ups" className="flex flex-wrap gap-x-6 gap-y-2 text-label">
        {SOURCE_LINKS.map((l) => (
          <a key={l.id} href={l.href} className={link} target="_blank" rel="noreferrer" data-link={l.id}>
            {l.label}
          </a>
        ))}
        <a href="/evals" className={link} data-link="measured">
          How it's measured
        </a>
      </nav>
    </footer>
  )
}
