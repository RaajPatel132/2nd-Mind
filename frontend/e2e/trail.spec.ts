import { expect, test, type Locator } from '@playwright/test'
import { freshUser, send } from './helpers.ts'

async function rows(turn: Locator) {
  return turn.getByTestId('trail-step').evaluateAll((els) =>
    els.map((el) => ({
      step: el.getAttribute('data-step'),
      state: el.getAttribute('data-state'),
      label: el.querySelector('[data-testid="step-label"]')?.textContent ?? '',
    })),
  )
}

// UI.8: the Trail shows each step as the server reports it, and a reload looks the same.
test('a save shows its steps in order while running, and the same steps after a reload', async ({ page }, testInfo) => {
  await freshUser(page, testInfo)
  // Record every row as it appears and every state it passes through.
  await page.evaluate(() => {
    const seen: { step: string; state: string }[] = []
    ;(window as unknown as { __trail: typeof seen }).__trail = seen
    const note = (el: Element) => {
      const step = el.getAttribute('data-step')
      const state = el.getAttribute('data-state')
      if (step && state && !seen.some((s) => s.step === step && s.state === state)) seen.push({ step, state })
    }
    new MutationObserver((records) => {
      for (const r of records) {
        if (r.type === 'attributes' && r.target instanceof Element && r.target.matches('[data-testid="trail-step"]')) note(r.target)
        r.addedNodes.forEach((n) => {
          if (n instanceof Element) n.querySelectorAll('[data-testid="trail-step"]').forEach(note)
          if (n instanceof Element && n.matches('[data-testid="trail-step"]')) note(n)
        })
      }
    }).observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['data-state'] })
  })

  const turn = await send(page, 'I live in Bengaluru', 'Bengaluru')
  const final = await rows(turn)
  expect(final.map((r) => r.step)).toEqual(['understand', 'extract', 'entities', 'reconcile', 'guard', 'save', 'enrich', 'answer'])
  expect(final.every((r) => r.state === 'done')).toBe(true)

  // Each step entered running (in order, one at a time) before it showed its result.
  const seen = await page.evaluate(() => (window as unknown as { __trail: { step: string; state: string }[] }).__trail)
  const firstRunning = seen.filter((s) => s.state === 'running').map((s) => s.step)
  expect(firstRunning).toEqual(final.map((r) => r.step))
  for (const { step } of final) {
    const running = seen.findIndex((s) => s.step === step && s.state === 'running')
    const done = seen.findIndex((s) => s.step === step && s.state === 'done')
    expect(running).toBeGreaterThanOrEqual(0)
    expect(done).toBeGreaterThan(running)
  }

  // Reload: the stored events draw the same rows, labels and states.
  await page.reload()
  const reloaded = page.getByTestId('turn').last()
  await expect(reloaded.getByTestId('trail-step')).toHaveCount(final.length)
  expect(await rows(reloaded)).toEqual(final)
})

test('the previous trail folds into its summary row when a new turn starts, and expands back', async ({ page }, testInfo) => {
  await freshUser(page, testInfo)
  await send(page, 'I live in Bengaluru', 'Bengaluru')
  await send(page, "I'm vegetarian")
  const first = page.getByTestId('turn').first()
  await expect(first.getByTestId('trail')).toHaveAttribute('data-folded', 'true')
  const summary = first.getByTestId('trail-summary')
  await expect(summary).toContainText(/8 steps · .* · saved \d/)
  await summary.click()
  await expect(first.getByTestId('trail')).toHaveAttribute('data-folded', 'false')
  await expect(first.locator('button[data-step-row]').first()).toBeFocused()
  // ↑ ↓ move between rows.
  await page.keyboard.press('ArrowDown')
  await expect(first.locator('button[data-step-row]').nth(1)).toBeFocused()
})
