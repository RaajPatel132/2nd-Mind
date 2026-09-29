import { expect, test, type Page, type Route } from '@playwright/test'
import { freshUser, send } from './helpers.ts'

/**
 * R.8: the conversation's history behaviours, on a history the test controls. The turn list is
 * the real API's response, reshaped in flight (more turns, or turns marked as housekeeping), so
 * the UI's paging and grouping are tested without seeding dozens of turns through the models.
 */

type TurnJson = Record<string, unknown> & { id: string; input: string; kind: string }
type Page20 = { items: TurnJson[]; next_before: string | null }

async function realTurns(page: Page): Promise<TurnJson[]> {
  const me = (await (await page.request.get('/v1/me')).json()) as { workspaces: { id: string }[] }
  const ws = me.workspaces[0]?.id ?? ''
  const body = (await (await page.request.get(`/v1/workspaces/${ws}/turns?limit=20`)).json()) as Page20
  return body.items
}

function clone(turn: TurnJson, n: number): TurnJson {
  // Ids sort by time (UUIDv7); a fixed-width counter keeps the synthetic ones in order.
  const suffix = n.toString(16).padStart(12, '0')
  return { ...turn, id: `${turn.id.slice(0, 24)}${suffix}`, input: `Synthetic message ${String(n)}` }
}

test.describe('conversation history', () => {
  test('loading earlier turns keeps the first visible turn where it was', async ({ page }, testInfo) => {
    await freshUser(page, testInfo)
    await send(page, 'I live in Bengaluru')
    const template = (await realTurns(page)).at(0)
    if (!template) throw new Error('the real turn list is empty')
    // 45 turns, newest first: pages of 20 with next_before, like the API.
    const all = Array.from({ length: 45 }, (_, i) => clone(template, 45 - i))
    await page.route('**/v1/workspaces/*/turns?*', async (route: Route) => {
      const url = new URL(route.request().url())
      if (route.request().method() !== 'GET') return route.fallback()
      const before = url.searchParams.get('before')
      const limit = Number(url.searchParams.get('limit') ?? '20')
      const start = before ? all.findIndex((t) => t.id === before) + 1 : 0
      const items = all.slice(start, start + limit)
      const more = start + limit < all.length
      await route.fulfill({ json: { items, next_before: more ? (items.at(-1)?.id ?? null) : null } })
    })
    await page.route('**/v1/turns/*/events', (route) => route.fulfill({ json: { turn_id: '', events: [] } }))
    await page.reload()
    const turns = page.getByTestId('messages').getByTestId('turn')
    await expect(turns).toHaveCount(20)

    await page.evaluate(() => {
      window.scrollTo({ top: 0 })
    })
    const firstBefore = turns.first()
    const text = (await firstBefore.locator('p').nth(1).textContent()) ?? ''
    const topBefore = (await firstBefore.boundingBox())?.y ?? 0

    await page.getByRole('button', { name: 'Load earlier messages' }).click()
    await expect(turns).toHaveCount(40)
    const same = page.getByTestId('turn').filter({ hasText: text }).first()
    const topAfter = (await same.boundingBox())?.y ?? -1
    // The turn the reader was looking at stays put (within a few pixels of layout rounding).
    expect(Math.abs(topAfter - topBefore)).toBeLessThan(4)
  })

  test('a run of housekeeping turns folds into one row that expands', async ({ page }, testInfo) => {
    await freshUser(page, testInfo)
    await send(page, 'I live in Bengaluru')
    await send(page, "I'm vegetarian")
    await send(page, 'I moved to Pune')
    const real = await realTurns(page)
    // The two oldest become housekeeping turns; the newest stays a message.
    const reshaped = real.map((t, i) => (i === 0 ? t : { ...t, kind: 'system', input: 'Tidy the quick layer' }))
    await page.route('**/v1/workspaces/*/turns?*', (route) =>
      route.request().method() === 'GET' ? route.fulfill({ json: { items: reshaped, next_before: null } }) : route.fallback(),
    )
    await page.reload()
    const group = page.getByTestId('housekeeping-group')
    await expect(group).toHaveCount(1)
    await expect(group).toContainText('2 housekeeping changes')
    const toggle = group.getByTestId('housekeeping-toggle')
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await toggle.press('Enter')
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await expect(group.getByTestId('system-note')).toHaveCount(2)
    await expect(page.getByTestId('messages').getByTestId('turn')).toHaveCount(1)
  })

  test('first-run suggestions end with a recall that answers from what was saved', async ({ page }, testInfo) => {
    await freshUser(page, testInfo)
    const cards = page.getByTestId('suggestion')
    await expect(cards).toHaveCount(3)
    await expect(cards.nth(2)).toContainText('Where do I live?')
    await send(page, 'I live in Bengaluru')
    await send(page, 'I moved to Pune')
    await send(page, 'Where do I live?', /Pune/)
  })
})
