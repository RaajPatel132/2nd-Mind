import { forwardRef, useImperativeHandle, useLayoutEffect, useRef, type TextareaHTMLAttributes } from 'react'
import { cx } from './cx'

const LINE_PX = 24
const MAX_LINES = 8

type Props = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'rows'> & { value: string }

/**
 * A textarea that grows with its text to 8 lines, then scrolls. The growth animates through
 * the wrapper's grid row (grid-template-rows is the one height property we animate).
 */
export const TextArea = forwardRef<HTMLTextAreaElement, Props>(function TextArea({ value, className, ...rest }, ref) {
  const area = useRef<HTMLTextAreaElement>(null)
  const mirror = useRef<HTMLDivElement>(null)
  const wrap = useRef<HTMLDivElement>(null)
  useImperativeHandle(ref, () => area.current as HTMLTextAreaElement)

  useLayoutEffect(() => {
    if (!mirror.current || !wrap.current) return
    mirror.current.textContent = `${value}\u200b`
    const lines = Math.min(MAX_LINES, Math.max(1, Math.round(mirror.current.offsetHeight / LINE_PX)))
    wrap.current.style.gridTemplateRows = `${String(lines * LINE_PX)}px`
  }, [value])

  return (
    <div ref={wrap} className="relative grid min-w-0 flex-1 grow-rows">
      <div
        ref={mirror}
        aria-hidden
        className={cx('pointer-events-none invisible absolute inset-x-0 top-0 whitespace-pre-wrap break-words', className)}
      />
      <textarea
        ref={area}
        value={value}
        rows={1}
        className={cx('scroll-thin h-full min-h-0 w-full resize-none overflow-y-auto border-0 bg-transparent p-0 outline-none', className)}
        {...rest}
      />
    </div>
  )
})
