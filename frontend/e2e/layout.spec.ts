import { expect, test } from '@playwright/test'
import { freshUser, inspect, send } from './helpers.ts'

// UI.13: 360 × 740 and 1440 × 900. No sideways scroll, the composer reachable, and the
// inspector a bottom sheet on the phone (docked on the wide screen).
test('no horizontal scroll, a reachable composer, and the inspector where it belongs', async ({ page }, testInfo) => {
  await freshUser(page, testInfo)
  const turn = await send(page, 'I live in Bengaluru', 'Bengaluru')
  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(await overflow()).toBeLessThanOrEqual(0)

  const composer = page.getByTestId('composer')
  await expect(composer).toBeInViewport()
  await page.keyboard.press('Escape')
  await page.locator('body').click({ position: { x: 5, y: 200 } })
  await page.keyboard.press('/')
  await expect(composer).toBeFocused()

  await inspect(page, turn)
  const mode = await page.getByTestId('inspector').getAttribute('data-mode')
  const width = page.viewportSize()?.width ?? 0
  expect(mode).toBe(width < 768 ? 'bottom' : width >= 1440 ? 'docked' : 'overlay')
  expect(await overflow()).toBeLessThanOrEqual(0)
  // Esc closes it and focus goes back to what opened it.
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('inspector')).toBeHidden()
  await expect(turn.getByTestId('inspect')).toBeFocused()
  // F6 toggles it for the latest turn.
  await page.keyboard.press('F6')
  await expect(page.getByTestId('inspector')).toBeVisible()
  await page.keyboard.press('F6')
  await expect(page.getByTestId('inspector')).toBeHidden()
})
