import { expect, test, type Locator, type Page } from '@playwright/test'
import { freshUser, inspect, openStep, send } from './helpers.ts'

// S4.9 on the compose stack, with the fake provider and the fixture page server (`fixtures`, and
// the video hosts it stands in for): a full link, a partial link completed with pasted text, a
// video and a refused private address. Nothing here reaches the internet.

const PAGES = 'http://fixtures:8080'
const VIDEO = 'http://www.youtube.com:8080/watch?v=qk7Lp2xR9aE'

function fetchRow(turn: Locator): Locator {
  return turn.locator('[data-testid="trail-step"][data-step="fetch"]')
}

/** Open the saved link's memory from the glass box (its save is in the Memory diff). */
async function openLink(page: Page, turn: Locator): Promise<Locator> {
  const glassBox = await inspect(page, turn)
  await glassBox.getByTestId('panel-diff').getByTestId('diff-edit').first().click()
  await page.keyboard.press('Escape')
  const sheet = page.getByTestId('item-detail')
  await expect(sheet.getByTestId('link-section')).toBeVisible()
  return sheet
}

test.describe('links', () => {
  test.describe.configure({ timeout: 120_000 })

  test('a saved link is read after the turn ends, and its row updates in place with no reload', async ({ page }, testInfo) => {
    await freshUser(page, testInfo)
    const turn = await send(page, `for the sleep tips ${PAGES}/sleep`, 'Saved the link. Reading it now.')
    const row = fetchRow(turn)
    await expect(row).toHaveCount(1) // one row for the save and the read
    // The worker reads the page after the reply; the same row takes the result.
    await expect(row.getByTestId('step-label')).toHaveText(/^Read /, { timeout: 60_000 })
    await expect(row).toHaveAttribute('data-state', 'done')

    const detail = await openStep(turn, 'fetch')
    const facts = detail.getByTestId('fetch-facts')
    await expect(facts).toContainText('fixtures')
    await expect(facts).toContainText('extraction')
    await expect(facts).toContainText('passages')
    await expect(detail.getByTestId('fetch-plain')).toContainText('not from you')

    const sheet = await openLink(page, turn)
    await expect(sheet.getByTestId('link-status')).toHaveText('Read')
    await expect(sheet.getByTestId('link-summary')).toBeVisible()
    // The original opens in a new tab, and the new tab can't reach back (noopener noreferrer).
    const open = sheet.getByTestId('link-open')
    await expect(open).toHaveAttribute('href', `${PAGES}/sleep`)
    await expect(open).toHaveAttribute('target', '_blank')
    await expect(open).toHaveAttribute('rel', /noopener/)
    await expect(open).toHaveAttribute('rel', /noreferrer/)
    // What the person said is still theirs, shown under its own heading.
    await expect(sheet).toContainText('What you said')
    await expect(sheet).toContainText('for the sleep tips')
    // Nothing overflows sideways, at any width.
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(0)
  })

  test('a partly read link is completed with pasted text, from the keyboard', async ({ page }, testInfo) => {
    await freshUser(page, testInfo)
    const turn = await send(page, `the riverside vote ${PAGES}/council`, 'Saved the link. Reading it now.')
    await expect(fetchRow(turn).getByTestId('step-label')).toHaveText('Could only read part of it (paywall). Add the text?', { timeout: 60_000 })

    const sheet = await openLink(page, turn)
    await expect(sheet.getByTestId('link-status')).toHaveText('Partly read')
    await expect(sheet.getByTestId('link-card')).toContainText('paywall')
    const add = sheet.getByTestId('link-add-text')
    await add.focus()
    await page.keyboard.press('Enter')
    const text = sheet.getByTestId('link-text')
    await expect(text).toBeVisible()
    await text.fill(
      'The council voted on Tuesday to approve the riverside plan after a long debate about its cost. ' +
        'The plan moves the library into the old mill by the weir, keeps the two oldest warehouses as a market hall, ' +
        'and opens a path along the water that will run for two kilometres. Work begins in the spring, and the first ' +
        'stage is due to finish before the following winter, according to the council papers.',
    )
    await sheet.getByTestId('link-read-text').click()
    await expect(sheet.getByTestId('link-status')).toHaveText('Read', { timeout: 60_000 })
    await expect(sheet.getByTestId('link-add-text')).toHaveCount(0) // nothing left to add
  })

  test('a video shows its channel and length, and says there is no transcript', async ({ page }, testInfo) => {
    await freshUser(page, testInfo)
    const turn = await send(page, `a quick dinner ${VIDEO}`, 'Saved the link. Reading it now.')
    const label = fetchRow(turn).getByTestId('step-label')
    await expect(label).toContainText('Quiet Kitchen', { timeout: 60_000 })
    await expect(label).toContainText('10:40')
    const detail = await openStep(turn, 'fetch')
    await expect(detail.getByTestId('fetch-plain')).toContainText('no transcript')

    const sheet = await openLink(page, turn)
    await expect(sheet.getByTestId('link-card')).toHaveAttribute('data-kind', 'video')
    await expect(sheet.getByTestId('link-facts')).toHaveText('Quiet Kitchen · 10:40')
  })

  test('a private address is refused in the open: no request, and the rule is shown', async ({ page }, testInfo) => {
    await freshUser(page, testInfo)
    const turn = await send(page, 'look at http://192.168.1.20/admin', /didn.t open it/i)
    const row = fetchRow(turn)
    await expect(row).toHaveAttribute('data-state', 'refused')
    await expect(row.getByTestId('step-label')).toHaveText("Didn't open it: private address.")
    // A refusal opens by itself, with its reason in plain words.
    await expect(row.getByTestId('step-detail')).toContainText('No request was made')

    const glassBox = await inspect(page, turn)
    const tools = glassBox.getByTestId('panel-tools')
    await expect(tools).toContainText('web.fetch')
    await expect(tools.getByTestId('tool-call-policy').filter({ hasText: 'private_address' })).toBeVisible()
    await expect(tools).toContainText('192.168.1.20') // the host, never a path
    await expect(tools).not.toContainText('/admin')
  })

  test('an answer that rests on a saved page quotes the passage, marked as the page', async ({ page }, testInfo) => {
    await freshUser(page, testInfo)
    const turn = await send(page, `for the sleep tips ${PAGES}/sleep`, 'Saved the link. Reading it now.')
    await expect(fetchRow(turn).getByTestId('step-label')).toHaveText(/^Read /, { timeout: 60_000 })

    // The fake reranker scores by shared words, so the question echoes the words the person used.
    const ask = await send(page, 'What about lavender in my sleep tips?')
    const snippets = ask.getByTestId('page-snippets')
    await expect(snippets).toBeVisible()
    await expect(snippets).toContainText('From the page')
    await expect(snippets).toContainText('lavender')
    // The retrieval panel says which passage matched.
    const glassBox = await inspect(page, ask)
    await expect(glassBox.getByTestId('matched-passage').first()).toContainText('matched via page passage')
  })
})
