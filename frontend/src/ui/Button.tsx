import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { cx } from './cx'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
export type ButtonSize = 'sm' | 'md' | 'lg'

const VARIANTS: Record<ButtonVariant, string> = {
  primary: 'sheen bg-inverse text-on-inverse',
  secondary: 'bg-transparent text-fg ring-1 ring-inset ring-line-strong hover:bg-surface-2 hover:ring-fg-3',
  ghost: 'bg-transparent text-fg-2 hover:bg-surface-2 hover:text-fg',
  danger: 'bg-transparent text-bad ring-1 ring-inset ring-bad/40 hover:bg-bad-tint',
}

const SIZES: Record<ButtonSize, string> = {
  sm: 'h-8 px-3 text-label',
  md: 'h-10 px-4 text-label',
  lg: 'h-12 px-6 text-label',
}

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant
  size?: ButtonSize
  icon?: ReactNode
}

/** Pill button. Hover lifts 1px; press scales to 0.97. One primary per view. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', icon, className, children, type = 'button', ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cx(
        'inline-flex shrink-0 cursor-pointer items-center justify-center gap-2 whitespace-nowrap rounded-full font-ui',
        'transition dur-2 ease-out hover:-translate-y-px active:scale-97 active:dur-1',
        'disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:translate-y-0',
        'motion-reduce:hover:translate-y-0 motion-reduce:active:scale-100',
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    >
      {icon}
      {children}
    </button>
  )
})
