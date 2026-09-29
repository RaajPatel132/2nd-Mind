import { expect, test } from '@playwright/test'
import { freshUser, send } from './helpers.ts'

// UI.7: the ring shows what's left, and goes down after a turn without a refetch.
test('after a turn the ring goes down and the popover shows the new remaining', async ({ page }, testInfo) => {
  await freshUser(page, testInfo)
  const ring = page.getByTestId('quota-ring')
  await expect(ring).toHaveAttribute('data-remaining', '1.0000')
  const before = await page.request.get('/v1/me/usage').then((r) => r.json() as Promise<{ remaining_usd: number; limit_usd: number }>)

  await send(page, 'I live in Bengaluru', 'Bengaluru')
  const after = await page.request.get('/v1/me/usage').then((r) => r.json() as Promise<{ remaining_usd: number; limit_usd: number }>)
  expect(after.remaining_usd).toBeLessThan(before.remaining_usd)
  await expect(ring).toHaveAttribute('data-remaining', (after.remaining_usd / after.limit_usd).toFixed(4))
  await expect(page.getByTestId('quota-delta')).toHaveText(/^−\$0\.\d/)

  await ring.click()
  const popover = page.getByTestId('quota-popover')
  await expect(popover).toBeVisible()
  const money = (n: number) => (n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`)
  await expect(popover.getByTestId('quota-remaining')).toHaveText(`${money(after.remaining_usd)} of ${money(after.limit_usd)} left`)
  await expect(popover.getByTestId('quota-last')).toHaveText(/^−\$0\.\d+$/)
  await page.keyboard.press('Escape')
  await expect(popover).toBeHidden()
  await expect(ring).toBeFocused()
})
