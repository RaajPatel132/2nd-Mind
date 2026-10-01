/**
 * The persona's two suggested prompts (FR-13.5, S4.13): one save and one recall, chosen so that the
 * first thirty seconds show a memory diff and a retrieval explanation. They stay until they have
 * been sent; picking one fills the composer.
 */
import { motion } from 'motion/react'
import { CardButton, Overline } from '../ui'
import { motionProps } from '../ui/motion'

type Props = {
  name: string
  save: string
  recall: string
  /** Messages already sent in this memory: a prompt that was sent is not offered again. */
  sent: readonly string[]
  onPick: (text: string) => void
}

export function SuggestedPrompts({ name, save, recall, sent, onPick }: Props) {
  const offered = [
    { text: save, note: 'Saves it, and shows the memory diff', id: 'save' },
    { text: recall, note: `Answers from what ${name}'s memory holds, with where it came from`, id: 'recall' },
  ].filter((p) => !sent.includes(p.text))
  if (offered.length === 0) return null
  return (
    <motion.section {...motionProps('rise')} className="mx-auto mt-10 w-full max-w-180" aria-label="Try these" data-testid="suggested-prompts">
      <Overline>Try these</Overline>
      <ul className="m-0 mt-2 grid list-none gap-3 p-0 sm:grid-cols-2">
        {offered.map((p) => (
          <li key={p.id}>
            <CardButton
              title={p.text}
              note={p.note}
              onClick={() => {
                onPick(p.text)
              }}
              data-testid="suggested-prompt"
              data-prompt={p.id}
            />
          </li>
        ))}
      </ul>
    </motion.section>
  )
}
