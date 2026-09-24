# ADR-0007: Redis with arq for background jobs (and rate limits later)

- **Status:** accepted
- **Date:** 2026-09-24

## Context

Link enrichment, embeddings and expiry run in the background (NFR-6.3); S4 needs per-identity
rate limits. We want one small dependency covering both.

## Decision

Redis 7 with `arq` (asyncio-native job queue). The `worker` runs the same image as `api` with
a different command; `arq --check` is its health check. S1 ships a no-op `heartbeat` job and an
integration test that enqueues it and sees it complete. The local stack shares one Redis
server with Langfuse (app on DB 1, Langfuse on DB 0).

## Alternatives considered

- **Celery:** heavier, sync-first, more moving parts.
- **Postgres-backed queue (e.g. procrastinate):** one fewer store, but we want Redis anyway
  for rate limits and it keeps queue load off the primary database.

## Consequences

Jobs are async functions in `jobs/`; only `jobs/adapters/` imports arq. arq is small and
maintained slowly; if it stalls, the queue client is the only code to replace.
