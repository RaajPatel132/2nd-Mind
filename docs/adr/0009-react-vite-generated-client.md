# ADR-0009: React + Vite + TypeScript + Tailwind, talking only to /v1 via a generated client

- **Status:** accepted
- **Date:** 2026-09-24

## Context

The UI is a thin client: chat plus the glass box. FR-17.1 says the web app uses the public
API with no back door, and the API contract must stay in sync with the UI.

## Decision

A Vite SPA (React 19, TypeScript 5.9 strict, Tailwind 4). `make gen-client` exports
`backend/openapi.json` and generates `src/api/schema.gen.ts` (openapi-typescript); calls go
through `openapi-fetch`, including the SSE stream (typed frames from the OpenAPI schema).
ESLint forbids `fetch`, `XMLHttpRequest` and `EventSource` outside `src/api/`. In production
the static build is served by unprivileged nginx, which proxies `/v1` (same origin, SSE
unbuffered).

## Alternatives considered

- **Next.js / SSR:** a second server runtime and a tempting back door to the database.
- **Hand-written fetch wrappers:** drift silently from the API.

## Consequences

A backend schema change fails CI until the snapshot and the generated client are updated
together. TypeScript is pinned to 5.9 until typescript-eslint and openapi-typescript support 6+.
