import { expect, test } from '@playwright/test'
import { setTier } from './admin.ts'
import { freshUser, send } from './helpers.ts'

type Turn = { id: string; models: Record<string, { model: string }> }

// ADR-0031: Auto is the default and sends no pick; a signed-in person may pick a model their
// plan offers, and it drives the turn; the list shows each model's price against Auto's.
test('Auto is the default; a pick drives the turn and is remembered', async ({ page }, testInfo) => {
  await freshUser(page, testInfo)
  const trigger = page.getByTestId('model-picker').getByRole('button')
  await expect(trigger).toHaveAccessibleName('Model: Auto')
  await expect(page.getByTestId('provider-mode')).toHaveCount(0)

  // Open by keyboard: Auto first and selected, then the models a signed-in person may pick.
  await trigger.focus()
  await page.keyboard.press('ArrowDown')
  const list = page.getByRole('listbox', { name: 'Model' })
  await expect(list).toBeVisible()
  await expect(list.getByRole('option', { name: /^Auto/ })).toHaveAttribute('aria-selected', 'true')
  await expect(list.getByRole('option', { name: /^Claude Sonnet 5/ })).toHaveCount(0) // premium only
  await expect(list.getByRole('option', { name: /^Claude Opus/ })).toHaveCount(0) // not offered at all
  await expect(list.getByRole('option', { name: /^GPT-6 Luna/ })).toContainText('Cheaper')
  const mini = list.getByRole('option', { name: /^GPT-5.4 mini/ })
  await expect(mini).toContainText("Auto's price")
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('ArrowDown')
  await expect(list).toHaveAttribute('aria-activedescendant', (await mini.getAttribute('id')) ?? '')
  await page.keyboard.press('Enter')
  await expect(list).toBeHidden()
  await expect(trigger).toBeFocused()
  await expect(trigger).toHaveAccessibleName(/^Model: GPT-5\.4 mini, [\d.]+× Auto's price$/)

  await send(page, 'what should I cook tonight?')
  const me = await page.request.get('/v1/me').then((r) => r.json() as Promise<{ workspaces: { id: string }[] }>)
  const ws = me.workspaces[0]?.id ?? ''
  const page1 = await page.request.get(`/v1/workspaces/${ws}/turns?limit=1`).then((r) => r.json() as Promise<{ items: Turn[] }>)
  expect(page1.items).toHaveLength(1)
  const turn = page1.items[0]
  // Every chat step ran on the pick; embeddings keep their own route (recall embeds the question).
  const chat = Object.entries(turn.models).filter(([step]) => step !== 'embed')
  expect(chat.length).toBeGreaterThan(0)
  expect(new Set(chat.map(([, m]) => m.model))).toEqual(new Set(['gpt-5.4-mini']))

  // The pick is remembered for this viewer, and Auto goes back to sending none.
  await page.reload()
  await expect(page.getByTestId('model-picker').getByRole('button')).toHaveAccessibleName(/^Model: GPT-5\.4 mini/)
  await page.getByTestId('model-picker').getByRole('button').click()
  await page.getByRole('option', { name: /^Auto/ }).click()
  await expect(page.getByTestId('model-picker').getByRole('button')).toHaveAccessibleName('Model: Auto')
})

test('an upgrade adds the dearer model to the list on the next turn', async ({ page }, testInfo) => {
  await freshUser(page, testInfo)
  const me = (await (await page.request.get('/v1/me')).json()) as { user: { email: string } }
  setTier(me.user.email, 'premium')
  await send(page, "I'm vegetarian")
  await page.reload()
  await page.getByTestId('model-picker').getByRole('button').click()
  await expect(page.getByRole('option', { name: /^Claude Sonnet 5/ })).toBeVisible()
})
