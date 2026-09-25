import { Cpu } from 'lucide-react'
import type { ModelChoice, Picker } from '../api/client'
import { formatWeight, weightNote } from '../lib/format'
import { Icon, Select, Tag, type SelectGroup } from '../ui'

type Props = {
  picker: Picker
  value: string
  onChange: (id: string) => void
}

function groupsOf(picker: Picker): SelectGroup[] {
  // Tag stand-ins row by row only when some models are real; if all are, the footer says so.
  const mixed = picker.choices.some((c) => c.simulated) && picker.choices.some((c) => !c.simulated)
  const groups: SelectGroup[] = []
  for (const c of picker.choices) {
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
      note: c.available ? weightNote(c.weight, c.id === picker.baseline) : 'No API key for this provider',
      trailing: <span className="font-machine text-mono-sm text-fg-2 tnum">{formatWeight(c.weight)}</span>,
      disabled: !c.available,
    })
  }
  return groups
}

/**
 * The model for new turns, in the top bar where the provider-mode tag was. Each model shows how
 * fast it uses the quota against the baseline; "fake" marks one the fake provider stands in for.
 */
export function ModelPicker({ picker, value, onChange }: Props) {
  const current: ModelChoice | undefined = picker.choices.find((c) => c.id === value)
  const simulated = picker.choices.some((c) => c.simulated)
  return (
    <div data-testid="model-picker">
      <Select
        label="Model"
        value={value}
        groups={groupsOf(picker)}
        onChange={onChange}
        triggerLabel={current ? `Model: ${current.label}, ${formatWeight(current.weight)} quota` : 'Model'}
        footer={
          <>
            Quota is counted in {picker.baseline_label} tokens: a model at 2× uses it twice as fast.
            {simulated && ' Fake: no API key, so a stand-in replies, charged as the model would be.'}
          </>
        }
      >
        <Icon icon={Cpu} className="shrink-0 text-fg-3 sm:hidden" />
        <span className="hidden truncate sm:inline" data-testid="model-picker-label">
          {current?.label ?? value}
        </span>
        {current && <span className="font-machine text-mono-sm text-fg-3 tnum">{formatWeight(current.weight)}</span>}
        {current?.simulated && <Tag className="hidden sm:inline-block">fake</Tag>}
      </Select>
    </div>
  )
}
