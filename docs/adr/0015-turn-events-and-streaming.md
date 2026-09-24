# ADR-0015: Typed turn events are the glass box's source of truth; replies stream over SSE

- **Status:** accepted
- **Date:** 2026-09-24

## Context

FR-9.2: the glass box renders from structured events the agent emits and persists, not from
traces. FR-1.5: answers stream. NFR-6.2: a provider outage gives a clean error, never a crash
or half-written state.

## Decision

- **Events:** a Pydantic discriminated union (`intent`, `decision`, `memory_diff`,
  `retrieval`, `tool_call`, `policy`, `model_call`, `error`), each versioned (`v`), persisted
  in `turn_events` with a per-turn `seq` assigned atomically. Each `model_call` event is
  written in the same transaction as its `usage_ledger` row.
- **Streaming:** `POST /v1/workspaces/{id}/turns` returns `text/event-stream` with frames
  `turn.started`, `token`*, then `turn.completed` or `turn.failed`, plus keep-alive comments.
  Frame schemas are in the OpenAPI snapshot, so the TS client is typed.
- **Runner:** the turn row is created before streaming, then the graph runs in its own
  task. A client that disconnects doesn't cancel the turn; it completes and appears on
  refresh. A failed turn stores an `error` event, a user-safe message and **no reply text**.

## Alternatives considered

- **WebSockets:** bidirectional capability we don't need; SSE works through proxies and
  plain HTTP tooling.
- **Glass box from Langfuse:** couples the product to the trace backend's availability.

## Consequences

Events are append-only history; changing an event's meaning requires a new `v`.
