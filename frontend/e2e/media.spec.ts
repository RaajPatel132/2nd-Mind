import { mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test, type Page } from '@playwright/test'

/**
 * The pictures in the README and the link-preview card, captured by script so they can be remade
 * the day the screens change. On demand, not part of the CI gate:
 *
 *   make seed-persona                                   # the sample persona's template
 *   E2E_MEDIA=1 npx playwright test media.spec.ts --project=desktop
 *   uv run --with pillow python ../scripts/make_demo_gif.py    # frames -> docs/media/tour.gif
 *
 * Against the plain stack the model is the offline fake (its replies are scripted, and the Trail
 * names it); say so wherever a picture is shown. To capture real replies, start the stack with
 * `make up-live` and pass E2E_BASE_URL. That costs cents; the offline pictures cost nothing.
 *
 * Writes: docs/media/{landing,chat,glass-box,phone,social-preview}.png, frontend/public/og.png,
 * and the tour's frames, with their timings (frames.json), to $MEDIA_FRAMES or the system temp
 * directory's secondmind-media-frames.
 */
test.skip(!process.env.E2E_MEDIA, 'the media capture runs on demand')
test.describe.configure({ mode: 'serial', timeout: 180_000 })

const here = dirname(fileURLToPath(import.meta.url))
const docsMedia = join(here, '..', '..', 'docs', 'media')
const publicDir = join(here, '..', 'public')
// Not under test-results: Playwright empties that at the start of every run.
const framesDir = process.env.MEDIA_FRAMES ?? join(tmpdir(), 'secondmind-media-frames')

async function settled(page: Page, last = true) {
  const turn = page.getByTestId('turn').last()
  await expect(turn).toHaveAttribute('data-status', /completed|failed/, { timeout: 90_000 })
  if (last) await expect(turn.getByTestId('receipt')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByTestId('send')).not.toHaveAttribute('data-state', 'sending')
  return turn
}

test('the screenshots and the link-preview card', async ({ browser }, testInfo) => {
  const baseURL = testInfo.project.use.baseURL
  mkdirSync(docsMedia, { recursive: true })

  // ---- the card a shared link shows: the landing page's own words, nothing that can go stale
  const card = await browser.newContext({ baseURL, viewport: { width: 800, height: 420 }, deviceScaleFactor: 1.5 })
  const cardPage = await card.newPage()
  await cardPage.goto('/')
  await expect(cardPage.getByTestId('landing-line')).toBeVisible()
  await cardPage.addStyleTag({
    content: `[data-testid="landing"] > :nth-child(n+3), [data-testid="landing"] header a { display: none !important }
              [data-testid="landing"] { min-height: 0 !important }`,
  })
  await cardPage.waitForTimeout(1500)
  await cardPage.screenshot({ path: join(publicDir, 'og.png') })
  await cardPage.screenshot({ path: join(docsMedia, 'social-preview.png'), clip: { x: 0, y: 0, width: 800, height: 400 } })
  await card.close()

  // ---- the landing page, whole
  const wide = await browser.newContext({ baseURL, viewport: { width: 1440, height: 900 } })
  const page = await wide.newPage()
  await page.goto('/')
  await expect(page.getByTestId('landing')).toBeVisible()
  await page.waitForTimeout(1500)
  await page.screenshot({ path: join(docsMedia, 'landing.png'), fullPage: true })

  // ---- find it: below 1440 px the Trail of a new turn is open in the chat itself
  const chat = await browser.newContext({ baseURL, viewport: { width: 1180, height: 800 } })
  const chatPage = await chat.newPage()
  await chatPage.goto('/')
  await chatPage.getByTestId('try-persona').click()
  await expect(chatPage.getByTestId('composer')).toBeVisible({ timeout: 30_000 })
  await chatPage.locator('[data-prompt="save"]').click()
  await chatPage.getByTestId('composer').press('Enter')
  await settled(chatPage)
  await chatPage.locator('[data-prompt="recall"]').click()
  await chatPage.getByTestId('composer').press('Enter')
  await settled(chatPage)
  await chatPage.waitForTimeout(1200)
  await chatPage.screenshot({ path: join(docsMedia, 'chat.png') })
  await chat.close()

  // ---- catch it: the glass box on the save, where the memory diff shows what was written
  await page.getByTestId('try-persona').click()
  await expect(page.getByTestId('composer')).toBeVisible({ timeout: 30_000 })
  await page.locator('[data-prompt="save"]').click()
  await page.getByTestId('composer').press('Enter')
  const save = await settled(page)
  await save.getByTestId('inspect').click()
  await expect(page.getByTestId('glass-box')).toBeVisible()
  await expect(page.getByTestId('panel-diff')).toBeVisible()
  await page.waitForTimeout(1200)
  await page.screenshot({ path: join(docsMedia, 'glass-box.png') })
  await wide.close()

  // ---- the phone
  const phone = await browser.newContext({
    baseURL,
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  })
  const small = await phone.newPage()
  await small.goto('/')
  await small.getByTestId('try-persona').click()
  await expect(small.getByTestId('composer')).toBeVisible({ timeout: 30_000 })
  await small.locator('[data-prompt="recall"]').click()
  await small.getByTestId('composer').press('Enter')
  await settled(small)
  await small.waitForTimeout(1200)
  await small.screenshot({ path: join(docsMedia, 'phone.png') })
  await phone.close()
})

test('the tour: frames of one first visit, for the GIF', async ({ browser }, testInfo) => {
  const baseURL = testInfo.project.use.baseURL
  rmSync(framesDir, { recursive: true, force: true })
  mkdirSync(framesDir, { recursive: true })
  const context = await browser.newContext({ baseURL, viewport: { width: 1180, height: 760 } })
  const page = await context.newPage()
  const frames: { file: string; at: number; hold: number | null }[] = []

  /** One frame. `hold` is how long it should stay on screen; without it, as long as the next took. */
  async function frame(hold: number | null = null) {
    const file = `${String(frames.length + 1).padStart(3, '0')}.png`
    const at = Date.now()
    await page.screenshot({ path: join(framesDir, file) })
    frames.push({ file, at, hold })
  }
  /** Frames at the pace the screen changes until the condition holds, then the last one, held. */
  async function until(done: () => Promise<boolean>, hold: number) {
    const started = Date.now()
    while (!(await done()) && Date.now() - started < 30_000) await frame()
    await page.waitForTimeout(250)
    await frame(hold)
  }
  const turns = page.getByTestId('turn')
  const finished = async () => /completed|failed/.test((await turns.last().getAttribute('data-status')) ?? '')

  await page.goto('/')
  await expect(page.getByTestId('landing')).toBeVisible()
  await page.waitForTimeout(1500)
  await frame(2200)
  await page.getByTestId('try-persona').click()
  await until(() => page.getByTestId('composer').isVisible(), 1800)

  await page.locator('[data-prompt="save"]').click()
  await page.waitForTimeout(300)
  await frame(1400)
  await page.getByTestId('composer').press('Enter')
  await until(finished, 2400)

  await page.locator('[data-prompt="recall"]').click()
  await page.waitForTimeout(300)
  await frame(1400)
  await page.getByTestId('composer').press('Enter')
  await until(finished, 3200)

  await turns.last().getByTestId('inspect').click()
  await expect(page.getByTestId('glass-box')).toBeVisible()
  await page.waitForTimeout(900)
  await frame(3600)
  writeFileSync(join(framesDir, 'frames.json'), JSON.stringify(frames, null, 1))
  await context.close()
})
