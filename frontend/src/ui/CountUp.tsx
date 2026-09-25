import { animate, useReducedMotion } from 'motion/react'
import { useEffect, useRef, useState } from 'react'
import { t } from './motion'

type Props = { value: number; format?: (n: number) => string; className?: string }

/** A number that animates between values (dur-5, tabular). Jumps under reduced motion. */
export function CountUp({ value, format = (n) => String(Math.round(n)), className }: Props) {
  const reduce = useReducedMotion()
  const [shown, setShown] = useState(value)
  const from = useRef(value)

  useEffect(() => {
    if (reduce) {
      from.current = value
      return
    }
    const controls = animate(from.current, value, {
      ...t.meter,
      onUpdate: (v) => { setShown(v) },
    })
    from.current = value
    return () => { controls.stop() }
  }, [value, reduce])

  return <span className={`tnum ${className ?? ''}`}>{format(reduce ? value : shown)}</span>
}
