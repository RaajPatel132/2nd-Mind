import { expect, test } from '@playwright/test'
import { freshUser, send } from './helpers.ts'

// UI.7: the ring shows what's left, and goes down after a turn without a refetch.
test('after a turn the ring goes down and the popover shows the new remaining', async ({ page }, testInfo) => {
  await freshUser(page, testInfo)
  const ring = page.getByTestId('quota-ring')
  await expect(ring).toHaveAttribute('data-remaining', '1.0000')
  const before = await page.request.get('/v1/me/usage').then((r) => r.json() as Promise<{ remaining_tokens: number; limit_tokens: number }>)

  await send(page, 'I live in Bengaluru', 'Bengaluru')
  const after = await page.request.get('/v1/me/usage').then((r) => r.json() as Promise<{ remaining_tokens: number; limit_tokens: number }>)
  expect(after.remaining_tokens).toBeLessThan(before.remaining_tokens)
  await expect(ring).toHaveAttribute('data-remaining', (after.remaining_tokens / after.limit_tokens).toFixed(4))
  await expect(page.getByTestId('quota-delta')).toHaveText(/^−\d/)

  await ring.click()
  const popover = page.getByTestId('quota-popover')
  await expect(popover).toBeVisible()
  await expect(popover.getByTestId('quota-remaining')).toHaveText(
    `${after.remaining_tokens.toLocaleString('en-US')} of ${after.limit_tokens.toLocaleString('en-US')} tokens`,
  )
  await expect(popover.getByTestId('quota-last')).toHaveText(/^−[\d,]+ tokens · \$/)
  await page.keyboard.press('Escape')
  await expect(popover).toBeHidden()
  await expect(ring).toBeFocused()
})
