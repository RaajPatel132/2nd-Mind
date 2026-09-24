# ADR-0010: pytest, testcontainers and Playwright for the test pyramid

- **Status:** accepted
- **Date:** 2026-09-24

## Context

NFR-9.5: unit tests per module, integration tests against real Postgres and a real queue,
provider contract tests, and browser E2E for key flows, with deterministic model doubles.

## Decision

- **Unit** (`pytest`, default): in-memory stores and the fake provider; no network.
- **Integration** (`pytest -m integration`): testcontainers `pgvector/pgvector:pg16` and
  `redis:7`, migrations applied as the owner, tests run as the RLS-bound app role.
- **Contract** (`tests/unit/providers/test_contract.py`): one suite parametrised over
  providers; real ones only with `-m live`.
- **E2E** (Playwright): the compose stack in fake-provider mode, desktop and 360 px.

## Alternatives considered

- **SQLite or mocks for DB tests:** can't prove RLS.
- **Cypress:** fine, but Playwright's multi-viewport projects and trace viewer fit better.

## Consequences

Integration tests need Docker (CI runners have it). The test helper finds colima or Docker
Desktop sockets via the active Docker context.
