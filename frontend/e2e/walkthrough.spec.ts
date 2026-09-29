import { expect, test, type Page } from '@playwright/test'
import { freshUser, inspect, lastTurn, send } from './helpers.ts'

/**
 * R.6: the hand walkthrough, driven by a script so it can be repeated. It runs on the
 * production-shaped stack with real models (`E2E_WALKTHROUGH=1 E2E_ACCESS_CODE=…`), takes a
 * screenshot at every step into `walkthrough-shots/` (gitignored, kept locally) and logs what it
 * saw. It asserts structure only; anything that looks wrong is for a person to read in the
 * pictures. Not part of the CI gate.
 */
test.skip(!process.env.E2E_WALKTHROUGH, 'the live walkthrough runs on demand')
test.describe.configure({ mode: 'serial', timeout: 300_000 })

const shots = 'walkthrough-shots'
let n = 0
async function shot(page: Page, name: string) {
  n += 1
  await page.screenshot({ path: `${shots}/${String(n).padStart(2, '0')}-${name}.png`, fullPage: false })
}

test('S3 demo on the recall fixture, at 1440 and 1920', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await freshUser(page, testInfo)
  expect((await page.request.post('/v1/dev/seed-recall')).ok()).toBeTruthy()
  await page.reload()
  await expect(page.getByTestId('composer')).toBeVisible({ timeout: 30_000 })
  await shot(page, 'first-run-fixture')

  const where = await send(page, 'Where do I live?', 'Pune')
  await shot(page, 'where-do-i-live')
  await where.getByTestId('citation').first().click()
  await expect(page.getByTestId('item-detail')).toBeVisible()
  await shot(page, 'citation-opens-item')
  await page.keyboard.press('Escape')

  const glass = await inspect(page, where)
  await expect(glass.getByTestId('panel-retrieval')).toBeVisible()
  await shot(page, 'inspector-docked-1440')
  await page.setViewportSize({ width: 1920, height: 1080 })
  await shot(page, 'inspector-docked-1920')
  await page.keyboard.press('Escape')

  await send(page, 'How many runs did I do in September?')
  await shot(page, 'count-with-offer')
  const abstain = await send(page, "What's Kabir's shoe size?")
  await expect(abstain.getByTestId('assistant-message')).toContainText(/don't have|nothing/i)
  await shot(page, 'abstains')
  await send(page, 'What did I read about sleep?')
  await shot(page, 'soft-channel')
  await send(page, 'What books did you suggest for a slow weekend?')
  await shot(page, 'conversation-recall')
  await send(page, 'yes')
  await shot(page, 'offer-accepted')

  await page.getByTestId('nav-upcoming').click()
  await expect(page.getByTestId('upcoming')).toBeVisible()
  await shot(page, 'upcoming')
})

test('S2.5 demo: trail, held write, refused secret, undo, inspector, at 360', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 360, height: 740 })
  await freshUser(page, testInfo)
  await shot(page, 'mobile-first-run')
  await send(page, 'I live in Bengaluru')
  await shot(page, 'mobile-save')
  await send(page, "I've been seeing a therapist for anxiety since March")
  await shot(page, 'mobile-held-write')
  const secret = await send(page, 'My gym locker combination is 17-38-02')
  await expect(secret.getByTestId('assistant-message')).not.toContainText('17-38-02')
  await shot(page, 'mobile-refused-secret')
  await send(page, 'I moved to Pune')
  await shot(page, 'mobile-supersede')
  await lastTurn(page).getByTestId('undo-turn').click()
  const confirm = lastTurn(page).getByTestId('undo-confirm')
  if (await confirm.isVisible()) await confirm.click()
  await shot(page, 'mobile-undo')
  await inspect(page, page.getByTestId('turn').first())
  await shot(page, 'mobile-inspector')
})

test('S3 verification features: snooze and the memory editor', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await freshUser(page, testInfo)
  const turn = await send(page, 'Remind me to renew my passport before it expires on the 3rd of next month.')
  const glass = await inspect(page, turn)
  await glass.getByTestId('panel-diff').getByTestId('diff-edit').first().click()
  await page.keyboard.press('Escape')
  const sheet = page.getByTestId('item-detail')
  await expect(sheet).toBeVisible()
  await expect(sheet.getByTestId('edit-date-picker')).toBeVisible()
  await shot(page, 'editor-date-picker')
  await page.keyboard.press('Escape')
  await page.getByTestId('nav-upcoming').click()
  await expect(page.getByTestId('upcoming')).toBeVisible()
  await shot(page, 'upcoming-with-reminder')
  expect(lastTurn(page)).toBeTruthy()
})
