import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import * as client from '../api/client'
import type { Turn, Upcoming } from '../api/client'
import { ToastProvider } from '../ui'
import { UpcomingPage } from './UpcomingPage'

vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  getUpcoming: vi.fn(),
  editItem: vi.fn(),
  snoozeReminder: vi.fn(),
}))

const tap = { item_id: 'i1', state: 'open', title: 'Fix the kitchen tap' }
const upcoming = {
  timezone: 'UTC',
  start: '2026-10-02T00:00:00Z',
  end: '2026-10-09T00:00:00Z',
  days: [],
  undated: [tap],
  due_soon: 0,
  note: null,
} as Upcoming

/** Only the fields the page reads; the rest of a Turn is the server's business. */
function turn(fields: Partial<Pick<Turn, 'status' | 'output' | 'error'>>): Turn {
  return { id: 't1', status: 'completed', output: null, error: null, ...fields } as Turn
}

async function pressDone(result: Turn | Error) {
  vi.mocked(client.getUpcoming).mockResolvedValue(upcoming)
  if (result instanceof Error) vi.mocked(client.editItem).mockRejectedValue(result)
  else vi.mocked(client.editItem).mockResolvedValue(result)
  const onTurn = vi.fn()
  render(
    <ToastProvider>
      <UpcomingPage workspaceId="w1" timezone="UTC" onTurn={onTurn} />
    </ToastProvider>,
  )
  fireEvent.click(await screen.findByRole('button', { name: 'Done' }))
  return { onTurn, toast: () => screen.findByTestId('toast') }
}

describe("Upcoming's Done", () => {
  it('says it is done when the turn completed, and reloads the list', async () => {
    const { onTurn, toast } = await pressDone(turn({ output: "Edited: changed 'tap'." }))
    expect((await toast()).textContent).toContain('Marked “Fix the kitchen tap” done')
    expect(onTurn).toHaveBeenCalledTimes(1)
    await waitFor(() => {
      expect(client.getUpcoming).toHaveBeenCalledTimes(2)
    })
  })

  it('does not say it is done when the API stored the turn as failed', async () => {
    const failed = turn({
      status: 'failed',
      error: { code: 'internal_error', message: 'this turn was not saved' },
    })
    const { onTurn, toast } = await pressDone(failed)
    const text = (await toast()).textContent
    expect(text).toContain('this turn was not saved')
    expect(text).not.toContain('Marked')
    expect(onTurn).toHaveBeenCalledWith(failed) // the failed turn still shows in the chat
    expect(client.getUpcoming).toHaveBeenCalledTimes(1) // nothing changed, so no reload
  })

  it("shows a refusal's own words", async () => {
    const { toast } = await pressDone(new Error("That memory doesn't exist any more."))
    expect((await toast()).textContent).toBe("That memory doesn't exist any more.")
  })
})
