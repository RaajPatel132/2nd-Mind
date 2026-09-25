import type { ReactNode } from 'react'

type PanelProps = {
  n: number
  title: string
  /** One line shown while the panel is collapsed (and as the body when there is nothing else). */
  reason?: string
  open?: boolean
  testId?: string
  children?: ReactNode
}

export function Panel(props: PanelProps) {
  return (
    <details open={props.open} className="group rounded-lg border border-slate-200 bg-white" data-testid={props.testId}>
      <summary className="flex cursor-pointer list-none items-baseline gap-2 rounded-lg px-3 py-2.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500">
        <span className="text-xs tabular-nums text-slate-500">{props.n}</span>
        <span className="shrink-0 whitespace-nowrap text-sm font-semibold text-slate-900">{props.title}</span>
        {props.reason && !props.open && <span className="min-w-0 truncate text-xs text-slate-600">— {props.reason}</span>}
        <span aria-hidden className="ml-auto text-xs text-slate-500 group-open:rotate-90">▸</span>
      </summary>
      <div className="border-t border-slate-100 px-3 py-3">
        {props.children ?? <p className="text-sm text-slate-600">{props.reason}</p>}
      </div>
    </details>
  )
}

export function Stat({ label, value, wide, testId }: { label: string; value: string; wide?: boolean; testId?: string }) {
  return (
    <div className={wide ? 'col-span-2' : undefined}>
      <dt className="text-slate-500">{label}</dt>
      <dd className="tabular-nums text-slate-900" data-testid={testId}>
        {value}
      </dd>
    </div>
  )
}

/** A small heading inside a panel. */
export function Subhead({ children }: { children: ReactNode }) {
  return <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-600">{children}</h3>
}

export function Chip({ children, tone = 'slate' }: { children: ReactNode; tone?: 'slate' | 'green' | 'amber' | 'red' | 'indigo' }) {
  const tones = {
    slate: 'bg-slate-100 text-slate-800',
    green: 'bg-emerald-50 text-emerald-900 ring-emerald-200',
    amber: 'bg-amber-50 text-amber-900 ring-amber-200',
    red: 'bg-red-50 text-red-900 ring-red-200',
    indigo: 'bg-indigo-50 text-indigo-900 ring-indigo-200',
  }
  return <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs ring-1 ring-inset ring-transparent ${tones[tone]}`}>{children}</span>
}
