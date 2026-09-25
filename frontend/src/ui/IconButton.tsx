import { forwardRef, type ButtonHTMLAttributes } from 'react'
import type { LucideIcon } from 'lucide-react'
import { cx } from './cx'
import { Icon } from './Icon'
import { Tooltip } from './Tooltip'

type Props = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> & {
  icon: LucideIcon
  /** Required: the accessible name and the tooltip. */
  label: string
  size?: 'sm' | 'md'
  tooltipSide?: 'top' | 'bottom'
}

/** A circle, 32 or 40. Always labelled, always with a tooltip. */
export const IconButton = forwardRef<HTMLButtonElement, Props>(function IconButton(
  { icon, label, size = 'md', tooltipSide = 'top', className, type = 'button', ...rest },
  ref,
) {
  return (
    <Tooltip label={label} side={tooltipSide}>
      {(describedBy) => (
        <button
          ref={ref}
          type={type}
          aria-label={label}
          {...describedBy}
          className={cx(
            'inline-flex shrink-0 cursor-pointer items-center justify-center rounded-full text-fg-2',
            'transition dur-2 ease-out hover:bg-surface-2 hover:text-fg active:scale-97 active:dur-1',
            'disabled:cursor-not-allowed disabled:opacity-40 motion-reduce:active:scale-100',
            size === 'sm' ? 'size-8' : 'size-10',
            className,
          )}
          {...rest}
        >
          <Icon icon={icon} size={size === 'sm' ? 16 : 20} />
        </button>
      )}
    </Tooltip>
  )
})
