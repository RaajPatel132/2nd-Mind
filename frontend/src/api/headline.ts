/** The landing page's numbers (S4.13): `headline.json`, generated from the committed live runs. */

export type HeadlineMetric = {
  id: string
  label: string
  value: number | null
  /** What to show: the number as measured, or "no run yet". */
  display: string
  detail: string
  run: string | null
}

export type HeadlineRun = {
  id: string
  suite: string
  date: string
  sha: string
  cases: number
  passed: number
  models: string[]
}

export type Headline = { schema_version: number; metrics: HeadlineMetric[]; runs: HeadlineRun[] }

/** The numbers, or null when they can't be read (the page then says so, and never invents one). */
export async function getHeadline(): Promise<Headline | null> {
  try {
    const response = await fetch('/headline.json', { credentials: 'same-origin' })
    if (!response.ok) return null
    return (await response.json()) as Headline
  } catch {
    return null
  }
}
