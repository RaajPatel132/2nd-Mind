import { expect, test } from '@playwright/test'
import { FLAG_SETTLE_MS, killSwitch, setTier } from './admin.ts'
import { freshUser, inspect, send } from './helpers.ts'

/**
 * R.10 / ADR-0032, on the running stack: the kill switch stops the next message with a notice and
 * no model call, browsing keeps working, and switching it off needs no restart; an upgrade changes
 * the allowance on the next turn.
 */
test.describe.configure({ mode: 'serial' })

test('@prodlike the kill switch gives the next message a notice, and off brings answers back', async ({ page }, testInfo) => {
  test.setTimeout(120_000)
  await freshUser(page, testInfo)
  await send(page, 'I live in Bengaluru', 'Bengaluru')
  killSwitch('on')
  try {
    await page.waitForTimeout(FLAG_SETTLE_MS)
    const composer = page.getByTestId('composer')
    await composer.fill('Where do I live?')
    await composer.press('Enter')
    const turn = page.getByTestId('turn').last()
    await expect(turn).toHaveAttribute('data-status', 'completed')
    await expect(turn.getByTestId('assistant-message')).toContainText('paused for everyone')
    await expect(turn.getByTestId('assistant-message')).toContainText('browse your memory')
    // The composer says the same, and the glass box explains the stop; no model ran.
    await expect(page.getByTestId('composer-notice')).toContainText('paused for everyone')
    await expect(turn.locator('[data-testid="trail-step"][data-step="blocked"]')).toContainText('Paused for everyone')
    const glassBox = await inspect(page, turn)
    await expect(glassBox.getByTestId('turn-cost')).toHaveText('$0')
    await page.keyboard.press('Escape')

    // Browsing does not call a model: Upcoming still opens.
    await page.getByTestId('nav-upcoming').click()
    await expect(page.getByTestId('upcoming')).toBeVisible()
    await page.getByTestId('nav-upcoming').click()
  } finally {
    killSwitch('off')
  }
  await page.waitForTimeout(FLAG_SETTLE_MS)
  await send(page, 'Where do I live now?', 'Bengaluru')
  await expect(page.getByTestId('composer-notice')).toHaveCount(0)
})

test('upgrading a person changes their allowance on the next turn', async ({ page }, testInfo) => {
  await freshUser(page, testInfo)
  const me = (await (await page.request.get('/v1/me')).json()) as { user: { email: string } }
  const before = (await (await page.request.get('/v1/me/usage')).json()) as { limit_usd: number; tier: string }
  expect(before.tier).toBe('standard')
  setTier(me.user.email, 'premium')
  await send(page, "I'm vegetarian")
  await page.getByTestId('quota-ring').click()
  await expect(page.getByTestId('quota-remaining')).toContainText('of $4.00 left')
})
