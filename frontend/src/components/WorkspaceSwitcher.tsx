/**
 * The workspace switcher (S4.11, PRD §10): My memory and Sample persona for a signed-in person;
 * Sample persona and Scratch for a guest. The sample is a personal copy of Aditi Rao's memory,
 * made the first time it is opened; "Start the sample over" replaces it with a fresh one.
 */
import { useState } from 'react'
import type { Me } from '../api/client'
import { Select, useToast, type SelectGroup } from '../ui'

type Workspace = Me['workspaces'][number]

const NAMES: Record<string, string> = {
  private: 'My memory',
  guest: 'My memory',
  persona_copy: 'Sample persona',
  scratch: 'Scratch',
}

const NOTES: Record<string, string> = {
  persona_copy: "Aditi Rao's memory, made for you",
  scratch: 'An empty memory of your own',
}

type Props = {
  workspaces: Workspace[]
  activeId: string
  /** A guest has no email: Sample persona and Scratch, never My memory. */
  guest: boolean
  onSwitch: (workspaceId: string) => void
  /** Open the sample (made on first use), or start it over. Resolves to why it failed, or null. */
  onSample: (reset: boolean) => Promise<string | null>
  onScratch?: () => Promise<string | null>
}

export function WorkspaceSwitcher({ workspaces, activeId, guest, onSwitch, onSample, onScratch }: Props) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const listed = workspaces.filter((w) => w.kind in NAMES)
  const active = listed.find((w) => w.id === activeId)
  const hasSample = listed.some((w) => w.kind === 'persona_copy')
  const hasScratch = listed.some((w) => w.kind === 'scratch')

  const mine: SelectGroup = {
    label: 'Open',
    options: [
      ...listed.map((w) => ({ value: w.id, label: NAMES[w.kind] ?? 'Memory', note: NOTES[w.kind] })),
      ...(hasSample ? [] : [{ value: 'sample:open', label: 'Sample persona', note: "Aditi Rao's memory, made for you" }]),
      ...(guest && !hasScratch && onScratch ? [{ value: 'scratch:open', label: 'Scratch', note: NOTES.scratch }] : []),
    ],
  }
  const groups: SelectGroup[] = [mine]
  if (hasSample) groups.push({ label: 'Sample', options: [{ value: 'sample:reset', label: 'Start the sample over', note: 'A fresh copy, moved to today' }] })

  async function pick(value: string): Promise<void> {
    if (value === activeId) return
    if (value.includes(':')) {
      setBusy(true)
      const problem =
        value === 'scratch:open' && onScratch ? await onScratch() : await onSample(value === 'sample:reset')
      setBusy(false)
      if (problem) toast(problem)
      return
    }
    onSwitch(value)
  }

  return (
    <Select
      label="Memory"
      triggerLabel={`Memory: ${active ? (NAMES[active.kind] ?? 'Memory') : 'Memory'}`}
      value={activeId}
      groups={groups}
      align="left"
      onChange={(value) => {
        void pick(value)
      }}
      className={busy ? 'opacity-60' : undefined}
    >
      {busy ? 'Opening…' : active ? (NAMES[active.kind] ?? 'Memory') : 'Memory'}
    </Select>
  )
}
