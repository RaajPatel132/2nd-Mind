import { expect, test } from '@playwright/test'
import { freshUser, inspect, send } from './helpers.ts'

// UI.6, UI.9, UI.10, UI.11, UI.12: the shell's smaller promises.

test('first run: three suggestions; picking one fills the composer and does not send', async ({ page }, testInfo) => {
  await freshUser(page, testInfo)
  await expect(page.getByRole('heading', { name: "What's on your mind?" })).toBeVisible()
  await expect(page.getByTestId('suggestion')).toHaveCount(3)
  await page.getByTestId('suggestion').first().click()
  await expect(page.getByTestId('composer')).toHaveValue('I live in Bengaluru')
  await expect(page.getByTestId('turn')).toHaveCount(0)
})

test('landmarks, the skip link, and texture removed in forced colours', async ({ page }, testInfo) => {
  await freshUser(page, testInfo)
  await expect(page.getByRole('banner')).toHaveCount(1)
  await expect(page.getByRole('main')).toHaveCount(1)
  await page.keyboard.press('Tab')
  await expect(page.getByRole('link', { name: 'Skip to message box' })).toBeFocused()
  const turn = await send(page, 'I live in Bengaluru')
  await inspect(page, turn)
  await expect(page.getByRole('complementary', { name: 'Inspector' })).toBeVisible()

  const texture = () =>
    page.evaluate(() => [getComputedStyle(document.body, '::before').display, getComputedStyle(document.body, '::after').display])
  expect(await texture()).toEqual(['block', 'block'])
  await page.emulateMedia({ forcedColors: 'active' })
  expect(await texture()).toEqual(['none', 'none'])
})

test('the composer grows to 8 lines, then scrolls', async ({ page }, testInfo) => {
  await freshUser(page, testInfo)
  const composer = page.getByTestId('composer')
  const height = () => composer.evaluate((el) => el.getBoundingClientRect().height)
  const one = await height()
  await composer.fill('one\ntwo\nthree')
  await expect.poll(height).toBeGreaterThan(one * 2.5)
  await composer.fill(Array.from({ length: 14 }, (_, i) => `line ${String(i + 1)}`).join('\n'))
  await expect.poll(height).toBeLessThanOrEqual(8 * 24 + 1)
  expect(await composer.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true)
})

test('a failed turn shows a bad rule, what went wrong and what to do', async ({ page }, testInfo) => {
  await freshUser(page, testInfo)
  await page.route('**/v1/workspaces/*/turns', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    const now = new Date().toISOString()
    const id = '00000000-0000-7000-8000-00000000000f'
    const turn = {
      id,
      workspace_id: '00000000-0000-7000-8000-000000000002',
      input: 'Buy coffee',
      output: null,
      status: 'failed',
      started_at: now,
      finished_at: now,
      config_hash: 'c'.repeat(64),
      prompt_versions: [],
      models: {},
      usage: { input_tokens: 0, cached_input_tokens: 0, output_tokens: 0, cost_usd: 0 },
      trace: { status: 'disabled', url: null },
      error: { code: 'provider_unavailable', message: 'The model provider is unavailable right now. Please try again in a moment.' },
      kind: 'user',
    }
    const frames: [string, unknown][] = [
      ['turn.started', { turn_id: id, workspace_id: turn.workspace_id, started_at: now }],
      ['step.started', { step: 'understand', at: now }],
      ['turn.event', { seq: 1, event: { type: 'step', v: 1, step: 'understand', status: 'failed', started_at: now, latency_ms: 20 } }],
      ['turn.failed', { turn_id: id, error: turn.error, usage: turn.usage, turn, quota: null }],
    ]
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'text/event-stream' },
      body: frames.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join(''),
    })
  })
  await page.getByTestId('composer').fill('Buy coffee')
  await page.getByTestId('composer').press('Enter')
  const turn = page.getByTestId('turn').last()
  await expect(turn).toHaveAttribute('data-status', 'failed')
  const failure = turn.getByRole('alert')
  await expect(failure).toContainText('The model provider is unavailable right now.')
  await expect(failure).toContainText('Send it again in a moment.')
  await expect(failure).toHaveClass(/border-bad/)
  await expect(turn.locator('[data-testid="trail-step"][data-state="failed"]')).toBeVisible()
})

test('timing & cost draws the waterfall and the quota after the turn', async ({ page }, testInfo) => {
  await freshUser(page, testInfo)
  const turn = await send(page, 'I live in Bengaluru')
  const glassBox = await inspect(page, turn)
  await expect(glassBox.getByTestId('waterfall-row')).toHaveCount(8)
  await expect(glassBox.getByTestId('quota-after')).toContainText(/Quota after this turn: [\d,]+ tokens left/)
})

test('the avatar popover shows the dev identity, and sign out ends the session', async ({ page }, testInfo) => {
  await freshUser(page, testInfo)
  await send(page, "I'm vegetarian")
  await page.getByTestId('quota-ring').click()
  await expect(page.getByTestId('account-email')).toContainText('@example.test')
  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page.getByText("You're signed out.")).toBeVisible()
  expect((await page.request.get('/v1/me')).status()).toBe(401)
})

test('scrolling up stops the follow and shows "Jump to latest", which brings you back', async ({ page }, testInfo) => {
  test.setTimeout(180_000) // three saves on a shared local VM
  await freshUser(page, testInfo)
  for (const m of ['I live in Bengaluru', "I'm vegetarian", 'I moved to Pune']) await send(page, m)
  const atBottom = () => page.evaluate(() => document.documentElement.scrollHeight - window.scrollY - window.innerHeight < 48)
  await expect.poll(atBottom).toBe(true)
  await page.mouse.move(200, 300)
  await page.mouse.wheel(0, -1500)
  const chip = page.getByRole('button', { name: 'Jump to latest' })
  await expect(chip).toBeVisible()
  await chip.click()
  await expect.poll(atBottom).toBe(true)
  await expect(chip).toBeHidden()
})
