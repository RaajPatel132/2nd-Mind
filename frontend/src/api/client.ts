/**
 * The only way the SPA talks to the backend: the client generated from backend/openapi.json.
 * ESLint forbids fetch/EventSource anywhere outside src/api (S1.12, FR-17.1).
 */
import createClient from 'openapi-fetch'
import { EventSourceParserStream } from 'eventsource-parser/stream'
import type { components, paths } from './schema.gen'

export type Schemas = components['schemas']
export type Me = Schemas['MeOut']
export type Meta = Schemas['MetaOut']
export type Picker = Schemas['PickerOut']
export type ModelChoice = Schemas['ModelChoiceOut']
export type Turn = Schemas['TurnOut']
export type TurnPage = Schemas['TurnPage']
export type TurnEvent = Schemas['TurnEventOut']['event']
export type StoredEvent = Schemas['TurnEventOut']
export type ModelCallEvent = Schemas['ModelCallEvent']
export type IntentEvent = Schemas['IntentEvent']
export type DecisionEvent = Schemas['DecisionEvent']
export type MemoryDiffEvent = Schemas['MemoryDiffEvent']
export type ToolCallEvent = Schemas['ToolCallEvent']
export type PolicyEvent = Schemas['PolicyEvent']
export type StepEvent = Schemas['StepEvent']
export type ErrorEvent = Schemas['ErrorEvent']
export type RetrievalEvent = Schemas['RetrievalEvent']
export type CitationsEvent = Schemas['CitationsEvent']
export type Citation = Schemas['Citation']
export type SubQueryTrace = Schemas['SubQueryTrace']
export type RetrievalCandidate = Schemas['RetrievalCandidate']
export type Upcoming = Schemas['UpcomingOut']
export type UpcomingEntry = Schemas['UpcomingEntryOut']
export type ItemEdit = Schemas['ItemEditIn']
export type AgentStep = Schemas['AgentStep']
export type Usage = Schemas['UsageOut']
export type DiffEntry = Schemas['DiffEntry']
export type EntityResolution = Schemas['EntityResolution']
export type Reconciliation = Schemas['Reconciliation']
export type TimeResolution = Schemas['TimeResolution']
export type Classification = Schemas['Classification']
export type HeldWrite = Schemas['HeldWriteOut']
export type ItemDetail = Schemas['ItemDetailOut']
export type EntityDetail = Schemas['EntityDetailOut']
export type Entity = Schemas['EntityRecord']
export type TurnStreamFrame = Schemas['TurnStreamFrame']

const api = createClient<paths>({ baseUrl: '', credentials: 'same-origin' })

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.data === undefined) throw toError(result.response.status, result.error)
  return result.data
}

function toError(status: number, body: unknown): ApiError {
  const err = (body as { error?: { code?: string; message?: string } } | undefined)?.error
  return new ApiError(status, err?.code ?? 'http_error', err?.message ?? `Request failed (${String(status)})`)
}

export async function getMe(): Promise<Me | null> {
  const { data, error, response } = await api.GET('/v1/me')
  if (response.status === 401) return null
  if (error !== undefined) throw toError(response.status, error)
  return data
}

export async function devLogin(): Promise<Me> {
  return unwrap(await api.POST('/v1/auth/dev-login'))
}

export async function logout(): Promise<void> {
  const { response } = await api.POST('/v1/auth/logout')
  if (!response.ok) throw toError(response.status, undefined)
}

/** The signed-in user's quota: tier, limit, used and remaining tokens (read only until S4). */
export async function getUsage(): Promise<Usage> {
  return unwrap(await api.GET('/v1/me/usage'))
}

export async function getMeta(): Promise<Meta> {
  return unwrap(await api.GET('/v1/meta'))
}

export async function listTurns(workspaceId: string, before?: string, limit = 30): Promise<TurnPage> {
  return unwrap(
    await api.GET('/v1/workspaces/{workspace_id}/turns', {
      params: { path: { workspace_id: workspaceId }, query: { limit, before: before ?? null } },
    }),
  )
}

export async function getTurnEvents(turnId: string): Promise<StoredEvent[]> {
  const page = unwrap(await api.GET('/v1/turns/{turn_id}/events', { params: { path: { turn_id: turnId } } }))
  return page.events
}

