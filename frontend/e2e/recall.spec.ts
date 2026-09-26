import { expect, test, type Page } from '@playwright/test'
import { freshUser, inspect, lastTurn, send } from './helpers.ts'

// S3.13 / S3.12 / S3.14 on the compose stack: the fake provider replays the recall golden
// cases' plans, and the synthetic recall fixture is seeded through the dev-only endpoint.

async function seeded(page: Page, testInfo: Parameters<typeof freshUser>[1]) {
  await freshUser(page, testInfo)
  const seed = await page.request.post('/v1/dev/seed-recall')
  expect(seed.ok()).toBeTruthy()
  await page.reload()
  await expect(page.getByTestId('composer')).toBeVisible({ timeout: 30_000 })
}

test.describe('recall', () => {
  test.describe.configure({ timeout: 180_000 })

  test('"Where do I live?": latest, Pune cited, Bengaluru present but demoted, the citation opens the item', async ({ page }, testInfo) => {
    await seeded(page, testInfo)
    const turn = await send(page, 'Where do I live?', 'Pune')
    await expect(turn.locator('[data-testid="trail-step"][data-step="plan"]').getByTestId('step-label')).toHaveText('1 question · latest')

    const glassBox = await inspect(page, turn)
    const retrieval = glassBox.getByTestId('panel-retrieval')
    await expect(retrieval).toHaveAttribute('data-open', 'true')
    const pune = retrieval.getByTestId('candidate').filter({ hasText: 'Lives in Pune' }).first()
    await expect(pune).toHaveAttribute('data-selected', 'true')
    await expect(pune).toContainText('cited')
    const bengaluru = retrieval.getByTestId('candidate').filter({ hasText: 'Lives in Bengaluru' }).first()
    await expect(bengaluru).toHaveAttribute('data-demoted', 'true')
    await expect(bengaluru).toHaveAttribute('data-selected', 'false')
    await expect(glassBox.getByTestId('tool-call-read').first()).toBeVisible()
    await expect(glassBox.getByTestId('waterfall-span').first()).toBeVisible()
    await page.keyboard.press('Escape')

    await turn.getByTestId('citation').first().click()
    const sheet = page.getByTestId('item-detail')
    await expect(sheet).toBeVisible()
    await expect(sheet).toContainText('I live in Pune.')
  })

  test('a date is fixed from the glass box as its own undoable turn', async ({ page }, testInfo) => {
    await freshUser(page, testInfo)
    const turn = await send(page, 'Remind me to renew my passport before it expires on the 3rd of next month.', /passport/i)
    const glassBox = await inspect(page, turn)
    await glassBox.getByTestId('panel-diff').getByTestId('diff-edit').first().click()
    await page.keyboard.press('Escape')
    const sheet = page.getByTestId('item-detail')
    await expect(sheet).toBeVisible()
    await sheet.getByTestId('edit-date').fill('5 November 2026')
    await sheet.getByTestId('edit-save').click()
    const edit = lastTurn(page)
    await expect(edit).toHaveAttribute('data-kind', 'edit', { timeout: 30_000 })
    await expect(edit).toHaveAttribute('data-status', 'completed')
    await expect(edit.getByTestId('assistant-message')).toContainText('Edited')
  })

  test('Upcoming lists what is ahead by day, and Done is a turn', async ({ page }, testInfo) => {
    await seeded(page, testInfo)
    await page.getByTestId('nav-upcoming').click()
    const upcoming = page.getByTestId('upcoming')
    await expect(upcoming).toBeVisible()
    await expect(upcoming.getByTestId('upcoming-entry').filter({ hasText: 'Dinner at Saffron Street' }).first()).toBeVisible()
    await expect(upcoming.getByTestId('upcoming-undated').filter({ hasText: 'kitchen tap' })).toHaveCount(1)
    await upcoming.getByTestId('upcoming-undated').filter({ hasText: 'kitchen tap' }).getByRole('button', { name: 'Done' }).click()
    await expect(upcoming.getByTestId('upcoming-undated').filter({ hasText: 'kitchen tap' })).toHaveCount(0)
    await page.getByTestId('nav-upcoming').click()
    await expect(lastTurn(page)).toHaveAttribute('data-kind', 'edit')
  })
})

test.describe('upcoming snooze', () => {
  test.describe.configure({ timeout: 180_000 })

  test('Snooze moves a reminder, not the plan, and undo puts it back', async ({ page }, testInfo) => {
    await seeded(page, testInfo)
    await page.getByTestId('nav-upcoming').click()
    const upcoming = page.getByTestId('upcoming')
    const row = (via: string) =>
      upcoming.locator(`[data-testid="upcoming-entry"][data-via="${via}"]`).filter({ hasText: 'Dinner at Saffron Street' }).first()
    const dayOf = (via: string) => row(via).locator('xpath=ancestor::section[1]').getAttribute('aria-label')
    await expect(row('trigger')).toBeVisible()
    const [reminderDay, dinnerDay] = [await dayOf('trigger'), await dayOf('occurred')]

    await row('trigger').getByRole('button', { name: /Snooze/ }).click()
    await page.getByRole('option', { name: '1 day' }).click()
    await expect.poll(() => dayOf('trigger')).not.toBe(reminderDay)
    expect(await dayOf('occurred')).toBe(dinnerDay)

    await page.getByTestId('nav-upcoming').click()
    const edit = lastTurn(page)
    await expect(edit).toHaveAttribute('data-kind', 'edit', { timeout: 30_000 })
    await expect(edit.getByTestId('assistant-message')).toContainText('Snoozed')
    await edit.getByTestId('undo-turn').click()
    await expect(page.locator('[data-testid="system-note"][data-kind="undo"]')).toContainText('Undone')
    await page.getByTestId('nav-upcoming').click()
    await expect.poll(() => dayOf('trigger')).toBe(reminderDay)
  })
})
