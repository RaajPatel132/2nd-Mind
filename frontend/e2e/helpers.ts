import { expect, type Locator, type Page, type TestInfo } from '@playwright/test'

/** A fresh dev user, so every test starts from an empty memory. */
export async function freshUser(page: Page, testInfo: TestInfo) {
  const email = `e2e-${testInfo.project.name}-${String(Date.now())}-${String(testInfo.workerIndex)}@example.test`
  const response = await page.request.post('/v1/auth/dev-login', { data: { email } })
  expect(response.ok()).toBeTruthy()
  await page.goto('/')
  await expect(page.getByTestId('composer')).toBeVisible({ timeout: 30_000 })
}

/** The latest turn (a message or an event row). */
export function lastTurn(page: Page): Locator {
  return page.getByTestId('messages').locator('[data-testid="turn"], [data-testid="system-note"]').last()
}

/** Send a message and wait until its turn has finished and its receipt is shown. */
export async function send(page: Page, message: string, expectReply?: string | RegExp) {
  const composer = page.getByTestId('composer')
  await composer.fill(message)
  await composer.press('Enter')
  const turn = page.getByTestId('turn').last()
  // A save runs several model calls and a write; on a busy local VM that can take a while.
  await expect(turn).toHaveAttribute('data-status', /completed|failed/, { timeout: 60_000 })
  await expect(turn.getByTestId('receipt')).toBeVisible({ timeout: 15_000 })
  if (expectReply !== undefined) await expect(turn.getByTestId('assistant-message')).toContainText(expectReply)
  await expect(page.getByTestId('send')).not.toHaveAttribute('data-state', 'sending')
  return turn
}

/** A step row of a turn's Trail, opened so its two layers are readable. */
export async function openStep(turn: Locator, step: string): Promise<Locator> {
  const row = turn.locator(`[data-testid="trail-step"][data-step="${step}"]`)
  await expect(row).toBeVisible()
  const trigger = row.locator('button[data-step-row]')
  if ((await trigger.getAttribute('aria-expanded')) !== 'true') await trigger.click()
  const detail = row.getByTestId('step-detail')
  await expect(detail).toBeVisible()
  return detail
}

/** Open the inspector for a turn from its receipt; returns the glass box inside it. */
export async function inspect(page: Page, turn: Locator): Promise<Locator> {
  await turn.getByTestId('inspect').click()
  const glassBox = page.getByTestId('glass-box')
  await expect(glassBox).toBeVisible()
  return glassBox
}
