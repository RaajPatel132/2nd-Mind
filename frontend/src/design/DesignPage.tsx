/**
 * `/design`: the living reference (docs/design/system.md §12.5). Every token with its hex and
 * contrast, the type scale in the three faces, every primitive in every state, the Trail in
 * every step state on synthetic data, and a replay button for each motion preset.
 */
import { AnimatePresence, motion } from 'motion/react'
import { ArrowRight, Copy, Plus, Settings, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { factsOf, stepViews, type Facts, type StepStart, type StepView, type TrailEvent } from '../trail/model'
import { usePacedSteps } from '../trail/pacing'
import { HeldContext, type HeldActions } from '../trail/context'
import { Trail } from '../trail/Trail'
import {
  BrandMark,
  Button,
  CardButton,
  Chip,
  CountUp,
  DiffRow,
  Disclosure,
  DisclosureTrigger,
  Icon,
  IconButton,
  Kbd,
  Overline,
  Popover,
  QuotaRing,
  SendButton,
  Sheet,
  Skeleton,
  Tag,
  TextArea,
  Tooltip,
  Wordmark,
  cx,
  useToast,
} from '../ui'
import { motionProps, presets, type PresetName } from '../ui/motion'
import { contrast } from './contrast'
import { FAILED_EVENTS, HELD_EVENTS, PICKER, REFUSED_EVENTS, RUNNING_START, SAVE_EVENTS, withSeq } from './fixtures'
import { ModelPicker } from '../components/ModelPicker'

type Swatch = { name: string; bg: string; use: string; text?: boolean }

const SURFACES: Swatch[] = [
  { name: 'canvas', bg: 'bg-canvas', use: 'App background. Never pure black.' },
  { name: 'surface', bg: 'bg-surface', use: 'Raised: composer, step detail, cards, top bar on scroll.' },
  { name: 'surface-2', bg: 'bg-surface-2', use: 'Hover, inputs, chips, the avatar.' },
  { name: 'surface-3', bg: 'bg-surface-3', use: 'Overlays: popovers, sheets, toasts, pressed.' },
  { name: 'line', bg: 'bg-line', use: 'Hairlines and dividers. Decorative.' },
  { name: 'line-strong', bg: 'bg-line-strong', use: 'Secondary outline, ring track, the rail.' },
  { name: 'line-control', bg: 'bg-line-control', use: 'Input and toggle borders (≥ 3 : 1).' },
]
const TEXTS: Swatch[] = [
  { name: 'fg', bg: 'bg-fg', use: 'Primary text, answers, active labels.', text: true },
  { name: 'fg-2', bg: 'bg-fg-2', use: 'Secondary text, finished step labels.', text: true },
  { name: 'fg-3', bg: 'bg-fg-3', use: 'Meta, captions, timings, placeholders.', text: true },
  { name: 'fg-4', bg: 'bg-fg-4', use: 'Disabled and decorative only. Never text to read.' },
  { name: 'inverse', bg: 'bg-inverse', use: 'Primary button fill.' },
  { name: 'on-inverse', bg: 'bg-on-inverse', use: 'Text and icons on inverse.' },
]
const MEANING: Swatch[] = [
  { name: 'ok', bg: 'bg-ok', use: 'Saved, added, allowed, fulfilled.', text: true },
  { name: 'warn', bg: 'bg-warn', use: 'Held for you, a decision, quota < 25%.', text: true },
  { name: 'bad', bg: 'bg-bad', use: 'Refused, removed, failed, quota < 10%.', text: true },
]
const SURFACE_NAMES = ['canvas', 'surface', 'surface-2', 'surface-3']

const TYPE: { token: string; cls: string; spec: string; sample: string }[] = [
  { token: 'display', cls: 'font-voice text-display', spec: 'Voice 44/48 −0.02em', sample: "What's on your mind?" },
  { token: 'title-lg', cls: 'font-voice text-title-lg', spec: 'Voice 28/34 −0.01em', sample: 'Turn 7f3c' },
  { token: 'title', cls: 'font-ui text-title', spec: 'Interface 17/24 600', sample: 'Memory diff' },
  { token: 'prompt', cls: 'font-ui text-prompt', spec: 'Interface 20/30 500', sample: 'I moved to Pune on 12 Sep' },
  { token: 'answer', cls: 'font-ui text-answer', spec: 'Interface 17/28 400', sample: 'Noted. Your home is now Pune from 12 September.' },
  { token: 'body', cls: 'font-ui text-body', spec: 'Interface 15/24 400', sample: 'Tell me anything worth keeping.' },
  { token: 'label', cls: 'font-ui text-label', spec: 'Interface 14/20 500', sample: 'Checking what I already know' },
  { token: 'overline', cls: 'font-ui text-overline uppercase', spec: 'Interface 11/16 +0.12em 600', sample: 'Memory diff' },
  { token: 'mono', cls: 'font-machine text-mono', spec: 'Machine 13/20', sample: 'reconcile@2 · 1,512 → 96 tok' },
  { token: 'mono-sm', cls: 'font-machine text-mono-sm', spec: 'Machine 12/16', sample: '2.14 s · 3,482 tokens · $0.0061' },
]

const SPACE = [
  ['4', 'w-1'],
  ['8', 'w-2'],
  ['12', 'w-3'],
  ['16', 'w-4'],
  ['20', 'w-5'],
  ['24', 'w-6'],
  ['32', 'w-8'],
  ['40', 'w-10'],
  ['56', 'w-14'],
  ['72', 'w-18'],
] as const
const RADII = [
  ['xs · 6', 'rounded-xs'],
  ['sm · 10', 'rounded-sm'],
  ['md · 14', 'rounded-md'],
  ['lg · 20', 'rounded-lg'],
  ['full', 'rounded-full'],
] as const

/** The tokens' values as the browser resolved them from tokens.css (read once). */
function useTokens(names: string[]): Record<string, string> {
  const [values] = useState<Record<string, string>>(() => {
    const style = getComputedStyle(document.documentElement)
    return Object.fromEntries(names.map((n) => [n, style.getPropertyValue(`--color-${n}`).trim().toUpperCase()]))
  })
  return values
}

export default function DesignPage() {
  const toast = useToast()
  const held: HeldActions = useMemo(
    () => ({
      held: new Map(),
      busy: null,
      confirm: () => {
        toast('In the app, Confirm applies the held change as a new turn.')
      },
      reject: () => {
        toast('In the app, Reject discards the held change.')
      },
    }),
    [toast],
  )
  return (
    <HeldContext.Provider value={held}>
      <header className="sticky top-0 z-20 border-b border-line bg-(--bar-fill) backdrop-blur-bar">
        <nav aria-label="Sections" className="mx-auto flex h-14 max-w-290 items-center gap-6 px-4 md:px-8">
          <a href="/" className="inline-flex items-center gap-2.5 text-fg no-underline">
            <BrandMark />
            <Wordmark />
          </a>
          <span className="rounded-full px-2 py-0.5 font-machine text-mono-sm text-fg-3 ring-1 ring-inset ring-line-strong">Ink</span>
          <span className="no-scrollbar ml-auto flex min-w-0 gap-1 overflow-x-auto">
            {['colour', 'type', 'space', 'motion', 'components', 'trail'].map((id) => (
              <a key={id} href={`#${id}`} className="whitespace-nowrap rounded-full px-2.5 py-1.5 text-label text-fg-3 no-underline transition-colors dur-2 hover:bg-surface-2 hover:text-fg">
                {id}
              </a>
            ))}
          </span>
        </nav>
      </header>
      <main id="main" className="mx-auto max-w-290 px-4 pb-30 md:px-8">
        <section className="pt-18">
          <Overline>The design system · living reference</Overline>
          <h1 className="m-0 mt-3 max-w-3xl font-voice text-display text-fg">
            The conversation is the product. <em className="text-fg-2">Everything else is a layer on it.</em>
          </h1>
          <p className="measure m-0 mt-5 text-answer text-fg-2">
            Every token and primitive below comes from the same files the app uses: <code className="font-machine text-mono text-fg">tokens.css</code> and{' '}
            <code className="font-machine text-mono text-fg">src/ui/</code>. If something here looks wrong, the app looks wrong too.
          </p>
        </section>
        <ColourSection />
        <TypeSection />
        <SpaceSection />
        <MotionSection />
        <ComponentsSection />
        <TrailSection />
      </main>
    </HeldContext.Provider>
  )
}

function Section({ id, overline, title, children }: { id: string; overline: string; title: ReactNode; children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-16 pt-24" aria-labelledby={`${id}-title`}>
      <Overline>{overline}</Overline>
      <h2 id={`${id}-title`} className="m-0 mb-8 mt-2 font-voice text-title-lg text-fg">
        {title}
      </h2>
      {children}
    </section>
  )
}

function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx('min-w-0 rounded-md border border-line bg-surface p-5 shadow-edge', className)}>{children}</div>
}

