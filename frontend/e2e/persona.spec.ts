import { expect, test, type Page } from '@playwright/test'
import { openStep } from './helpers.ts'

// S4.11 to S4.13 on the compose stack, with the fake provider: the landing page, the sample persona
// as a guest, the two suggested prompts, the workspace switcher and /evals. `make seed-persona` has
// loaded the template (the e2e target does it).

type Headline = { metrics: { id: string; display: string; run: string | null }[]; runs: { id: string }[] }

async function sendDraft(page: Page) {
  await page.getByTestId('composer').press('Enter')
  const turn = page.getByTestId('turn').last()
  await expect(turn).toHaveAttribute('data-status', /completed|failed/, { timeout: 60_000 })
  await expect(turn.getByTestId('receipt')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('send')).not.toHaveAttribute('data-state', 'sending')
  return turn
}

async function tryPersona(page: Page) {
  await page.goto('/')
  await expect(page.getByTestId('landing')).toBeVisible()
  await page.getByTestId('try-persona').click()
  await expect(page.getByTestId('composer')).toBeVisible({ timeout: 30_000 })
}

test.describe('the sample persona', () => {
  test.describe.configure({ timeout: 120_000 })

  test('the landing page says what it is, shows three measured numbers, and says where to click', async ({ page }) => {
    const numbers = (await (await page.request.get('/headline.json')).json()) as Headline
    await page.goto('/')
    await expect(page.getByTestId('landing-line')).toBeVisible()
    await expect(page.getByTestId('try-persona')).toBeVisible()
    await expect(page.getByTestId('sign-in')).toBeVisible()
    const stats = page.getByTestId('headline-stat')
    await expect(stats).toHaveCount(3)
    for (const metric of numbers.metrics) {
      const stat = page.locator(`[data-testid="headline-stat"][data-metric="${metric.id}"]`)
      // The number as measured (or "no run yet"), each one a link to the runs behind it.
      await expect(stat.getByTestId('headline-value')).toHaveText(metric.display)
      await expect(stat).toHaveAttribute('href', '/evals')
      if (metric.run === null) await expect(stat.getByTestId('headline-run')).toHaveText('no run yet')
    }
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(0)
  })

  test('/evals lists the runs behind the numbers', async ({ page }) => {
    const numbers = (await (await page.request.get('/headline.json')).json()) as Headline
    await page.goto('/evals')
    await expect(page.getByTestId('evals-page')).toBeVisible()
    await expect(page.getByTestId('evals-metric')).toHaveCount(numbers.metrics.length)
    for (const run of numbers.runs) await expect(page.getByTestId('evals-run').filter({ hasText: run.id })).toBeVisible()
  })

  test('the first thirty seconds: landing, the sample, one save with its diff, one recall with its citation', async ({ page }) => {
    const started = Date.now()
    await tryPersona(page)
    // It is Aditi's memory: her past chats are there, and the two suggested prompts are offered.
    await expect(page.getByTestId('messages')).toContainText('What did Priya say about my last review?')
    const prompts = page.getByTestId('suggested-prompts')
    await expect(prompts.getByTestId('suggested-prompt')).toHaveCount(2)
    await expect(page.getByRole('button', { name: /Memory: Sample persona/ })).toBeVisible()

    await prompts.locator('[data-prompt="save"]').click()
    const save = await sendDraft(page)
    await expect(save.getByTestId('assistant-message')).toContainText(/saved|noted/i)
    // The memory diff: the save row, opened, lists what was written.
    const detail = await openStep(save, 'save')
    await expect(detail.getByTestId('diff-entry').first()).toBeVisible()
    await expect(page.getByTestId('suggested-prompts').locator('[data-prompt="save"]')).toHaveCount(0) // sent

    await page.getByTestId('suggested-prompts').locator('[data-prompt="recall"]').click()
    const recall = await sendDraft(page)
    await expect(recall.getByTestId('assistant-message')).toContainText('Murakami') // the new save, used
    await expect(recall.getByTestId('citation').first()).toBeVisible()
    await expect(page.getByTestId('suggested-prompts')).toHaveCount(0) // both sent
    expect(Date.now() - started).toBeLessThan(30_000)
  })

  test('the glass box is showing when the sample opens: docked on a wide screen, the Trail open on a narrow one', async ({ page }) => {
    await tryPersona(page)
    await page.getByTestId('suggested-prompts').locator('[data-prompt="recall"]').click()
    const turn = await sendDraft(page)
    const width = page.viewportSize()?.width ?? 0
    if (width >= 1440) {
      await expect(page.getByTestId('inspector')).toBeVisible()
      await expect(page.getByTestId('inspector')).toHaveAttribute('data-mode', 'docked')
    } else {
      await expect(turn.locator('[data-testid="trail-step"]').first()).toBeVisible() // the Trail is open
    }
  })

  test('the switcher moves between the sample and a scratch memory, from the keyboard, and starts the sample over', async ({ page }) => {
    await tryPersona(page)
    await page.getByTestId('suggested-prompts').locator('[data-prompt="recall"]').click()
    await sendDraft(page)

    const switcher = page.getByRole('button', { name: /Memory: Sample persona/ })
    await switcher.focus()
    await page.keyboard.press('Enter')
    const listbox = page.getByRole('listbox')
    await expect(listbox).toBeVisible()
    await listbox.getByRole('option', { name: /Scratch/ }).click()
    // An empty memory of their own: the first-run greeting, none of Aditi's chats.
    await expect(page.getByTestId('first-run')).toBeVisible({ timeout: 30_000 })
    await expect(page.getByTestId('messages')).not.toContainText('Priya')
    await expect(page.getByRole('button', { name: /Memory: Scratch/ })).toBeVisible()

    await page.getByRole('button', { name: /Memory: Scratch/ }).click()
    await page.getByRole('listbox').getByRole('option', { name: /^Sample persona/ }).click()
    await expect(page.getByTestId('messages')).toContainText('What did Priya say about my last review?')
    await expect(page.getByTestId('messages')).toContainText('What could I get Kabir') // what was asked here

    // Start over: a fresh copy, with the suggested prompts offered again.
    await page.getByRole('button', { name: /Memory: Sample persona/ }).click()
    await page.getByRole('listbox').getByRole('option', { name: /Start the sample over/ }).click()
    await expect(page.getByTestId('suggested-prompts').getByTestId('suggested-prompt')).toHaveCount(2, { timeout: 30_000 })
    await expect(page.getByTestId('messages')).not.toContainText('What could I get Kabir')
  })

  test('a guest comes back as the same guest, and what they did is still there', async ({ page }) => {
    await tryPersona(page)
    await page.getByTestId('suggested-prompts').locator('[data-prompt="recall"]').click()
    await sendDraft(page)
    await page.reload()
    await expect(page.getByTestId('messages')).toContainText('What could I get Kabir')
  })
})
