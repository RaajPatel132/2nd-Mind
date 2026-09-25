import { expect, test } from '@playwright/test'
import { freshUser, send } from './helpers.ts'

// UI.13: with reduced motion a whole save turn plays with fades only, and when it ends nothing
// is still animating (no shimmer, pulse, caret or spinner left running).
test('a full save turn under reduced motion leaves nothing animating', async ({ page }, testInfo) => {
  await freshUser(page, testInfo)
  expect(await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(true)
  const turn = await send(page, 'I live in Bengaluru', 'Bengaluru')
  await expect(turn.getByTestId('trail-step')).toHaveCount(8)
  await expect(turn.locator('[data-testid="trail-step"][data-state="running"]')).toHaveCount(0)
  await expect
    .poll(() => page.evaluate(() => document.getAnimations().filter((a) => a.playState === 'running').length), { timeout: 5_000 })
    .toBe(0)
})
