import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { cx } from './cx'

type Props = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'title'> & { title: ReactNode; note?: ReactNode }

/** A raised card you can press (suggestions): surface, hairline, top light edge; lifts 1px. */
export function CardButton({ title, note, className, type = 'button', ...rest }: Props) {
  return (
    <button
      type={type}
      className={cx(
        'h-full w-full cursor-pointer rounded-md border border-line bg-surface p-4 text-left shadow-edge',
        'transition dur-2 ease-out hover:-translate-y-px hover:border-line-strong hover:bg-surface-2 active:scale-99 motion-reduce:hover:translate-y-0',
        className,
      )}
      {...rest}
    >
      <span className="block text-label text-fg">{title}</span>
      {note && <span className="mt-1 block font-ui text-mono-sm text-fg-3">{note}</span>}
    </button>
  )
}
