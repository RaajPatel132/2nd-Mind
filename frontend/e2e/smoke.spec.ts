import { expect, test } from '@playwright/test'
import { inspect, send } from './helpers.ts'

// S1.13 smoke: send a message, see the streamed reply, open the glass box (now the Inspector),
// see Timing & cost.
test.describe('chat and glass box', () => {
  test('a reply streams in and Timing & cost is filled in', async ({ page }) => {
    const message = `hello from e2e ${String(Date.now())}`
    await page.goto('/')
    await expect(page.getByTestId('composer')).toBeVisible()

    const turn = await send(page, message)
    const reply = turn.getByTestId('assistant-message')
    await expect(reply).toContainText(`You said: "${message}"`)
    await expect(reply).toContainText('fake provider')

    // The glass box is a sheet now (UI.11): closed until Inspect or F6 opens it.
    await expect(page.getByTestId('glass-box')).toBeHidden()
    const glassBox = await inspect(page, turn)

    const timing = glassBox.getByTestId('timing-cost')
    await expect(timing).toHaveAttribute('data-open', 'true')
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
    await send(page, message, message)

    await page.reload()
    await expect(page.getByTestId('messages').getByText(message, { exact: true }).first()).toBeVisible()
  })

  test('no horizontal scroll at this width', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByTestId('composer')).toBeVisible()
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(0)
  })

  test('fonts are self-hosted: no request leaves for a font host', async ({ page }) => {
    const requests: string[] = []
    page.on('request', (r) => requests.push(r.url()))
    await page.goto('/')
    await expect(page.getByTestId('composer')).toBeVisible()
    await page.evaluate(() => document.fonts.ready)
    const origin = new URL(page.url()).origin
    expect(requests.filter((u) => !u.startsWith(origin) && !u.startsWith('data:'))).toEqual([])
    const fonts = requests.filter((u) => /\.woff2?($|\?)/.test(u))
    expect(fonts.length).toBeGreaterThan(0)
    expect(fonts.every((u) => /-latin-/.test(u))).toBe(true)
  })
})
