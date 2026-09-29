import { expect, test } from '@playwright/test'
import { freshUser, inspect, send } from './helpers.ts'

/**
 * R.13: the tagged subset that runs against the production-shaped stack (`make up-prodlike`,
 * staging config, real models): save, recall, undo, the glass box, the web headers and the access
 * door. The kill-switch notice (spend.spec.ts) carries the same tag. Nothing here compares a
 * model's wording; the fake-provider run stays the CI gate.
 *
 *   E2E_ACCESS_CODE=… E2E_COMPOSE="-p secondmind-prodlike -f ../compose.yaml -f ../compose.prodlike.yaml …" \
 *     npx playwright test --grep @prodlike --project=desktop
 */
test.skip(!process.env.E2E_ACCESS_CODE, 'runs against the production-shaped stack only')
test.describe.configure({ mode: 'serial' })

test('@prodlike the door asks for the access code and the web tier sends its headers', async ({ page }) => {
  const denied = await page.request.post('/v1/auth/dev-login', { data: { email: 'no-code@example.test' } })
  expect(denied.status()).toBeGreaterThanOrEqual(400)
  expect(denied.status()).toBeLessThan(500)

  const home = await page.request.get('/')
  const headers = home.headers()
  expect(headers['x-content-type-options']).toBe('nosniff')
  expect(headers['content-security-policy']).toBeTruthy()
  expect(headers['strict-transport-security']).toContain('max-age')
  const api = await page.request.get('/v1/meta')
  expect(api.headers()['cache-control']).toContain('no-store')
  expect((await api.json()) as { env: string; provider_mode: string }).toMatchObject({ env: 'staging', provider_mode: 'live' })
})

test('@prodlike save, recall and undo on real models, and the glass box explains the turn', async ({ page }, testInfo) => {
  test.setTimeout(180_000)
  await freshUser(page, testInfo)
  // No provider tag in the top bar: live is the normal state.
  await expect(page.getByTestId('provider-mode')).toHaveCount(0)

  await send(page, 'I live in Bengaluru')
  const save = page.getByTestId('turn').first() // send() returns the latest turn, a moving target
  await expect(save.getByTestId('receipt')).toBeVisible()
  const recall = await send(page, 'Where do I live?', 'Bengaluru')

  const glassBox = await inspect(page, recall)
  await expect(glassBox.getByTestId('turn-cost')).not.toHaveText('$0')
  await expect(glassBox.getByTestId('waterfall-row').first()).toBeVisible()
  await page.keyboard.press('Escape')

  await save.getByTestId('undo-turn').click()
  const confirm = save.getByTestId('undo-confirm')
  if (await confirm.isVisible()) await confirm.click()
  await expect(page.locator('[data-testid="system-note"][data-kind="undo"]')).toContainText('Undone')
  const after = await send(page, 'Where do I live?')
  await expect(after.getByTestId('assistant-message')).not.toContainText('Bengaluru')
})