/** Revert every memory write of a turn, as a new undo turn (undoing an undo is a redo). */
export async function undoTurn(turnId: string): Promise<Turn> {
  return unwrap(await api.POST('/v1/turns/{turn_id}/undo', { params: { path: { turn_id: turnId } } }))
}

export async function listHeldWrites(workspaceId: string): Promise<HeldWrite[]> {
  const page = unwrap(
    await api.GET('/v1/workspaces/{workspace_id}/held-writes', {
      params: { path: { workspace_id: workspaceId }, query: {} },
    }),
  )
  return page.items
}

/** Apply a held write; it runs as a new confirmation turn with its own diff. */
export async function confirmHeldWrite(heldId: string): Promise<Turn> {
  return unwrap(await api.POST('/v1/held-writes/{held_id}/confirm', { params: { path: { held_id: heldId } } }))
}

export async function rejectHeldWrite(heldId: string): Promise<HeldWrite> {
  return unwrap(await api.POST('/v1/held-writes/{held_id}/reject', { params: { path: { held_id: heldId } } }))
}

export async function getItem(itemId: string): Promise<ItemDetail> {
  return unwrap(await api.GET('/v1/items/{item_id}', { params: { path: { item_id: itemId } } }))
}

/** The workspace's people, places and things: what the editor can link a memory to (S3.12). */
export async function listEntities(workspaceId: string): Promise<Entity[]> {
  const out = unwrap(
    await api.GET('/v1/workspaces/{workspace_id}/entities', { params: { path: { workspace_id: workspaceId } } }),
  )
  return out.items
}

/** Edit one memory in place; it runs as its own undoable turn (S3.12). */
export async function editItem(itemId: string, body: ItemEdit): Promise<Turn> {
  return unwrap(await api.PATCH('/v1/items/{item_id}', { params: { path: { item_id: itemId } }, body }))
}

/** Move a pending reminder to a new time; the memory's own date stays (S3.14). */
export async function snoozeReminder(triggerId: string, dateExpression: string): Promise<Turn> {
  return unwrap(
    await api.POST('/v1/triggers/{trigger_id}/snooze', {
      params: { path: { trigger_id: triggerId } },
      body: { date_expression: dateExpression },
    }),
  )
}

/** What's ahead, grouped by local day, with undated open tasks and the due-soon note (S3.14). */
export async function getUpcoming(workspaceId: string, days?: number): Promise<Upcoming> {
  return unwrap(
    await api.GET('/v1/workspaces/{workspace_id}/upcoming', {
      params: { path: { workspace_id: workspaceId }, query: days ? { days } : {} },
    }),
  )
}

export async function getEntity(entityId: string): Promise<EntityDetail> {
  return unwrap(await api.GET('/v1/entities/{entity_id}', { params: { path: { entity_id: entityId } } }))
}

/** Frames are produced by the typed server; the name picks the matching data shape. */
function toFrame(event: TurnStreamFrame['event'], data: unknown): TurnStreamFrame {
  return { event, data } as TurnStreamFrame
}

const FRAME_NAMES = new Set<TurnStreamFrame['event']>([
  'turn.started',
  'token',
  'step.started',
  'turn.event',
  'turn.completed',
  'turn.failed',
])

/**
 * Send a message; yields the typed server-sent frames of the turn as they arrive. `model` is a
 * picker choice for every chat step of the turn; null keeps the configured routing.
 */
export async function* streamTurn(
  workspaceId: string,
  message: string,
  model: string | null = null,
  signal?: AbortSignal,
): AsyncGenerator<TurnStreamFrame> {
  const { data, error, response } = await api.POST('/v1/workspaces/{workspace_id}/turns', {
    params: { path: { workspace_id: workspaceId } },
    body: { message, model },
    parseAs: 'stream',
    signal,
  })
  if (error !== undefined || !response.ok) throw toError(response.status, error)
  if (!data) throw new ApiError(response.status, 'empty_stream', 'The server sent no stream.')
  const frames = data.pipeThrough(new TextDecoderStream()).pipeThrough(new EventSourceParserStream())
  const reader = frames.getReader()
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) return
      const name = value.event as TurnStreamFrame['event'] | undefined
      if (name && FRAME_NAMES.has(name)) yield toFrame(name, JSON.parse(value.data) as unknown)
    }
  } finally {
    reader.releaseLock()
  }
}
