# ADR-0001: Python 3.12, FastAPI, Pydantic v2 and uv for the backend

- **Status:** accepted
- **Date:** 2026-09-24

## Context

The backend is an agent service: typed model I/O, streaming responses, many small async
calls to model providers and stores. The team's home stack is Python. NFR-9.4 asks for types
and config validation throughout.

## Decision

Python 3.12 with FastAPI (async, OpenAPI from code) and Pydantic v2 for every boundary type
(settings, API schemas, events, structured model output). `uv` manages the project, with
`uv.lock` committed. Strict typing is enforced: `mypy --strict` and `ruff` in `make check`.

## Alternatives considered

- **Node/TypeScript backend:** one language across the stack, but a weaker ecosystem for
  evals, data work and the Phase 2 training ladder.
- **Django:** batteries we don't need (templates, admin, ORM migrations style); async is
  secondary.
- **Poetry / pip-tools:** slower, and uv also installs Python itself in CI.

## Consequences

OpenAPI comes for free and drives the TS client. Pydantic models are shared by config, API and
events, so validation rules live in one place. We pin to 3.12 until our dependencies all
support 3.13; bumping is a one-line change plus a CI run.
