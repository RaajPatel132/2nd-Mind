import { expect, test } from '@playwright/test'

// S1.13 smoke: send a message, see the streamed reply, open the glass box, see Timing & cost.
test.describe('chat and glass box', () => {
  test('a reply streams in and Timing & cost is filled in', async ({ page }, testInfo) => {
    const mobile = testInfo.project.name === 'mobile-360'
    const message = `hello from e2e ${String(Date.now())}`
    await page.goto('/')

    const composer = page.getByTestId('composer')
    await expect(composer).toBeVisible()
    await composer.fill(message)
    await composer.press('Enter')

    const reply = page.getByTestId('assistant-message').last()
    await expect(reply).toContainText(`You said: "${message}"`)
    await expect(reply).toContainText('fake provider')

    if (mobile) {
      await expect(page.getByTestId('glass-box')).toBeHidden()
      await page.getByTestId('open-glass-box').last().click()
    }
    const glassBox = page.getByTestId('glass-box')
    await expect(glassBox).toBeVisible()

    const timing = glassBox.getByTestId('timing-cost')
    await expect(timing).toHaveAttribute('open', '')
    await expect(timing.getByTestId('model-call-model').first()).toHaveText('fake · fake-chat')
    await expect(timing.getByTestId('call-latency').first()).toHaveText(/\d+(\.\d+)? m?s/)
    await expect(timing.getByTestId('call-cost').first()).toHaveText(/^\$\d/)
    await expect(timing.getByTestId('turn-cost')).toHaveText(/^\$\d/)
    for (const panel of ['Decision', 'Memory diff', 'Retrieval', 'Tool calls']) {
      await expect(glassBox.getByText(panel, { exact: true })).toBeVisible()
    }
    await expect(timing.getByTestId('trace-link')).toBeVisible()
  })

  test('history is there after a refresh', async ({ page }) => {
    const message = `remember me ${String(Date.now())}`
    await page.goto('/')
    await page.getByTestId('composer').fill(message)
    await page.getByTestId('composer').press('Enter')
    await expect(page.getByTestId('assistant-message').last()).toContainText(message)

    await page.reload()
    await expect(page.getByText(message, { exact: true })).toBeVisible()
  })

  test('no horizontal scroll at this width', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByTestId('composer')).toBeVisible()
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(overflow).toBeLessThanOrEqual(0)
  })
})