// ------------------------------------------------------------------ colour

function ColourSection() {
  const all = [...SURFACES, ...TEXTS, ...MEANING]
  const hex = useTokens(all.map((s) => s.name))
  const ratio = (a: string, b: string) => (hex[a] && hex[b] ? contrast(hex[a], hex[b]) : null)
  const minOn = (name: string) => {
    const rs = SURFACE_NAMES.map((s) => ratio(name, s)).filter((r): r is number => r !== null)
    return rs.length ? Math.min(...rs) : null
  }
  const describe = (s: Swatch): string => {
    if (s.text) {
      const m = minOn(s.name)
      return m ? `lowest on a surface · ${m.toFixed(1)} : 1` : ''
    }
    if (s.name === 'on-inverse') return `on inverse · ${ratio('on-inverse', 'inverse')?.toFixed(1) ?? '…'} : 1`
    if (s.name === 'line-control') return `on surface-2 · ${ratio('line-control', 'surface-2')?.toFixed(2) ?? '…'} : 1`
    if (SURFACE_NAMES.includes(s.name)) return `fg on it · ${ratio('fg', s.name)?.toFixed(1) ?? '…'} : 1`
    if (s.name === 'fg-4') return `on canvas · ${ratio('fg-4', 'canvas')?.toFixed(1) ?? '…'} : 1 · decorative`
    return 'decorative'
  }
  return (
    <Section id="colour" overline="Colour" title="Ink, metal and three signals">
      {[
        ['Surfaces and lines', SURFACES],
        ['Text', TEXTS],
        ['Meaning (the only hues)', MEANING],
      ].map(([label, list]) => (
        <div key={label as string} className="mt-10 first:mt-0">
          <Overline className="mb-3.5">{label as string}</Overline>
          <ul className="m-0 grid list-none grid-cols-1 gap-3 p-0 sm:grid-cols-2 lg:grid-cols-4" data-testid="swatches">
            {(list as Swatch[]).map((s) => (
              <li key={s.name} className="overflow-hidden rounded-md border border-line bg-surface">
                <div className={cx('flex h-20 items-end border-b border-line px-3.5 pb-3', s.bg)} aria-hidden>
                  <span className={cx('font-voice text-title-lg', s.name === 'inverse' || s.name === 'fg' || s.name === 'fg-2' || s.name === 'ok' || s.name === 'warn' || s.name === 'bad' || s.name === 'fg-3' ? 'text-on-inverse' : 'text-fg')}>Aa</span>
                </div>
                <div className="grid gap-0.5 px-3.5 pb-3.5 pt-3">
                  <p className="m-0 font-machine text-mono text-fg">{s.name}</p>
                  <p className="m-0 font-machine text-mono-sm text-fg-2" data-testid="swatch-hex">
                    {hex[s.name] ?? '…'}
                  </p>
                  <p className="m-0 mt-1 text-label font-normal text-fg-3">{s.use}</p>
                  <p className="m-0 mt-1.5 font-machine text-mono-sm text-fg-2">{describe(s)}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      ))}
      <div className="mt-10 grid overflow-hidden rounded-md border border-line bg-surface sm:grid-cols-2">
        <div className="min-h-36 bg-(image:--gradient-chrome)" aria-hidden />
        <div className="p-5">
          <p className="m-0 font-machine text-mono text-fg">--gradient-chrome</p>
          <p className="m-0 mt-2 text-label font-normal text-fg-2">Brushed metal, the one flourish. Allowed on exactly four things:</p>
          <ol className="m-0 mt-2 pl-5 text-label font-normal text-fg-2">
            <li>the brand mark</li>
            <li>the quota ring's arc</li>
            <li>the primary button's hover sheen</li>
            <li>the running step's shimmer</li>
          </ol>
        </div>
      </div>
    </Section>
  )
}

// ------------------------------------------------------------------ type

function TypeSection() {
  return (
    <Section id="type" overline="Typography" title="Three voices, one job each">
      <div className="grid gap-3 md:grid-cols-3">
        <Card>
          <Overline>Voice · Instrument Serif</Overline>
          <p className="m-0 mt-4 font-voice text-display text-fg">
            Hello, <em className="text-fg-2">again.</em>
          </p>
          <p className="m-0 mt-3 text-label font-normal text-fg-3">The product, at moments that matter.</p>
        </Card>
        <Card>
          <Overline>Interface · Instrument Sans</Overline>
          <p className="m-0 mt-4 text-answer text-fg">Noted. Severance is marked as watched.</p>
          <p className="m-0 mt-3 text-label font-normal text-fg-3">Everything you read and press.</p>
        </Card>
        <Card>
          <Overline>Machine · Geist Mono</Overline>
          <p className="m-0 mt-4 font-machine text-mono text-fg-2">
            intent <b className="font-medium text-fg">save</b> · 0.94
            <br />
            extract@3 · 1,208 → 214 tok
          </p>
          <p className="m-0 mt-3 text-label font-normal text-fg-3">What the system computed for you to inspect.</p>
        </Card>
      </div>
      <div className="mt-3 overflow-hidden rounded-md border border-line" data-testid="type-scale">
        {TYPE.map((row) => (
          <div key={row.token} className="grid gap-2 border-b border-line px-5 py-4 last:border-b-0 type-row sm:items-center sm:gap-5">
            <div>
              <p className="m-0 font-machine text-mono text-fg">{row.token}</p>
              <p className="m-0 font-machine text-mono-sm text-fg-3">{row.spec}</p>
            </div>
            <p className={cx('m-0 min-w-0 break-words text-fg', row.cls)}>{row.sample}</p>
          </div>
        ))}
      </div>
    </Section>
  )
}

// ------------------------------------------------------------------ space

function SpaceSection() {
  return (
    <Section id="space" overline="Space, radius, surfaces" title="A 4px grid, five radii, flat surfaces">
      <div className="grid gap-3 md:grid-cols-3">
        <Card>
          <Overline className="mb-3.5">Space</Overline>
          <div className="grid gap-2">
            {SPACE.map(([n, w]) => (
              <div key={n} className="flex items-center gap-3 font-machine text-mono-sm text-fg-3">
                <span className="w-8 text-right tnum">{n}</span>
                <span className={cx('block h-2.5 rounded-xs bg-fg-3', w)} />
              </div>
            ))}
          </div>
        </Card>
        <Card>
          <Overline className="mb-3.5">Radius</Overline>
          <div className="flex flex-wrap gap-3.5">
            {RADII.map(([label, r]) => (
              <div key={label} className="grid justify-items-center gap-2 font-machine text-mono-sm text-fg-3">
                <span className={cx('block size-14 bg-surface-2 ring-1 ring-inset ring-line-strong', r)} />
                {label}
              </div>
            ))}
          </div>
        </Card>
        <Card>
          <Overline className="mb-3.5">Surfaces</Overline>
          <div className="rounded-md border border-line bg-canvas p-3 font-machine text-mono-sm text-fg-3">
            canvas
            <div className="mt-2 rounded-md border border-line bg-surface p-3 shadow-edge">
              surface · hairline · top edge
              <div className="mt-2 rounded-sm bg-surface-2 p-3">surface-2</div>
              <div className="mt-2 rounded-sm border border-line-strong bg-surface-3 p-3 shadow-overlay">surface-3 · overlay shadow</div>
            </div>
          </div>
        </Card>
      </div>
    </Section>
  )
}

// ------------------------------------------------------------------ motion

function MotionSection() {
  const names = Object.keys(presets) as PresetName[]
  return (
    <Section id="motion" overline="Motion" title="Nothing jumps, nothing fakes work">
      <div className="overflow-hidden rounded-md border border-line" data-testid="motion-presets">
        {names.map((name) => (
          <PresetRow key={name} name={name} />
        ))}
      </div>
      <p className="m-0 mt-3 text-label font-normal text-fg-3">
        Durations: dur-1 120ms · dur-2 200ms · dur-3 320ms · dur-4 480ms · dur-5 900ms. Stagger 40ms, six at most. Reduced motion turns every transform into a
        120ms fade and stops the loops.
      </p>
    </Section>
  )
}

function PresetRow({ name }: { name: PresetName }) {
  const [run, setRun] = useState(0)
  const [shown, setShown] = useState(true)
  const p = presets[name]
  return (
    <div className="grid items-center gap-3 border-b border-line px-5 py-4 last:border-b-0 preset-row sm:gap-5">
      <div>
        <p className="m-0 font-machine text-mono text-fg">{p.name}</p>
        <p className="m-0 font-machine text-mono-sm text-fg-3">{String(Math.round(p.transition.duration * 1000))}ms</p>
      </div>
      <p className="m-0 text-label font-normal text-fg-3">{p.use}</p>
      <div className="flex items-center gap-3">
        <span className="relative grid h-10 w-20 place-items-center rounded-sm bg-canvas ring-1 ring-inset ring-line">
          <AnimatePresence mode="wait">
            {shown && <motion.span key={run} {...motionProps(name)} className="block size-4 rounded-full bg-fg" />}
          </AnimatePresence>
        </span>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => {
            setShown(false)
            window.setTimeout(() => {
              setRun((r) => r + 1)
              setShown(true)
            }, 250)
          }}
          aria-label={`Replay ${p.name}`}
        >
          Replay
        </Button>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ components

function Demo({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <Card className="grid content-start gap-4">
      <div>
        <p className="m-0 text-title text-fg">{title}</p>
        {note && <p className="m-0 mt-1 text-label font-normal text-fg-3">{note}</p>}
      </div>
      {children}
    </Card>
  )
}

function ComponentsSection() {
  const toast = useToast()
  const [sending, setSending] = useState(false)
  const [open, setOpen] = useState(false)
  const [pop, setPop] = useState(false)
  const [sheet, setSheet] = useState(false)
  const [remaining, setRemaining] = useState(0.585)
  const [count, setCount] = useState(3482)
  const [text, setText] = useState('')
  const [delta, setDelta] = useState<{ key: number; text: string } | null>(null)
  const [model, setModel] = useState(PICKER.default)
  const anchor = useRef<HTMLButtonElement>(null)
  return (
    <Section id="components" overline="Components" title="Every primitive, every state">
      <div className="grid gap-3 lg:grid-cols-2">
        <Demo title="Button" note="Primary, secondary, ghost, danger. Pill. 32 / 40 / 48. One primary per view.">
          {(['primary', 'secondary', 'ghost', 'danger'] as const).map((v) => (
            <div key={v} className="flex flex-wrap items-center gap-2.5">
              <Button variant={v} size="sm">
                {v}
              </Button>
              <Button variant={v}>{v}</Button>
              <Button variant={v} size="lg" icon={<Icon icon={ArrowRight} />}>
                With icon
              </Button>
              <Button variant={v} disabled>
                Disabled
              </Button>
            </div>
          ))}
        </Demo>
        <Demo title="IconButton, Kbd, Tooltip" note="Always labelled, always a tooltip (400ms delay).">
          <div className="flex flex-wrap items-center gap-2.5">
            <IconButton icon={Copy} label="Copy" />
            <IconButton icon={Settings} label="Settings" size="sm" />
            <IconButton icon={Trash2} label="Delete (disabled)" disabled />
            <Kbd>↵</Kbd>
            <Kbd>⇧↵</Kbd>
            <Kbd>F6</Kbd>
            <Tooltip label="A tooltip on surface-3">
              {(d) => (
                <Button variant="ghost" size="sm" {...d}>
                  Hover me
                </Button>
              )}
            </Tooltip>
          </div>
        </Demo>
        <Demo title="SendButton" note="Idle, disabled, sending (the chrome arc). Press it: the arrow launches and lands.">
          <div className="flex items-center gap-4">
            <SendButton type="button" sending={false} disabled={false} label="Send (idle)" />
            <SendButton type="button" sending={false} disabled label="Send (disabled)" />
            <SendButton type="button" sending disabled={false} />
            <SendButton
              type="button"
              sending={sending}
              disabled={false}
              label="Send (try it)"
              onClick={() => {
                setSending(true)
                window.setTimeout(() => {
                  setSending(false)
                }, 1400)
              }}
            />
          </div>
        </Demo>
        <Demo title="Chip, Tag, dots" note="Chips in label or Machine type, with an optional status dot. Tags for layers and tiers.">
          <div className="flex flex-wrap items-center gap-2">
            <Chip>My memory</Chip>
            <Chip dot="ok">Saved</Chip>
            <Chip dot="warn">Held</Chip>
            <Chip dot="bad">Refused</Chip>
            <Chip kind="mono">save · 0.94</Chip>
            <Chip kind="mono">+1 ~2</Chip>
            <Tag>Core</Tag>
            <Tag>Quick</Tag>
            <Tag>Archive</Tag>
            <Tag strong>Standard</Tag>
          </div>
        </Demo>
        <Demo title="Disclosure" note="Height through grid rows (dur-3). Closed content is inert.">
          <div>
            <DisclosureTrigger
              open={open}
              controls="demo-disclosure"
              onClick={() => {
                setOpen((o) => !o)
              }}
              className="w-full rounded-sm px-3 py-2 text-left text-label text-fg-2 hover:bg-surface-2"
            >
              {open ? 'Hide' : 'Show'} the detail
            </DisclosureTrigger>
            <Disclosure id="demo-disclosure" open={open}>
              <p className="m-0 px-3 pb-1 pt-2 text-body text-fg">The expand and collapse behind step rows and inspector panels.</p>
            </Disclosure>
          </div>
        </Demo>
        <Demo title="Popover, Toast, Sheet" note="Overlays: surface-3 and the one shadow. Esc closes; focus returns.">
          <div className="relative flex flex-wrap gap-2.5">
            <Button
              ref={anchor}
              aria-expanded={pop}
              onClick={() => {
                setPop((p) => !p)
              }}
            >
              Popover
            </Button>
            <Popover
              open={pop}
              onClose={() => {
                setPop(false)
              }}
              anchor={anchor}
              label="Demo popover"
              className="left-0 top-12 w-64"
            >
              <p className="m-0 text-body text-fg">A popover. Click outside or press Esc.</p>
            </Popover>
            <Button
              onClick={() => {
                toast('Undone. 2 memories restored.', { label: 'Redo', run: () => undefined })
              }}
            >
              Toast
            </Button>
            <Button
              onClick={() => {
                setSheet(true)
              }}
            >
              Sheet
            </Button>
            <Sheet
              open={sheet}
              mode="overlay"
              onClose={() => {
                setSheet(false)
              }}
              label="Demo sheet"
            >
              <div className="p-5">
                <Overline>Sheet</Overline>
                <p className="m-0 mt-2 font-voice text-title-lg text-fg">The inspector's container</p>
                <p className="m-0 mt-3 text-body text-fg-2">Docks at 1440px and up, overlays below that, rises from the bottom under 768px.</p>
                <Button
                  className="mt-5"
                  onClick={() => {
                    setSheet(false)
                  }}
                >
                  Close
                </Button>
              </div>
            </Sheet>
          </div>
        </Demo>
        <Demo title="QuotaRing" note="What's left, like a fuel gauge. Chrome; warn under 25%; bad under 10%.">
          <div className="flex flex-wrap items-center gap-6">
            {[1, 0.585, 0.2, 0.06].map((r) => (
              <QuotaRing key={r} remaining={r} initials="RP" expanded={false} controls="none" onClick={() => undefined} size="lg" />
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2.5">
            <QuotaRing remaining={remaining} initials="RP" delta={delta} expanded={false} controls="none" onClick={() => undefined} />
            <Button
              size="sm"
              onClick={() => {
                setRemaining((r) => Math.max(0, r - 0.12))
                setDelta({ key: Date.now(), text: '−2.9k' })
              }}
            >
              Spend
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setRemaining(0.585)
              }}
            >
              Reset
            </Button>
          </div>
        </Demo>
        <Demo title="CountUp, Skeleton" note="Numbers animate between values (dur-5). Skeletons only for content that is loading.">
          <div className="flex items-center gap-4">
            <CountUp value={count} format={(n) => Math.round(n).toLocaleString('en-US')} className="font-voice text-display text-fg" />
            <Button
              size="sm"
              icon={<Icon icon={Plus} />}
              onClick={() => {
                setCount((c) => c + 1200)
              }}
            >
              Add
            </Button>
          </div>
          <div className="grid gap-2">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-6 w-3/4" />
          </div>
        </Demo>
        <Demo title="DiffRow" note="Op glyphs in Machine type. Superseded rows strike through in fg-3, never red.">
          <ul className="m-0 grid list-none gap-2 p-0">
            <DiffRow glyph="+" layer="quick" title="Watched Severance" note="episode" />
            <DiffRow glyph="~" layer="quick" title="Watch Severance" note="wanted → fulfilled" />
            <DiffRow glyph="~" layer="core" title="Lives in Bengaluru" note="superseded" strike />
            <DiffRow glyph="−" layer="archive" title="Old gym timetable" note="removed" strike />
            <DiffRow glyph="⏸" layer="core" title="Prefers aisle seats" note="held · P-CORE-1" />
            <DiffRow glyph="∅" layer="quick" title="a secret (not shown)" note="not written · P-SECRET-1" />
          </ul>
        </Demo>
        <Demo
          title="Select (the model picker)"
          note="A pill that opens a grouped listbox. ↑ ↓ move, ↵ picks, Esc closes. Here: a fake stand-in (Haiku) and a provider with no key (OpenAI, disabled)."
        >
          <div className="flex min-h-12 justify-end">
            <ModelPicker picker={PICKER} value={model} onChange={setModel} />
          </div>
        </Demo>
        <Demo title="TextArea, CardButton, brand" note="The composer's input grows to 8 lines, then scrolls.">
          <div className="flex items-end gap-2.5 rounded-lg border border-line-control bg-surface px-4 py-2.5">
            <TextArea
              value={text}
              onChange={(e) => {
                setText(e.target.value)
              }}
              aria-label="Demo text area"
              placeholder="Type a few lines…"
              className="font-ui text-body text-fg placeholder:text-fg-3"
            />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <CardButton title="I live in Bengaluru" note="Saves a fact about you" />
            <CardButton title="I moved to Pune" note="Replaces where you live" />
          </div>
          <div className="flex items-center gap-4">
            <BrandMark size="xl" />
            <BrandMark size="lg" />
            <BrandMark />
            <Wordmark />
          </div>
        </Demo>
      </div>
    </Section>
  )
}

// ------------------------------------------------------------------ the Trail

function TrailDemo({ title, note, events, starts = [], folded = false }: { title: string; note: string; events: TrailEvent[]; starts?: StepStart[]; folded?: boolean }) {
  const [isFolded, setFolded] = useState(folded)
  const views = useMemo(() => stepViews(events, starts), [events, starts])
  const facts = useMemo(() => factsOf(events), [events])
  return (
    <div className="min-w-0 rounded-md border border-line bg-canvas px-4 pb-2.5 pt-3.5" data-testid="trail-demo">
      <Overline>{title}</Overline>
      <p className="m-0 mt-1 text-label font-normal text-fg-3">{note}</p>
      <Trail
        steps={views}
        facts={facts}
        turn={null}
        timezone="Asia/Kolkata"
        live={false}
        folded={isFolded}
        onFold={setFolded}
        summary="9 steps · 2.84 s · saved 2, updated 1"
      />
    </div>
  )
}

function TrailSection() {
  const save = useMemo(() => withSeq(SAVE_EVENTS), [])
  const running = useMemo(() => withSeq(SAVE_EVENTS.slice(0, 8)), [])
  const [replay, setReplay] = useState(0)
  return (
    <Section id="trail" overline="The Trail" title="The agent's work, step by step">
      <div className="grid gap-3 lg:grid-cols-2">
        <TrailDemo title="Done" note="Filled dots and past-tense results. Click a row for both layers." events={save} />
        <TrailDemo
          title="Running"
          note="The node pulses, chrome sweeps the label, the timer counts (static under reduced motion)."
          events={running}
          starts={[...stepViews(running, []).map((v) => ({ step: v.step, at: v.startedAt })), ...RUNNING_START]}
        />
        <TrailDemo title="Held" note="Amber. Opens by itself with Confirm and Reject." events={withSeq(HELD_EVENTS)} />
        <TrailDemo title="Refused" note="Red. Opens by itself and says why." events={withSeq(REFUSED_EVENTS)} />
        <TrailDemo title="Failed" note="A red ring. Says what went wrong and what to do." events={withSeq(FAILED_EVENTS)} />
        <TrailDemo title="Folded" note="An older turn folds into one summary row, which expands back." events={save} folded />
      </div>
      <div className="mt-3">
        <Card>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="m-0 text-title text-fg">A live turn, replayed</p>
              <p className="m-0 mt-1 text-label font-normal text-fg-3">The same rows at the server's pace, with the 240ms dwell and the 400ms cap.</p>
            </div>
            <Button
              variant="secondary"
              onClick={() => {
                setReplay((r) => r + 1)
              }}
            >
              Replay
            </Button>
          </div>
          {replay > 0 ? (
            <LiveReplay key={replay} />
          ) : (
            <p className="m-0 mt-4 text-label font-normal text-fg-3">Press Replay to stream a save turn.</p>
          )}
        </Card>
      </div>
    </Section>
  )
}

/** Feeds the save fixture in as a stream would: a start, then the step's events when it ends. */
function LiveReplay() {
  const [events, setEvents] = useState<TrailEvent[]>([])
  const [starts, setStarts] = useState<StepStart[]>([])
  useEffect(() => {
    const all = withSeq(SAVE_EVENTS)
    const timers: number[] = []
    let clock = 200
    let pending: TrailEvent[] = []
    for (const e of all) {
      pending.push(e)
      if (e.event.type !== 'step') continue
      const stepEvent = e.event
      const batch = pending
      pending = []
      timers.push(
        window.setTimeout(() => {
          setStarts((s) => [...s, { step: stepEvent.step, at: stepEvent.started_at }])
        }, clock),
      )
      clock += Math.min(stepEvent.latency_ms, 900)
      timers.push(
        window.setTimeout(() => {
          setEvents((ev) => [...ev, ...batch])
        }, clock),
      )
      clock += 40
    }
    return () => {
      timers.forEach((t) => {
        window.clearTimeout(t)
      })
    }
  }, [])
  const views = stepViews(events, starts)
  const facts = factsOf(events)
  return <LiveTrail views={views} facts={facts} />
}

function LiveTrail({ views, facts }: { views: StepView[]; facts: Facts }) {
  const [folded, setFolded] = useState(false)
  const shown = usePacedSteps(views, true)
  return <Trail steps={shown} facts={facts} turn={null} timezone="Asia/Kolkata" live folded={folded} onFold={setFolded} summary="" />
}
