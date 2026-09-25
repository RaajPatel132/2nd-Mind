import { AnimatePresence, motion } from 'motion/react'
import { Check, ChevronDown } from 'lucide-react'
import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { cx } from './cx'
import { Icon } from './Icon'
import { motionProps } from './motion'
import { Overline } from './Overline'

export type SelectOption = {
  value: string
  label: ReactNode
  /** A plain-words line under the label. */
  note?: ReactNode
  /** Right-aligned, usually a Machine-type fact. */
  trailing?: ReactNode
  disabled?: boolean
}

export type SelectGroup = { label: string; options: SelectOption[] }

type Props = {
  /** The accessible name of the control, e.g. "Model". */
  label: string
  value: string
  groups: SelectGroup[]
  onChange: (value: string) => void
  /** What the closed control shows; a chevron follows it. */
  children: ReactNode
  /** Extra words under the list (surface-3, fg-3). */
  footer?: ReactNode
  /** The side the list lines up with. */
  align?: 'left' | 'right'
  className?: string
  /** Full accessible name of the trigger when its visible content is abbreviated. */
  triggerLabel?: string
}

/**
 * A pill that opens a grouped listbox on surface-3 (the `pop` preset). ↑ ↓ Home End move,
 * Enter or Space picks, Esc or Tab closes; focus returns to the pill.
 */
export function Select({ label, value, groups, onChange, children, footer, align = 'right', className, triggerLabel }: Props) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(value)
  const trigger = useRef<HTMLButtonElement>(null)
  const list = useRef<HTMLDivElement>(null)
  const panel = useRef<HTMLDivElement>(null)
  const base = useId()
  const options = groups.flatMap((g) => g.options)
  const enabled = options.filter((o) => !o.disabled)
  const optionId = (v: string) => `${base}-o-${String(options.findIndex((o) => o.value === v))}`

  useEffect(() => {
    if (!open) return
    list.current?.focus({ preventScroll: true })
    function onPointer(e: PointerEvent) {
      const target = e.target as Node
      if (panel.current?.contains(target) || trigger.current?.contains(target)) return
      setOpen(false)
    }
    document.addEventListener('pointerdown', onPointer)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
    }
  }, [open])

  // Keep the active option in view as the keyboard moves through a long list.
  useEffect(() => {
    if (open) document.getElementById(optionId(active))?.scrollIntoView({ block: 'nearest' })
  })

  function show(next: boolean) {
    if (next) setActive(value)
    setOpen(next)
  }

  function close() {
    setOpen(false)
    trigger.current?.focus()
  }

  function pick(v: string) {
    if (options.find((o) => o.value === v)?.disabled) return
    onChange(v)
    close()
  }

  function move(e: KeyboardEvent<HTMLDivElement>) {
    const i = enabled.findIndex((o) => o.value === active)
    const to = (n: number) => {
      const next = enabled[n]
      if (next) setActive(next.value)
    }
    switch (e.key) {
      case 'ArrowDown':
        to(Math.min(enabled.length - 1, i + 1))
        break
      case 'ArrowUp':
        to(Math.max(0, i - 1))
        break
      case 'Home':
        to(0)
        break
      case 'End':
        to(enabled.length - 1)
        break
      case 'Enter':
      case ' ':
        pick(active)
        break
      case 'Escape':
        e.stopPropagation()
        close()
        break
      case 'Tab':
        setOpen(false)
        return
      default:
        return
    }
    e.preventDefault()
  }

  return (
    <div className={cx('relative', className)}>
      <button
        ref={trigger}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? `${base}-list` : undefined}
        aria-label={triggerLabel}
        onClick={() => {
          show(!open)
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault()
            show(true)
          }
        }}
        className={cx(
          'inline-flex h-8 max-w-full cursor-pointer items-center gap-1.5 whitespace-nowrap rounded-full pl-3 pr-2 font-ui text-label text-fg-2',
          'ring-1 ring-inset ring-line-strong transition-colors dur-2 ease-out hover:bg-surface-2 hover:text-fg',
          open && 'bg-surface-2 text-fg',
        )}
      >
        {children}
        <Icon icon={ChevronDown} className={cx('shrink-0 text-fg-3 transition-transform dur-2 ease-out', open && 'rotate-180')} />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            ref={panel}
            {...motionProps('pop')}
            className={cx(
              'z-40 rounded-md border border-line-strong bg-surface-3 p-1.5 shadow-overlay',
              // On a phone the list spans the screen under the bar; from sm it hangs off the pill.
              'max-sm:fixed max-sm:inset-x-3 max-sm:top-14 sm:absolute sm:top-10 sm:w-80',
              align === 'right' ? 'sm:right-0 sm:origin-top-right' : 'sm:left-0 sm:origin-top-left',
            )}
          >
            <div
              ref={list}
              id={`${base}-list`}
              role="listbox"
              aria-label={label}
              tabIndex={-1}
              aria-activedescendant={optionId(active)}
              onKeyDown={move}
              className="max-h-112 overflow-y-auto outline-none"
            >
              {groups.map((group, g) => (
                <div key={group.label} role="group" aria-labelledby={`${base}-g-${String(g)}`} className={cx(g > 0 && 'mt-1.5 border-t border-line pt-1.5')}>
                  <Overline id={`${base}-g-${String(g)}`} className="block px-2.5 pb-1 pt-1.5">
                    {group.label}
                  </Overline>
                  {group.options.map((o) => {
                    const selected = o.value === value
                    return (
                      <div
                        key={o.value}
                        id={optionId(o.value)}
                        role="option"
                        aria-selected={selected}
                        aria-disabled={o.disabled || undefined}
                        onPointerMove={() => {
                          if (!o.disabled) setActive(o.value)
                        }}
                        onClick={() => {
                          pick(o.value)
                        }}
                        className={cx(
                          'flex min-h-11 items-center gap-2.5 rounded-sm px-2.5 py-1.5 transition-colors dur-1 ease-out',
                          o.disabled ? 'cursor-not-allowed opacity-40' : 'cursor-pointer',
                          o.value === active && !o.disabled && 'bg-surface-2',
                        )}
                      >
                        <span className={cx('grid size-4 shrink-0 place-items-center text-fg', !selected && 'invisible')}>
                          <Icon icon={Check} />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className={cx('block truncate text-label', selected ? 'text-fg' : 'text-fg-2')}>{o.label}</span>
                          {o.note && <span className="block text-label font-normal text-fg-3">{o.note}</span>}
                        </span>
                        {o.trailing && <span className="shrink-0">{o.trailing}</span>}
                      </div>
                    )
                  })}
                </div>
              ))}
            </div>
            {footer && <div className="mt-1.5 border-t border-line px-2.5 pb-1 pt-2.5 text-label font-normal text-fg-3">{footer}</div>}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
