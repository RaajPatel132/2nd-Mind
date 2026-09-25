import { expect, test, type Page, type TestInfo } from '@playwright/test'

// S2.11 / S2.12 / S2.3 on the compose stack (fake provider replaying the golden ingest cases).
// Each test logs in as a fresh dev user, so it starts from an empty memory on every run.

async function freshUser(page: Page, testInfo: TestInfo) {
  const email = `e2e-${testInfo.project.name}-${String(Date.now())}-${String(testInfo.workerIndex)}@example.test`
  const response = await page.request.post('/v1/auth/dev-login', { data: { email } })
  expect(response.ok()).toBeTruthy()
  await page.goto('/')
  await expect(page.getByTestId('composer')).toBeVisible({ timeout: 30_000 })
}

async function send(page: Page, message: string, expectReply: string | RegExp) {
  const composer = page.getByTestId('composer')
  await composer.fill(message)
  await composer.press('Enter')
  // A save runs several model calls and a write; on a busy local VM that can take a while.
  await expect(page.getByTestId('assistant-message').last()).toContainText(expectReply, { timeout: 60_000 })
  // Wait for the turn to finish (the words can arrive mid-stream); the chat sends one at a time.
  await expect(page.getByTestId('send')).toHaveText('Send', { timeout: 60_000 })
}

async function glassBoxFor(page: Page, testInfo: TestInfo, index = -1) {
  const glassBox = page.getByTestId('glass-box')
  if (testInfo.project.name === 'mobile-360') {
    await page.getByTestId('open-glass-box').nth(index).click()
  }
  await expect(glassBox).toBeVisible()
  return glassBox
}

test.describe('saving memories', () => {
  test.describe.configure({ timeout: 180_000 })

  test('§8.1 (b): three memories and a new person, shown in Decision and Memory diff', async ({ page }, testInfo) => {
    await freshUser(page, testInfo)
    await send(
      page,
      'My wife liked this bag from MK, serial no. 123ABC. We can gift it on her birthday next May.',
      "I've assumed 'My wife' is someone new",
    )
    const glassBox = await glassBoxFor(page, testInfo)

    const decision = glassBox.getByTestId('panel-decision')
    await expect(decision).toHaveAttribute('open', '')
    await expect(decision.getByTestId('decision-intent')).toContainText('Intent save')
    await expect(decision.getByTestId('decision-memory')).toHaveCount(3)
    await expect(decision.locator('[data-testid="decision-entity"][data-outcome="new"]', { hasText: 'wife' })).toHaveCount(1)
    await expect(decision.getByTestId('decision-date').first()).toContainText('next May')

    const diff = glassBox.getByTestId('panel-diff')
    await expect(diff).toHaveAttribute('open', '')
    const added = diff.locator('[data-testid="diff-entry"][data-op="added"]')
    await expect(added).toHaveCount(5) // wife, the MK bag, and the three memories
    await expect(added.filter({ hasText: 'Wife likes MK bags' })).toContainText('+')

    const tools = glassBox.getByTestId('panel-tools')
    await expect(tools.getByTestId('tool-call-policy').first()).toHaveText('allowed · P-DEFAULT')
    await expect(glassBox.getByText('Nothing was retrieved this turn.').first()).toBeVisible() // collapsed, with its reason
  })

  test('moving city supersedes the old fact, and undo brings it back', async ({ page }, testInfo) => {
    await freshUser(page, testInfo)
    await send(page, 'I live in Bengaluru', 'Bengaluru')
    await send(page, 'I moved to Pune', 'Updated: you live in Pune now')
    const glassBox = await glassBoxFor(page, testInfo)

    const superseded = glassBox.locator('[data-testid="diff-entry"][data-op="superseded"]')
    await expect(superseded).toHaveCount(1)
    await expect(superseded).toContainText('I live in Bengaluru')
    await expect(superseded).toContainText('valid_to')

    // The glass box renders only from stored events: a reload shows the same diff.
    await page.reload()
    const reloaded = await glassBoxFor(page, testInfo)
    await expect(reloaded.locator('[data-testid="diff-entry"][data-op="superseded"]')).toContainText('I live in Bengaluru')

    // The turn changed more than one memory, so undo asks first.
    await glassBox.getByTestId('undo-turn').click()
    await glassBox.getByTestId('undo-confirm').click()
    const note = page.locator('[data-testid="system-note"][data-kind="undo"]')
    await expect(note).toContainText('Undone')
    await expect(glassBox).toContainText('Undoes turn')
    await expect(glassBox.locator('[data-testid="diff-entry"][data-op="removed"]').first()).toBeVisible()
  })

  test('a sensitive core write is held until confirmed', async ({ page }, testInfo) => {
    await freshUser(page, testInfo)
    await send(page, "I've been seeing a therapist for anxiety since March", 'Held for your confirmation')
    const glassBox = await glassBoxFor(page, testInfo)

    const held = glassBox.locator('[data-testid="diff-entry"][data-op="held"]')
    await expect(held).toContainText('P-SENS-1')
    await held.getByTestId('held-confirm').click()
    await expect(page.locator('[data-testid="system-note"][data-kind="confirm"]')).toContainText('Confirmed')
  })

  test('a secret is refused and never shown back', async ({ page }, testInfo) => {
    await freshUser(page, testInfo)
    await send(page, 'Remember my wifi password is hunter2', "I didn't save that")
    await page.reload()
    await expect(page.getByText('hunter2')).toHaveCount(0)
  })
})
