import { expect, test } from '@playwright/test'
import { freshUser, send } from './helpers.ts'

type Usage = { input_tokens: number; cached_input_tokens: number; output_tokens: number; charged_tokens: number }
type Turn = { id: string; models: Record<string, { model: string }>; usage: Usage }
type Events = { events: { event: { type: string; step?: string; usage?: Usage } }[] }

// ADR-0030: the model picker replaces the provider-mode tag; a pick drives the turn and its charge.
test('picking a model runs the turn on it and charges the quota at its weight', async ({ page }, testInfo) => {
  await freshUser(page, testInfo)
  const trigger = page.getByTestId('model-picker').getByRole('button')
  await expect(trigger).toHaveAccessibleName('Model: Claude Sonnet 5, 1× quota')
  await expect(page.getByTestId('provider-mode')).toHaveCount(0)

  // Open by keyboard, move to Opus 5 and pick it.
  await trigger.focus()
  await page.keyboard.press('ArrowDown')
  const list = page.getByRole('listbox', { name: 'Model' })
  await expect(list).toBeVisible()
  await expect(list.getByRole('group')).toHaveCount(2)
  await expect(list.getByRole('option', { name: /^Claude Sonnet 5(?![.\d])/ })).toHaveAttribute('aria-selected', 'true')
  const opus = list.getByRole('option', { name: /^Claude Opus 5(?![.\d])/ })
  await expect(opus).toContainText('Uses quota 2.5× as fast')
  await expect(list.getByRole('option', { name: /^GPT-6 Luna/ })).toContainText('95% less quota')
  await page.keyboard.press('ArrowUp')
  await expect(list).toHaveAttribute('aria-activedescendant', (await opus.getAttribute('id')) ?? '')
  await page.keyboard.press('Enter')
  await expect(list).toBeHidden()
  await expect(trigger).toBeFocused()
  await expect(trigger).toHaveAccessibleName('Model: Claude Opus 5, 2.5× quota')

  await send(page, 'what should I cook tonight?')
  const me = await page.request.get('/v1/me').then((r) => r.json() as Promise<{ workspaces: { id: string }[] }>)
  const ws = me.workspaces[0]?.id ?? ''
  const page1 = await page.request.get(`/v1/workspaces/${ws}/turns?limit=1`).then((r) => r.json() as Promise<{ items: Turn[] }>)
  expect(page1.items).toHaveLength(1)
  const turn = page1.items[0]
  // Every chat step ran on the pick; embeddings keep their own route (recall embeds the question).
  const chat = Object.entries(turn.models).filter(([step]) => step !== 'embed')
  expect(chat.length).toBeGreaterThan(0)
  expect(new Set(chat.map(([, m]) => m.model))).toEqual(new Set(['claude-opus-5']))
  const events = await page.request.get(`/v1/turns/${turn.id}/events`).then((r) => r.json() as Promise<Events>)
  const calls = events.events.map((e) => e.event).filter((e) => e.type === 'model_call' && e.step !== 'embed')
  const raw = calls.reduce((n, c) => n + (c.usage ? c.usage.input_tokens + c.usage.cached_input_tokens + c.usage.output_tokens : 0), 0)
  const charged = calls.reduce((n, c) => n + (c.usage?.charged_tokens ?? 0), 0)
  // Each call rounds on its own, so the chat calls' charge is within a token per call of 2.5 × raw.
  expect(Math.abs(charged - raw * 2.5)).toBeLessThanOrEqual(calls.length)
  const usage = await page.request.get('/v1/me/usage').then((r) => r.json() as Promise<{ used_tokens: number }>)
  expect(usage.used_tokens).toBe(turn.usage.charged_tokens)

  // The pick is remembered for this viewer.
  await page.reload()
  await expect(page.getByTestId('model-picker').getByRole('button')).toHaveAccessibleName('Model: Claude Opus 5, 2.5× quota')
})
