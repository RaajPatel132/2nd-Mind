import { AxeBuilder } from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { freshUser, inspect, send } from './helpers.ts'

async function serious(page: Page, name: string) {
  // Let entrances finish: axe reads colours, and a card mid-fade has less contrast than at rest.
  await expect.poll(() => page.evaluate(() => document.getAnimations().filter((a) => a.playState === 'running' && a.effect?.getComputedTiming().iterations !== Infinity).length)).toBe(0)
  await page.waitForTimeout(400)
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
  const bad = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
  expect(bad.map((v) => `${name}: ${v.id} (${v.impact}) ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([])
}

// UI.13 / UI.4: axe finds nothing serious or critical on the main screen in each state, or on /design.
test('the main screen: empty, mid-turn, finished, inspector open', async ({ page }, testInfo) => {
  await freshUser(page, testInfo)
  await expect(page.getByTestId('first-run')).toBeVisible()
  await serious(page, 'empty')

  // Mid-turn: hold the stream open after the first steps (the fake provider is otherwise instant).
  await page.route('**/v1/workspaces/*/turns', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    const now = new Date().toISOString()
    const body = [
      `event: turn.started\ndata: ${JSON.stringify({ turn_id: '00000000-0000-7000-8000-000000000001', workspace_id: '00000000-0000-7000-8000-000000000002', started_at: now })}\n\n`,
      `event: step.started\ndata: ${JSON.stringify({ step: 'understand', at: now })}\n\n`,
    ].join('')
    await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body })
  })
  await page.getByTestId('composer').fill('I live in Bengaluru')
  await page.getByTestId('composer').press('Enter')
  await expect(page.locator('[data-testid="trail-step"][data-state="running"]')).toBeVisible()
  await serious(page, 'mid-turn')
  await page.unroute('**/v1/workspaces/*/turns')

  await page.reload()
  const turn = await send(page, "I'm vegetarian")
  await serious(page, 'finished')
  await inspect(page, turn)
  await serious(page, 'inspector open')
})

test('/design', async ({ page }) => {
  await page.goto('/design')
  await expect(page.getByTestId('swatches').first()).toBeVisible()
  await expect(page.getByTestId('trail-demo').first()).toBeVisible()
  await serious(page, '/design')
  // It shows the tokens with their hex values, and every motion preset has a replay button.
  await expect(page.getByTestId('swatch-hex').first()).toHaveText(/^#[0-9A-F]{6}$/)
  expect(await page.getByRole('button', { name: /^Replay / }).count()).toBeGreaterThanOrEqual(7)
})
