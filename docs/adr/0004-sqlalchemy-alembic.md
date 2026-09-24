# ADR-0004: SQLAlchemy 2 (async, asyncpg) and Alembic

- **Status:** accepted
- **Date:** 2026-09-24

## Context

We need typed persistence in async code, versioned migrations that run automatically on
start-up and deploy (NFR-10.4), and raw SQL where Postgres features matter (RLS, pgvector,
full-text).

## Decision

SQLAlchemy 2 with the asyncpg driver, typed `Mapped[...]` models living only in
`*/adapters/tables.py`, and Alembic migrations written by hand (RLS policies and grants are
DDL that autogenerate can't express). An integration test runs `alembic check` so models and
migrations never drift. Migrations run as a one-shot `migrate` service before `api` and
`worker` start, and via `make migrate`.

## Alternatives considered

- **Raw asyncpg + hand SQL:** fastest, but no typed models and more boilerplate.
- **SQLModel / Tortoise:** thinner communities; SQLModel mixes API and table models, which
  our boundaries keep apart on purpose.
- **Migrations on API start-up:** races when several replicas start together.

## Consequences

Models are infrastructure detail, never imported by domain code (import-linter enforces it).
Hand-written migrations cost a little time but are reviewable security artefacts.
