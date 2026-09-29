import { Cpu } from 'lucide-react'
import type { ModelChoice, Picker } from '../api/client'
import { AUTO } from '../hooks/useModelPick'
import { formatMoney, formatRelativePrice, priceNote } from '../lib/format'
import { Icon, Select, Tag, type SelectGroup } from '../ui'

type Props = {
  picker: Picker
  /** The models this person's plan offers besides Auto. */
  offered: ModelChoice[]
  /** The pick, or null for Auto. */
  value: string | null
  onChange: (id: string) => void
}

function groupsOf(picker: Picker, offered: ModelChoice[]): SelectGroup[] {
  // Tag stand-ins row by row only when some models are real; if all are, the footer says so.
  const mixed = offered.some((c) => c.simulated) && offered.some((c) => !c.simulated)
  const groups: SelectGroup[] = [
    {
      label: 'Recommended',
      options: [
        {
          value: AUTO,
          label: picker.auto_label,
          note: picker.auto_note,
          trailing: <span className="font-machine text-mono-sm text-fg-2 tnum">~{formatMoney(picker.auto_usd_per_turn)}</span>,
        },
      ],
    },
  ]
  for (const c of offered) {
    let group = groups.find((g) => g.label === c.provider_label)
    if (!group) {
      group = { label: c.provider_label, options: [] }
      groups.push(group)
    }
    group.options.push({
      value: c.id,
      label: (
        <span className="inline-flex items-center gap-2">
          {c.label}
          {mixed && c.simulated && <Tag>fake</Tag>}
        </span>
      ),
      note: priceNote(c.relative_price),
      trailing: <span className="font-machine text-mono-sm text-fg-2 tnum">{formatRelativePrice(c.relative_price)}</span>,
    })
  }
  return groups
}

/**
 * The model for new turns, in the top bar where the provider-mode tag was. Auto is first and
 * the default: each step uses the model that suits it. The other models show their price for a
 * typical turn against Auto's; "fake" marks one the fake provider stands in for.
 */
export function ModelPicker({ picker, offered, value, onChange }: Props) {
  const current = offered.find((c) => c.id === value)
  const simulated = offered.some((c) => c.simulated)
  return (
    <div data-testid="model-picker">
      <Select
        label="Model"
        value={value ?? AUTO}
        groups={groupsOf(picker, offered)}
        onChange={onChange}
        triggerLabel={current ? `Model: ${current.label}, ${formatRelativePrice(current.relative_price)} Auto's price` : `Model: ${picker.auto_label}`}
        footer={
          <>
            Prices are for a typical recall turn, against Auto&apos;s (about {formatMoney(picker.auto_usd_per_turn)}).
            {simulated && ' Fake: no API key, so a stand-in replies, priced as the model would be.'}
          </>
        }
      >
        <Icon icon={Cpu} className="shrink-0 text-fg-3 sm:hidden" />
        <span className="hidden truncate sm:inline" data-testid="model-picker-label">
          {current?.label ?? picker.auto_label}
        </span>
        {current && <span className="font-machine text-mono-sm text-fg-3 tnum">{formatRelativePrice(current.relative_price)}</span>}
        {current?.simulated && <Tag className="hidden sm:inline-block">fake</Tag>}
      </Select>
    </div>
  )
}
