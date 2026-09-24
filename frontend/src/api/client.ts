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
export type Turn = Schemas['TurnOut']
export type TurnPage = Schemas['TurnPage']
export type TurnEvent = Schemas['TurnEventOut']['event']
export type StoredEvent = Schemas['TurnEventOut']
export type ModelCallEvent = Schemas['ModelCallEvent']
export type IntentEvent = Schemas['IntentEvent']
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

/** Frames are produced by the typed server; the name picks the matching data shape. */
function toFrame(event: TurnStreamFrame['event'], data: unknown): TurnStreamFrame {
  return { event, data } as TurnStreamFrame
}

const FRAME_NAMES = new Set<TurnStreamFrame['event']>(['turn.started', 'token', 'turn.completed', 'turn.failed'])

/** Send a message; yields the typed server-sent frames of the turn as they arrive. */
export async function* streamTurn(
  workspaceId: string,
  message: string,
  signal?: AbortSignal,
): AsyncGenerator<TurnStreamFrame> {
  const { data, error, response } = await api.POST('/v1/workspaces/{workspace_id}/turns', {
    params: { path: { workspace_id: workspaceId } },
    body: { message },
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
