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
  if (mode === 'docked') {
    // R.8: beside the chat, full height on the right edge (it once sat below the page, at the
    // bottom left), and the chat's content stops where the sheet starts.
    await page.waitForTimeout(600) // the slide-in
    const sheet = await page.getByTestId('inspector').boundingBox()
    const height = page.viewportSize()?.height ?? 0
    expect(sheet?.y).toBe(0)
    expect(sheet?.height).toBeGreaterThanOrEqual(height - 1)
    expect((sheet?.x ?? 0) + (sheet?.width ?? 0)).toBeGreaterThanOrEqual(width - 1)
    const main = await page.locator('main').boundingBox()
    expect((main?.x ?? 0) + (main?.width ?? 0)).toBeLessThanOrEqual((sheet?.x ?? 0) + 1)
  }
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

// S4.13, ledger 46: at 360 px the top bar's controls never sit on top of each other, and the spend
// chip that floats up after a turn doesn't land on the quota ring or its neighbours.
type Box = { x: number; y: number; width: number; height: number }

function overlaps(a: Box, b: Box): boolean {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height
}

test('the top bar keeps its controls apart and the spend chip off the quota ring', async ({ page }, testInfo) => {
  await freshUser(page, testInfo)
  const controls = {
    brand: page.getByRole('link', { name: '2nd Mind, home' }),
    memory: page.getByRole('button', { name: /^Memory:/ }),
    upcoming: page.getByTestId('nav-upcoming'),
    ring: page.getByTestId('quota-ring'),
  }
  async function boxes(): Promise<Map<string, Box>> {
    const found = new Map<string, Box>()
    for (const [name, locator] of Object.entries(controls)) {
      const box = await locator.boundingBox()
      if (box) found.set(name, box)
    }
    return found
  }

  const rest = await boxes()
  expect(rest.size).toBe(4)
  const names = [...rest.keys()]
  for (const [i, first] of names.entries())
    for (const second of names.slice(i + 1)) {
      const a = rest.get(first)
      const b = rest.get(second)
      if (a && b) expect(overlaps(a, b), `${first} and ${second}`).toBe(false)
    }

  const composer = page.getByTestId('composer')
  await composer.fill('I live in Bengaluru')
  await composer.press('Enter')
  const delta = page.getByTestId('quota-delta')
  await delta.waitFor({ state: 'visible', timeout: 60_000 })
  const chip = await delta.boundingBox()
  if (chip) for (const [name, box] of await boxes()) expect(overlaps(chip, box), `the spend chip and ${name}`).toBe(false)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)
})
