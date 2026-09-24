# ADR-0008: Self-hosted Langfuse for traces; the trace id is the turn id

- **Status:** accepted
- **Date:** 2026-09-24

## Context

Every turn must be traced with per-call tokens, cost and latency (NFR-5.1), linked from the
glass box (FR-9.4), and share an id with our events and logs (NFR-5.2, NFR-5.4). The glass
box itself must keep working when the trace backend is down (FR-9.2).

## Decision

Langfuse v4, self-hosted in compose (web, worker, ClickHouse, MinIO; it reuses the stack's
Postgres and Redis). A headless init seeds the org, project and API keys, so tracing works on
first start. The Python SDK (OpenTelemetry-based) starts one `turn` observation per turn with
trace id = turn id (UUIDv7 hex), and one `generation` per model call carrying model, token
usage, cost and time to first token. Content is sent only when `TRACE_INCLUDE_CONTENT=true`.

Our own `model_call` events are the glass box's source of truth; the trace is a link. At turn
end we record `trace_status` (`recorded`/`unavailable`/`disabled`) from a cached health
probe; the UI shows the link only when recorded.

## Alternatives considered

- **OTel collector + Jaeger/Tempo:** generic spans, no LLM-specific views (cost, generations).
- **Hosted Langfuse:** simpler, but sends traces off-box; revisit for production (S5).

## Consequences

The local stack needs about 2–3 GB more memory (ClickHouse, Langfuse). Export is async,
so an outage costs traces, never turns.
