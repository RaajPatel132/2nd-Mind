import { expect, test } from '@playwright/test'
import { freshUser, inspect, openStep, send } from './helpers.ts'

// S2.11 / S2.12 / S2.3 on the compose stack (fake provider replaying the golden ingest cases).
// Since UI.8 the glass box lives in each turn's Trail; UI.11 puts the whole of it in the
// Inspector. The assertions are the S2 ones, run against step details and the Inspector.

test.describe('saving memories', () => {
  test.describe.configure({ timeout: 180_000 })

  test('§8.1 (b): three memories and a new person, in the Trail and the Inspector', async ({ page }, testInfo) => {
    await freshUser(page, testInfo)
    const turn = await send(
      page,
      'My wife liked this bag from MK, serial no. 123ABC. We can gift it on her birthday next May.',
      "I've assumed 'My wife' is someone new",
    )

    // In the Trail: each step's technical layer.
    await expect((await openStep(turn, 'understand')).getByTestId('decision-intent')).toContainText('Intent save')
    await expect((await openStep(turn, 'extract')).getByTestId('decision-memory')).toHaveCount(3)
    const entities = await openStep(turn, 'entities')
    await expect(entities.locator('[data-testid="decision-entity"][data-outcome="new"]', { hasText: 'wife' })).toHaveCount(1)
    await expect((await openStep(turn, 'dates')).getByTestId('decision-date').first()).toContainText('next May')
    const save = await openStep(turn, 'save')
    const added = save.locator('[data-testid="diff-entry"][data-op="added"]')
    await expect(added).toHaveCount(5) // wife, the MK bag, and the three memories
    await expect(added.filter({ hasText: 'Wife likes MK bags' })).toContainText('+')
    await expect((await openStep(turn, 'guard')).getByTestId('tool-call-policy').first()).toHaveText('allowed · P-DEFAULT')

    // In the Inspector: the same facts in the five panels.
    const glassBox = await inspect(page, turn)
    const decision = glassBox.getByTestId('panel-decision')
    await expect(decision).toHaveAttribute('data-open', 'true')
    await expect(decision.getByTestId('decision-intent')).toContainText('Intent save')
    await expect(decision.getByTestId('decision-memory')).toHaveCount(3)
    await expect(decision.locator('[data-testid="decision-entity"][data-outcome="new"]', { hasText: 'wife' })).toHaveCount(1)
    await expect(decision.getByTestId('decision-date').first()).toContainText('next May')
    const diff = glassBox.getByTestId('panel-diff')
    await expect(diff).toHaveAttribute('data-open', 'true')
    await expect(diff.locator('[data-testid="diff-entry"][data-op="added"]')).toHaveCount(5)
    const tools = glassBox.getByTestId('panel-tools')
    await expect(tools.getByTestId('tool-call-policy').first()).toHaveText('allowed · P-DEFAULT')
    await expect(glassBox.getByText('A pure save: nothing was looked up.').first()).toBeVisible() // collapsed, with its reason
  })

  test('moving city supersedes the old fact, and undo brings it back', async ({ page }, testInfo) => {
    await freshUser(page, testInfo)
    await send(page, 'I live in Bengaluru', 'Bengaluru')
    let turn = await send(page, 'I moved to Pune', 'Updated: you live in Pune now')
    await expect(turn.locator('[data-testid="trail-step"][data-step="reconcile"]').getByTestId('step-label')).toHaveText('Replaces 1 memory')

    const superseded = (await openStep(turn, 'save')).locator('[data-testid="diff-entry"][data-op="superseded"]')
    await expect(superseded).toHaveCount(1)
    await expect(superseded).toContainText('I live in Bengaluru')
    await expect(superseded).toContainText('valid_to')

    // The Trail renders only from stored events: a reload shows the same diff.
    await page.reload()
    turn = page.getByTestId('turn').last()
    await expect((await openStep(turn, 'save')).locator('[data-testid="diff-entry"][data-op="superseded"]')).toContainText('I live in Bengaluru')

    // The turn changed more than one memory, so undo asks first.
    await turn.getByTestId('undo-turn').click()
    await turn.getByTestId('undo-confirm').click()
    const note = page.locator('[data-testid="system-note"][data-kind="undo"]')
    await expect(note).toContainText('Undone')
    const undo = await openStep(note, 'undo')
    await expect(undo).toContainText('Undoes turn')
    await expect(undo.locator('[data-testid="diff-entry"][data-op="removed"]').first()).toBeVisible()
  })

  test('a sensitive core write is held until confirmed', async ({ page }, testInfo) => {
    await freshUser(page, testInfo)
    const turn = await send(page, "I've been seeing a therapist for anxiety since March", 'Held for your confirmation')

    // The guard step is held, amber and open by itself, with Confirm and Reject inline.
    const guard = turn.locator('[data-testid="trail-step"][data-step="guard"]')
    await expect(guard).toHaveAttribute('data-state', 'held')
    await expect(guard.locator('button[data-step-row]')).toHaveAttribute('aria-expanded', 'true')
    const held = guard.locator('[data-testid="diff-entry"][data-op="held"]')
    await expect(held).toContainText('P-SENS-1')
    await held.getByTestId('held-confirm').click()
    await expect(page.locator('[data-testid="system-note"][data-kind="confirm"]')).toContainText('Confirmed')
  })

  test('a secret is refused and never shown back', async ({ page }, testInfo) => {
    await freshUser(page, testInfo)
    const turn = await send(page, 'Remember my wifi password is hunter2', "I didn't save that")
    const guard = turn.locator('[data-testid="trail-step"][data-step="guard"]')
    await expect(guard).toHaveAttribute('data-state', 'refused')
    await expect(guard.getByTestId('step-label')).toHaveText('Refused: that looks like a password')
    await expect(turn.locator('[data-testid="trail-step"][data-step="save"]')).toHaveCount(0)
    await expect(turn.getByTestId('undo-turn')).toHaveCount(0) // the receipt shows no save
    await page.reload()
    await expect(page.getByText('hunter2')).toHaveCount(0)
  })
})
