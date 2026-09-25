import { motion } from 'motion/react'
import { BrandMark, CardButton } from '../ui'
import { motionProps } from '../ui/motion'

/** What works in this build on every provider mode: two saves and one supersede. */
const SUGGESTIONS = [
  { text: 'I live in Bengaluru', note: 'Saves a fact about you' },
  { text: "I'm vegetarian", note: 'Saves a preference' },
  { text: 'I moved to Pune', note: 'Replaces where you live, keeps the old one as history' },
] as const

/** An empty workspace: the mark, the greeting, three suggestions. Picking one fills the composer. */
export function FirstRun({ onPick }: { onPick: (text: string) => void }) {
  return (
    <section className="flex flex-col items-center pt-16 sm:pt-24 text-center" aria-labelledby="first-run-title" data-testid="first-run">
      <motion.div {...motionProps('rise')}>
        <BrandMark size="xl" />
      </motion.div>
      <motion.h1 id="first-run-title" {...motionProps('rise', 1)} className="m-0 mt-6 font-voice text-display text-fg">
        What's on your mind?
      </motion.h1>
      <motion.p {...motionProps('rise', 2)} className="m-0 mt-3 max-w-md text-body text-fg-2">
        Tell me anything worth keeping. You'll see each step I take, and you can undo any of it.
      </motion.p>
      <ul className="m-0 mt-10 grid w-full list-none gap-3 p-0 sm:grid-cols-3">
        {SUGGESTIONS.map((s, i) => (
          <motion.li key={s.text} {...motionProps('rise', i + 3)}>
            <CardButton
              title={s.text}
              note={s.note}
              onClick={() => {
                onPick(s.text)
              }}
              data-testid="suggestion"
            />
          </motion.li>
        ))}
      </ul>
    </section>
  )
}
