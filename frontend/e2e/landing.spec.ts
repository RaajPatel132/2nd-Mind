import { expect, test } from '@playwright/test'

// The landing page's words: the line, the three beats (their examples are the sample persona's own
// prompts, read from /v1/meta so they can't drift from the seed), and the footer that points at the
// code and the write-ups. Runs on the compose stack with `make seed-persona` loaded.

type Meta = { persona: { prompts: { save: string; recall: string } } | null }

test.describe('the landing page', () => {
  test('says what sets it apart, in the product line', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByTestId('landing-line')).toHaveText('A personal memory that shows its work.')
  })

  test('shows three beats, two of them with the sample persona’s own prompts', async ({ page, request }) => {
    const meta = (await (await request.get('/v1/meta')).json()) as Meta
    test.skip(meta.persona === null, 'the sample persona is not loaded on this stack')
    await page.goto('/')
    const beats = page.getByTestId('landing-beats').locator('li[data-beat]')
    await expect(beats).toHaveCount(3)
    await expect(beats.nth(0)).toContainText('Catch it.')
    await expect(beats.nth(1)).toContainText('Find it.')
    await expect(beats.nth(2)).toContainText('Prove it.')
    await expect(beats.nth(0)).toContainText(meta.persona?.prompts.save ?? '')
    await expect(beats.nth(1)).toContainText(meta.persona?.prompts.recall ?? '')
  })

  test('points at the source, the architecture, the decisions and the runs', async ({ page }) => {
    await page.goto('/')
    const footer = page.getByTestId('landing-footer')
    for (const id of ['source', 'architecture', 'decisions']) {
      const link = footer.locator(`a[data-link="${id}"]`)
      await expect(link).toHaveAttribute('href', /^https:\/\/github\.com\/RaajPatel132\/2nd-Mind(\/|$)/)
      await expect(link, 'opens in a new tab without leaking the referrer').toHaveAttribute('rel', /noreferrer/)
      await expect(link).toHaveAttribute('target', '_blank')
    }
    await expect(footer.locator('a[data-link="measured"]')).toHaveAttribute('href', '/evals')
  })

  test('keeps the three numbers the same height, however their labels wrap', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.goto('/')
    const stats = page.getByTestId('headline-stat')
    await expect(stats).toHaveCount(3)
    const heights = await stats.evaluateAll((els) => els.map((el) => Math.round(el.getBoundingClientRect().height)))
    expect(new Set(heights).size, `stat card heights ${heights.join(', ')}`).toBe(1)
  })

  test('does not scroll sideways at 360 px', async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 740 })
    await page.goto('/')
    await expect(page.getByTestId('landing-footer')).toBeVisible()
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(0)
  })
})
